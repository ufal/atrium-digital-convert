"""
api_util/digital_text.py — ODT, ODS, XLSX and RTF through the shared reader (Layer A, #2 W2).

The born-digital types AMČR accepts beyond PDF and DOCX are read by ONE reader family, the one
atrium-ocr-postprocess owns (`text_formats.py`, its #31 text-lines readers; atrium-project#72
"one document reader"). It is vendored byte-identical at the repository root and pinned by
`tests/test_vendored_reader_parity.py`; this module only maps its output onto the converter's
internal representation, so Layers B–D (decode sanity, the record, the gates) treat these
documents exactly like a PDF or DOCX.

What the reader gives, and therefore what the record gets:

  * **pages** — the format's own pages: ODT page breaks (explicit and soft), one page per sheet
    for ODS/XLSX (label = sheet name), `\\page` for RTF. A sheet longer than the reader's line
    cap continues on `<name>+1`… pages.
  * **lines** — the format's own units: a paragraph or table cell (ODT, RTF), a row of text
    cells joined by a tab (ODS, XLSX). Numbers, dates and formulas are not text and are dropped
    by the reader.
  * **no geometry** — none of these formats has page coordinates without rendering, so, like a
    DOCX, the record carries no `bbox` and no `canvas` (the schema's "do not fabricate bounding
    boxes" rule).
  * **groups** — a spreadsheet page is one group (`s{page_index}`), so a consumer that turns
    groups into paragraphs keeps a sheet together; a text document's lines stay ungrouped.
    Spreadsheet `tables[]` (cell grids) are a follow-up, not part of v1.1.0-beta.

`source.origin` is the reader's own registry value (`digital-born-odt`, `-ods`, `-xlsx`,
`-rtf`), which `atrium_document.ORIGIN_ORIGINATORS` routes to this converter.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from api_util.digital_ir import (
    TEXT_LAYER_BLANK,
    TEXT_LAYER_DIGITAL,
    DigitalDocument,
    DigitalInputError,
    DigitalLine,
    DigitalPage,
    sha256_file,
)

#: The kinds this adapter reads, i.e. the born-digital types beyond PDF/DOCX that AMČR accepts.
KINDS: Tuple[str, ...] = ("odt", "ods", "xlsx", "rtf")

#: Spreadsheet kinds: one group per page (sheet).
SHEET_KINDS = frozenset({"ods", "xlsx"})

#: The reader's `IngestError` codes, mapped onto the converter's `DigitalInputError` reasons
#: (which the CLI turns into exit codes and the service into HTTP statuses). Codes not listed
#: are a file this reader could not read → `corrupt`.
_INGEST_REASONS: Dict[str, str] = {
    "encrypted": "encrypted",
    "zip_limits_exceeded": "zip_limits_exceeded",
    "too_large": "zip_limits_exceeded",
    "dependency_missing": "dependency_missing",
    "legacy_office_unsupported": "legacy_office_unsupported",
    "archive_unsupported": "unsupported",
    "binary_content": "unsupported",
    "image_needs_ocr": "unsupported",
}


def _reader():
    """The vendored reader, imported on use (it imports `tool_limits` from the repo root)."""
    import text_formats  # noqa: PLC0415  (repo root; put on sys.path by digital_to_json)

    return text_formats


def reader_limits(max_file_mb: Optional[float] = None, max_pages: Optional[int] = None):
    """The reader's `Limits`, aligned with this service's own upload and page limits."""
    tf = _reader()
    defaults = tf.DEFAULT_LIMITS
    changes = {}
    if max_file_mb:
        changes["max_file_mb"] = float(max_file_mb)
        changes["zip_max_member_mb"] = float(max_file_mb)
    if max_pages:
        changes["max_pages"] = int(max_pages)
    return tf.replace(defaults, **changes) if changes else defaults


def extract_text_document(
    path: str,
    doc_id: str,
    kind: str,
    max_file_mb: Optional[float] = None,
    max_pages: Optional[int] = None,
) -> DigitalDocument:
    """Layer A for ODT/ODS/XLSX/RTF: read with the shared reader, map to the IR."""
    if kind not in KINDS:
        raise ValueError(f"digital_text reads {KINDS}, not {kind!r}")
    tf = _reader()
    spec = tf.READERS[kind]
    try:
        text_doc = tf.read_document(
            path, reader_limits(max_file_mb, max_pages), tf.DEFAULT_OPTIONS, kind=kind
        )
    except tf.IngestError as exc:
        reason = _INGEST_REASONS.get(exc.code, "corrupt")
        raise DigitalInputError(
            reason, f"{spec.label} could not be read ({exc.code}: {exc.message})"
        ) from exc

    document = DigitalDocument(
        doc_id=doc_id,
        origin=spec.default_origin,
        media_type=spec.media_type,
        sha256=sha256_file(path),
        filename=os.path.basename(path),
        reading_order="logical",
        kind=kind,
        notes=list(text_doc.notes),
    )
    document.use("text_formats")
    if kind in ("xlsx", "odt", "ods"):
        document.use("lxml")

    for index, text_page in enumerate(text_doc.pages, 1):
        label = (text_page.label or "").strip() or str(index)
        page = DigitalPage(page=label, page_index=index, text_layer=TEXT_LAYER_DIGITAL)
        lines = [line for line in text_page.lines if line and line.strip()]
        if not lines:
            page.text_layer = TEXT_LAYER_BLANK
        group = f"s{index}" if kind in SHEET_KINDS else None
        for number, text in enumerate(lines):
            page.lines.append(
                DigitalLine(page=label, line=number, text=text.strip(), group_id=group)
            )
        document.pages.append(page)
    _unique_labels(document)
    return document


def _unique_labels(document: DigitalDocument) -> None:
    """Make page labels unique — `(page, line)` is the record's line key.

    Two sheets may share a name only in a damaged workbook, and a reader page label can repeat
    when a text document restarts its numbering; either would merge two pages' lines under one
    key. The later duplicate gets `#<page_index>` appended, which keeps the label readable.
    """
    seen = set()
    for page in document.pages:
        if page.page in seen:
            page.page = f"{page.page}#{page.page_index}"
            for line in page.lines:
                line.page = page.page
        seen.add(page.page)
