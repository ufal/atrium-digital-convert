"""
api_util/office_render.py — an office document rendered to PDF for its pages (atrium-digital-convert#9).

A DOCX, DOC, ODT or RTF has no pages until something lays it out. digital-convert keeps reading
its text with the native readers (`digital_docx`, the shared reader for ODT and RTF) and takes its
pages from a rendering: headless LibreOffice prints the document to PDF, the light PDF engine reads
that PDF's pages and words, and `api_util/render_place.py` places the native text on them. The
rendered PDF is also what the visual steps receive for a page that needs the ATR: page-classification
reads PDFs only, and the ATR's page numbers and boxes must be the docproc's (H2, D28). It is an
intermediate: not stored, named by its SHA-512 in the paradata (D23).

What is rendered is a working copy that reads as the native text does:

  * **tracked changes accepted** — LibreOffice draws a tracked deletion (struck through) and the
    native readers drop it (`digital_docx` G5; the shared reader skips `text:tracked-changes`). A
    DOCX copy drops `w:del`/`w:moveFrom` and unwraps `w:ins`/`w:moveTo` in the main, header,
    footer and note parts; an ODT copy drops `text:tracked-changes` and the change marks. An RTF
    is rendered as it is: a page where its changes show is one whose rendered words the native
    text does not cover, and goes to the ATR (`render_place`), so nothing is lost;
  * **the package repaired** — a DOCX whose main part has no WordprocessingML content type is one
    LibreOffice will not load ("source file could not be loaded"); the copy carries the Override
    the native reader adds in memory (`digital_docx.repaired_package`, G4).

The PDF export options are pinned (`PDF_EXPORT_OPTIONS`): comments are not printed, images keep
their resolution (the ATR and page-classification read them), automatically inserted blank pages
are kept (they are routed `empty`). Hyphenation dictionaries are not installed in the image, so
LibreOffice never hyphenates a word the native text has whole.

The PDF is made reproducible (`stable_pdf`): LibreOffice writes the time of the run
(`/CreationDate`) and a random `/ID` and `/DocChecksum` into it, so two renderings of one file
would differ in those bytes alone. They are overwritten in place, with values of the same length
(a fixed date; digests of the rendered copy), so the cross-reference offsets stay valid and a
rerun with the same image gives the same SHA-512 — the digest the paradata name the PDF by.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from typing import Any, Dict, Tuple

from api_util.digital_ir import DigitalInputError, sha512_file

#: The kinds read as native text on rendered pages (the visual workflow's office files).
RENDERED_KINDS: Tuple[str, ...] = ("docx", "doc", "odt", "rtf")

#: `writer_pdf_Export` filter options, pinned (LibreOffice >= 7.4 takes them as JSON).
PDF_EXPORT_OPTIONS: Dict[str, Dict[str, str]] = {
    "ExportNotes": {"type": "boolean", "value": "false"},
    "ExportNotesInMargin": {"type": "boolean", "value": "false"},
    "ReduceImageResolution": {"type": "boolean", "value": "false"},
    "IsSkipEmptyPages": {"type": "boolean", "value": "false"},
    "ExportFormFields": {"type": "boolean", "value": "false"},
}

#: The `--convert-to` argument the rendering runs with; recorded as the run's `render` parameter.
CONVERT_TO = "pdf:writer_pdf_Export:" + json.dumps(PDF_EXPORT_OPTIONS, separators=(",", ":"))

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

#: DOCX parts whose tracked changes are accepted in the copy (the main part is added by name).
_DOCX_STORY_PREFIXES = ("word/header", "word/footer", "word/footnotes", "word/endnotes")

#: Tracked-change wrappers: removed with their content, or replaced by their content.
_W_DROP = frozenset({"del", "moveFrom"})
_W_UNWRAP = frozenset({"ins", "moveTo"})
_ODF_DROP = frozenset({"tracked-changes", "change", "change-start", "change-end"})


@functools.lru_cache(maxsize=4)
def _version_of(binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    words = (proc.stdout or "").split()
    # "LibreOffice 24.2.7.2 420(Build:2)" -> "24.2.7.2"
    return words[1] if len(words) > 1 and words[0].lower().startswith("libreoffice") else ""


def libreoffice_version() -> str:
    """The installed LibreOffice's version (`soffice --version`), or '' when unknown."""
    from api_util.digital_legacy import libreoffice_binary  # noqa: PLC0415

    binary = libreoffice_binary()
    return _version_of(binary) if binary else ""


