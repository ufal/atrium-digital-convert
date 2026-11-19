"""
api_util/digital_legacy.py — headless LibreOffice: DOC and the spreadsheets, and the shared runner (#2 W2, #9).

AMČR accepts the legacy binary Office formats, and agreed on #4 (2026-09-26) that they reach the
record through a converter on this side: headless LibreOffice (MPL-2.0) inside the `digital`
image. It converts the file and the converted file is then read by the same readers as a native
one. A DOC becomes a DOCX (`digital_docx`), and since atrium-digital-convert#9 that DOCX is also
what is rendered for its pages (`api_util/office_render.py`).

A spreadsheet (XLSX or XLS) becomes an ODS, read by the shared reader's ODS path (`text_formats`).
Every cell is kept as the sheet displays it (motyc on #9, 2026-11-18: in AMČR's sheets the numbers
and dates are the content). An XLSX stores raw values and number formats, and only a spreadsheet
engine shows them; an ODS stores each cell's displayed text. So LibreOffice displays the sheet,
in `SPREADSHEET_LOCALE` (cs-CZ: `1,50`, `12 345`, `15.3.2021`, `PRAVDA`), set in the run's
throw-away profile, and the ODS keeps what it shows. An ODS upload is read as it is: its file
already holds the text it displayed.

What the output keeps is the ORIGINAL's identity, never the intermediate's: its media type
(`application/msword`, `application/vnd.ms-excel`) and its SHA-512 in the paradata. The
conversion itself (tool, target format, seconds) goes beside it (`DigitalDocument.conversion`, the
service's `reader.conversion`), and LibreOffice is logged among the run's components with its
terms, as a fact: paradata 3.0 computes no licence (D2), so the reason it was once left out of
the log (it would have entered the record's licence union) is gone.

`run_libreoffice()` is the one runner the conversion and the rendering share. Every run has its
own throw-away LibreOffice profile (`-env:UserInstallation`), because two concurrent `soffice`
processes sharing one profile block or crash each other, and a time limit
(`LIBREOFFICE_TIMEOUT_S`). A deployment without LibreOffice refuses these formats with
`dependency_missing` (CLI exit 2, HTTP 501) — never by silently reading nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from api_util.digital_ir import DigitalDocument, DigitalInputError, sha512_file

#: kind -> (LibreOffice filter target, the converted file's suffix, the kind it is then read as)
TARGETS: Dict[str, Tuple[str, str, str]] = {
    "doc": ("docx", ".docx", "docx"),
    "xls": ("ods:calc8", ".ods", "ods"),
    "xlsx": ("ods:calc8", ".ods", "ods"),
}

#: The kinds converted as spreadsheets: displayed by LibreOffice in `SPREADSHEET_LOCALE`.
SPREADSHEET_KINDS = frozenset({"xls", "xlsx"})

#: The locale a spreadsheet is displayed in (atrium-digital-convert#9, decided 2026-11-19): an
#: XLSX does not say how its numbers and default dates read, the viewer's locale does, and AMČR's
#: sheets are read in Czech. Recorded in the paradata as `spreadsheet_locale`.
SPREADSHEET_LOCALE = "cs-CZ"

#: The original's media type and `source.origin`, per converted kind.
MEDIA_TYPES: Dict[str, str] = {
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ORIGINS: Dict[str, str] = {
    "doc": "digital-born-doc",
    "xls": "digital-born-xls",
    "xlsx": "digital-born-xlsx",
}

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


def _seed_locale(profile: Path, locale: str) -> None:
    """Set the throw-away profile's locale (Tools > Options > Language Settings > Locale setting)
    before LibreOffice first starts on it: the locale its number formatter displays values in."""
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        '<item oor:path="/org.openoffice.Setup/L10N"><prop oor:name="ooSetupSystemLocale" oor:op="fuse">'
        f"<value>{locale}</value></prop></item>\n"
        "</oor:items>\n",
        encoding="utf-8",
    )


def run_libreoffice(
    path: str,
    kind: str,
    convert_to: str,
    out_suffix: str,
    workdir: str,
    locale: Optional[str] = None,
) -> Tuple[str, Dict[str, object]]:
    """Run headless LibreOffice once: `--convert-to convert_to` on a copy of `path` in `workdir`.

    The one runner the conversions and the rendering of office files share
    (`api_util/office_render.py`, atrium-digital-convert#9): its own throw-away profile, a neutral
    file name, `LIBREOFFICE_TIMEOUT_S`, and `dependency_missing` when LibreOffice is not installed.
    `locale`, when given, is set in that profile (a spreadsheet's display; the rendering sets none).
    Returns the output's path and the run's facts (`tool`, `from`, `to`, `seconds`, `binary`, and
    `locale` when one was set).
    """
    binary = libreoffice_binary()
    target = convert_to.split(":", 1)[0]
    if binary is None:
        raise DigitalInputError(
            "dependency_missing",
            f"a .{kind} file needs LibreOffice, and none is installed in this deployment (set "
            f"LIBREOFFICE_BIN, or use the -digital/-api image, which ships it)",
        )
    timeout = _timeout_s()
    profile = Path(workdir) / "lo-profile"
    outdir = Path(workdir) / "lo-out"
    outdir.mkdir(parents=True, exist_ok=True)
    # A neutral name: LibreOffice names its output after the input, and an uploaded name may
    # hold characters a filter mangles. The ORIGINAL name is kept by the caller, not here.
    staged = Path(workdir) / f"input.{kind}"
    if Path(path).resolve() != staged.resolve():
        shutil.copyfile(path, staged)
    if locale:
        _seed_locale(profile, locale)
    command = [
        binary,
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        convert_to,
        "--outdir",
        str(outdir),
        str(staged),
    ]
    started = time.monotonic()
    try:
        # TZ=UTC: LibreOffice stamps a PDF with the local time and its offset ("Z'" or "+01'00'"), so
        # the bytes would depend on the host's time zone (office_render.stable_pdf fixes the digits).
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "TZ": "UTC"},
        )
    except subprocess.TimeoutExpired as exc:
        import tool_limits  # noqa: PLC0415

        raise tool_limits.LIBREOFFICE_TIMEOUT_S.exceeded(
            None,
            detail=(
                f"LibreOffice did not convert the .{kind} file to .{target} within {timeout:g} s "
                "(LIBREOFFICE_TIMEOUT_S). Raise the limit, or send a smaller file."
            ),
        ) from exc
    elapsed = round(time.monotonic() - started, 3)
    converted = outdir / f"input{out_suffix}"
    if proc.returncode != 0 or not converted.is_file() or converted.stat().st_size == 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [
            f"exit code {proc.returncode}"
        ]
        raise DigitalInputError(
            "conversion_failed",
            f"LibreOffice could not convert the .{kind} file to .{target} ({tail[0][:200]}); the file "
            f"may be damaged or password-protected",
        )
    facts: Dict[str, object] = {
        "tool": "libreoffice",
        "from": kind,
        "to": target,
        "seconds": elapsed,
        "binary": os.path.basename(binary),
    }
    if locale:
        facts["locale"] = locale
    return str(converted), facts


def convert_legacy(path: str, kind: str, workdir: str) -> Tuple[str, Dict[str, object]]:
    """Convert one DOC, XLS or XLSX into `workdir`; return the converted path and the conversion
    facts. A spreadsheet is displayed in `SPREADSHEET_LOCALE`."""
    if kind not in TARGETS:
        raise ValueError(f"digital_legacy converts {tuple(TARGETS)}, not {kind!r}")
    target, suffix, _ = TARGETS[kind]
    locale = SPREADSHEET_LOCALE if kind in SPREADSHEET_KINDS else None
    return run_libreoffice(path, kind, target, suffix, workdir, locale=locale)


def extract_legacy(
    path: str,
    doc_id: str,
    kind: str,
    read_converted: Callable[[str, str], DigitalDocument],
) -> DigitalDocument:
    """Layer A for DOC, XLS and XLSX: convert, read the converted file, restore the original's
    identity.

    `read_converted(converted_path, converted_kind)` is the converter's own dispatcher, so the
    converted file goes through exactly the reader a native DOCX/ODS would.
    """
    with tempfile.TemporaryDirectory(prefix="digital_legacy_") as workdir:
        converted, facts = convert_legacy(path, kind, workdir)
        document = read_converted(converted, TARGETS[kind][2])
    document.doc_id = doc_id
    document.origin = ORIGINS[kind]
    document.media_type = MEDIA_TYPES[kind]
    document.sha512 = sha512_file(path)
    document.filename = os.path.basename(path)
    document.kind = kind
    document.conversion = facts
    document.notes.append(f"converted:{kind}->{facts['to']}")
    document.use("libreoffice")
    return document
