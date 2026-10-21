"""
api_util/digital_legacy.py — legacy DOC and XLS through headless LibreOffice (#2 W2).

AMČR accepts the legacy binary Office formats, and agreed on #4 (2026-09-26) that they reach the
record through a converter on this side: headless LibreOffice (MPL-2.0) inside the `digital`
image, declared as a conditional component. It only converts the file — DOC → DOCX, XLS →
XLSX — and the converted file is then read by the same readers as a native one
(`digital_docx`, or the shared reader for XLSX). So LibreOffice adds nothing to the record's
licence and nothing to its shape.

What the record keeps is the ORIGINAL's identity, never the intermediate's:

  * `source.filename`, `source.media_type` (`application/msword`, `application/vnd.ms-excel`)
    and `source.sha256` describe the uploaded file;
  * `source.origin` is `digital-born-doc` / `digital-born-xls`;
  * the conversion itself (tool, target format, seconds) is reported beside the record
    (`DigitalDocument.conversion`, the service's `reader.conversion`). LibreOffice is declared
    in `para_config.txt` but not logged into the licence union: it converts, it does not author.

Every conversion runs with its own throw-away LibreOffice profile (`-env:UserInstallation`),
because two concurrent `soffice` processes sharing one profile block or crash each other, and
under a time limit (`LIBREOFFICE_TIMEOUT_S`). A deployment without LibreOffice refuses these two
formats with `dependency_missing` (CLI exit 2, HTTP 501) — never by silently reading nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from api_util.digital_ir import DigitalDocument, DigitalInputError, sha256_file

#: kind -> (LibreOffice filter target, the converted file's suffix, the kind it is then read as)
TARGETS: Dict[str, Tuple[str, str, str]] = {
    "doc": ("docx", ".docx", "docx"),
    "xls": ("xlsx", ".xlsx", "xlsx"),
}

#: The original's media type and `source.origin`, per legacy kind.
MEDIA_TYPES: Dict[str, str] = {"doc": "application/msword", "xls": "application/vnd.ms-excel"}
ORIGINS: Dict[str, str] = {"doc": "digital-born-doc", "xls": "digital-born-xls"}

#: Where to look for the binary when `LIBREOFFICE_BIN` is not set.
_CANDIDATES = ("soffice", "libreoffice")


def libreoffice_binary() -> Optional[str]:
    """The LibreOffice executable this deployment can run, or None."""
    configured = (os.environ.get("LIBREOFFICE_BIN") or "").strip()
    if configured:
        return shutil.which(configured) or (configured if os.access(configured, os.X_OK) else None)
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _timeout_s() -> float:
    try:
        import tool_limits  # noqa: PLC0415  (repo root)
    except ImportError:  # pragma: no cover - the repo root is always on sys.path here
        return 120.0
    return float(tool_limits.LIBREOFFICE_TIMEOUT_S.get())


def convert_legacy(path: str, kind: str, workdir: str) -> Tuple[str, Dict[str, object]]:
    """Convert one DOC/XLS into `workdir`; return the converted path and the conversion facts."""
    if kind not in TARGETS:
        raise ValueError(f"digital_legacy converts {tuple(TARGETS)}, not {kind!r}")
    binary = libreoffice_binary()
    if binary is None:
        raise DigitalInputError(
            "dependency_missing",
            f"a legacy .{kind} file needs LibreOffice to be converted, and none is installed in this "
            f"deployment (set LIBREOFFICE_BIN, or use the -digital/-api image, which ships it)",
        )
    target, suffix, _ = TARGETS[kind]
    timeout = _timeout_s()
    profile = Path(workdir) / "lo-profile"
    outdir = Path(workdir) / "lo-out"
    outdir.mkdir(parents=True, exist_ok=True)
    # A neutral name: LibreOffice names its output after the input, and an uploaded name may
    # hold characters a filter mangles. The ORIGINAL name is kept by the caller, not here.
    staged = Path(workdir) / f"input.{kind}"
    shutil.copyfile(path, staged)
    command = [
        binary,
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        target,
        "--outdir",
        str(outdir),
        str(staged),
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        import tool_limits  # noqa: PLC0415

        raise tool_limits.LIBREOFFICE_TIMEOUT_S.exceeded(
            None,
            detail=(
                f"LibreOffice did not convert the .{kind} file within {timeout:g} s "
                "(LIBREOFFICE_TIMEOUT_S). Convert it to ."
                f"{target} before sending it, or raise the limit."
            ),
        ) from exc
    elapsed = round(time.monotonic() - started, 3)
    converted = outdir / f"input{suffix}"
    if proc.returncode != 0 or not converted.is_file() or converted.stat().st_size == 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [
            f"exit code {proc.returncode}"
        ]
        raise DigitalInputError(
            "conversion_failed",
            f"LibreOffice could not convert the .{kind} file to .{target} ({tail[0][:200]}); the file "
            f"may be damaged or password-protected",
        )
    return str(converted), {
        "tool": "libreoffice",
        "from": kind,
        "to": target,
        "seconds": elapsed,
        "binary": os.path.basename(binary),
    }


def extract_legacy(
    path: str,
    doc_id: str,
    kind: str,
    read_converted: Callable[[str, str], DigitalDocument],
) -> DigitalDocument:
    """Layer A for DOC/XLS: convert, read the converted file, restore the original's identity.

    `read_converted(converted_path, converted_kind)` is the converter's own dispatcher, so the
    converted file goes through exactly the reader a native DOCX/XLSX would.
    """
    with tempfile.TemporaryDirectory(prefix="digital_legacy_") as workdir:
        converted, facts = convert_legacy(path, kind, workdir)
        document = read_converted(converted, TARGETS[kind][2])
    document.doc_id = doc_id
    document.origin = ORIGINS[kind]
    document.media_type = MEDIA_TYPES[kind]
    document.sha256 = sha256_file(path)
    document.filename = os.path.basename(path)
    document.kind = kind
    document.conversion = facts
    document.notes.append(f"converted:{kind}->{facts['to']}")
    # NOT `document.use("libreoffice")`: the component list is the record's licence union
    # (accretion rule 5), and LibreOffice only changes the container format of a text it does
    # not author — AMČR's condition on #4 was that it "adds nothing to the licence of the
    # record". Its para_config.txt row declares it; the conversion itself is reported in
    # `conversion` (the service's `reader.conversion`).
    return document