def _local(tag: Any) -> str:
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _accept_changes(root: Any, namespace: str, drop: frozenset, unwrap: frozenset) -> int:
    """Accept every tracked change in `root`, in place; return how many elements changed."""
    changed = 0
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or not el.tag.startswith("{" + namespace + "}"):
            continue
        name = _local(el.tag)
        parent = el.getparent()
        if parent is None:
            continue
        if name in drop:
            # Keep the tail: it is the parent's text that follows the element.
            if el.tail:
                previous = el.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + el.tail
                else:
                    parent.text = (parent.text or "") + el.tail
            parent.remove(el)
            changed += 1
        elif name in unwrap:
            index = parent.index(el)
            children = list(el)
            for offset, child in enumerate(children):
                parent.insert(index + offset, child)
            if el.tail:
                if children:
                    children[-1].tail = (children[-1].tail or "") + el.tail
                elif index > 0:
                    before = parent[index - 1]
                    before.tail = (before.tail or "") + el.tail
                else:
                    parent.text = (parent.text or "") + el.tail
            parent.remove(el)
            changed += 1
    return changed


def _serialize(root: Any) -> bytes:
    from lxml import etree  # noqa: PLC0415  (a dependency of the readers)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _docx_copy(path: str, target: str) -> None:
    """The DOCX with its changes accepted and its main content type repaired (see the module)."""
    from api_util import digital_docx  # noqa: PLC0415

    digital_docx._check_zip(path)
    with zipfile.ZipFile(path) as zf:
        main = digital_docx._main_part_name(zf)
        types = digital_docx._parse_xml(zf.read("[Content_Types].xml"))
        overrides = [o for o in types if _local(o.tag) == "Override"]
        main_type = next(
            (
                o.get("ContentType")
                for o in overrides
                if o.get("PartName", "").lower() == "/" + main.lower()
            ),
            None,
        )
        patched_types = None
        if main_type != digital_docx.WML_MAIN:
            patched_types = digital_docx.repaired_package(path)
        stories = {main} | {
            n for n in zf.namelist() if n.startswith(_DOCX_STORY_PREFIXES) and n.endswith(".xml")
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            source = zipfile.ZipFile(io.BytesIO(patched_types)) if patched_types else zf
            try:
                for info in source.infolist():
                    data = source.read(info)
                    if info.filename in stories:
                        root = digital_docx._parse_xml(data)
                        if _accept_changes(root, _W_NS, _W_DROP, _W_UNWRAP):
                            data = _serialize(root)
                    dst.writestr(info, data)
            finally:
                if patched_types:
                    source.close()
    with open(target, "wb") as handle:
        handle.write(out.getvalue())


def _odt_copy(path: str, target: str) -> None:
    """The ODT with its changes accepted: no `text:tracked-changes`, no change marks."""
    from api_util.digital_docx import _parse_xml  # noqa: PLC0415  (the hardened parser)

    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DigitalInputError("corrupt", f"not a readable ODF package ({exc})") from exc
    with zf:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dst:
            for info in zf.infolist():
                data = zf.read(info)
                if info.filename in ("content.xml", "styles.xml"):
                    root = _parse_xml(data)
                    if _accept_changes(root, _TEXT_NS, _ODF_DROP, frozenset()):
                        data = _serialize(root)
                # `mimetype` stays the first, stored entry (ODF packaging).
                compress = (
                    zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                )
                dst.writestr(info, data, compress_type=compress)
    with open(target, "wb") as handle:
        handle.write(out.getvalue())


def prepared_copy(path: str, kind: str, workdir: str) -> str:
    """The working copy LibreOffice renders: changes accepted, the package repaired (module doc)."""
    if kind not in ("docx", "odt", "rtf"):
        raise ValueError(
            f"office_render prepares DOCX, ODT or RTF, not {kind!r} (a DOC goes as its DOCX)"
        )
    target = os.path.join(workdir, f"input.{kind}")
    if kind == "docx":
        _docx_copy(path, target)
    elif kind == "odt":
        _odt_copy(path, target)
    else:
        shutil.copyfile(path, target)
    return target


_PDF_DATE = re.compile(rb"/(CreationDate|ModDate)\s*\(D:(\d{4,14})")
_PDF_ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\]")
_PDF_CHECKSUM = re.compile(rb"/DocChecksum\s*/([0-9A-Fa-f]+)")
_FIXED_DATE = b"19800101000000"


def stable_pdf(data: bytes, seed: bytes) -> bytes:
    """The PDF with its run-specific bytes overwritten, length for length (the module docstring)."""
    digest = hashlib.sha512(seed).hexdigest().upper().encode("ascii")

    def date(match: "re.Match[bytes]") -> bytes:
        stamp = match.group(2)
        return match.group(0)[: -len(stamp)] + _FIXED_DATE[: len(stamp)]

    def ident(match: "re.Match[bytes]") -> bytes:
        out = match.group(0)
        for group in (1, 2):
            value = match.group(group)
            out = out.replace(value, digest[: len(value)], 1) if len(value) <= len(digest) else out
        return out

    def checksum(match: "re.Match[bytes]") -> bytes:
        value = match.group(1)
        if len(value) > len(digest):
            return match.group(0)
        return match.group(0)[: -len(value)] + digest[-len(value) :]

    stable = _PDF_DATE.sub(date, data)
    stable = _PDF_ID.sub(ident, stable)
    stable = _PDF_CHECKSUM.sub(checksum, stable)
    assert len(stable) == len(data)  # offsets unchanged
    return stable


def render_pdf(path: str, kind: str, workdir: str) -> Tuple[str, Dict[str, Any]]:
    """Render the DOCX, ODT or RTF at `path` to PDF in `workdir`: the PDF's path and the facts.

    The facts are what `reader.rendering` reports: `tool`, `version`, `from`, `to`, `sha512` of the
    (reproducible, `stable_pdf`) PDF; `pages` is added by the caller once the PDF is read. No
    timing: the facts describe the rendering, not the run.
    Fails as the legacy conversion does: `dependency_missing` without LibreOffice,
    `conversion_failed`, or `limit_exceeded` past `LIBREOFFICE_TIMEOUT_S` (D13).
    """
    from api_util.digital_legacy import run_libreoffice  # noqa: PLC0415

    os.makedirs(workdir, exist_ok=True)
    copy = prepared_copy(path, kind, workdir)
    with open(copy, "rb") as handle:
        seed = handle.read()
    pdf, ran = run_libreoffice(copy, kind, CONVERT_TO, ".pdf", workdir)
    with open(pdf, "rb") as handle:
        data = handle.read()
    with open(pdf, "wb") as handle:
        handle.write(stable_pdf(data, seed))
    facts: Dict[str, Any] = {
        "tool": ran["tool"],
        "version": libreoffice_version(),
        "from": kind,
        "to": "pdf",
        "sha512": sha512_file(pdf),
    }
    return pdf, facts


__all__ = [
    "CONVERT_TO",
    "stable_pdf",
    "PDF_EXPORT_OPTIONS",
    "RENDERED_KINDS",
    "libreoffice_version",
    "prepared_copy",
    "render_pdf",
]
