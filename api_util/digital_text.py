"""
api_util/digital_text.py — the shared reader's formats, and the text-only inputs (Layer A, #2 W2, #9).

Two families of input, both without page geometry of their own:

* **ODT, ODS, XLSX, RTF** are read by ONE reader family, the one atrium-ocr-postprocess owns
  (`text_formats.py`, its #31 text-lines readers; atrium-project#72 "one document reader"). It is
  vendored byte-identical at the repository root and pinned by
  `tests/test_vendored_reader_parity.py`; this module only maps its output onto the converter's
  internal representation. ODT and RTF are office documents: since atrium-digital-convert#9 their
  text read here is placed on the pages of their rendering (`api_util/render_place.py`), so this
  reading's pages do not reach the output. ODS and XLSX (XLS after LibreOffice) are spreadsheets:
  the textual workflow, one page per sheet.
* **TXT, CSV, TSV, JSON** — the text-only inputs of atrium-digital-convert#9 (point 4: they have no
  pages to render, skip the visual steps and go straight to scoring). Their content is extracted
  as it is, with the shared reader's decoding (`text_formats.decode_bytes`: BOM, UTF-8, the
  archive's legacy code pages) and its line canonicalisation (`normalize_line`), but NOT its
  OCR-export readings: every CSV/TSV column is kept (the shared reader keeps only a `text` column
  when it finds one), and every string value of a JSON document (the shared reader looks for
  OCR-style text keys).

  * TXT — pages are form-feed sections, lines are physical lines, and a blank line ends a
    paragraph (one group each).
  * CSV, TSV — one page; every row that has a non-empty cell is one line of its non-empty cells
    joined by a tab, the header row included; the page is one group. The CSV delimiter is
    sniffed among comma, semicolon, tab and bar; a quoting error fails the run (`corrupt`).
  * JSON — one page; every string value, in document order, is one group of its lines (split at
    its line breaks). Keys, numbers, booleans and nulls are structure, not text. A file that is
    not one JSON document (JSON Lines among them) fails the run (`corrupt`).

What the textual workflow's record gets:

  * **no geometry** — no `bbox` and no page size (the schema's "do not fabricate bounding boxes"
    rule; the docproc proposal's flow documents);
  * **lines no longer than `MAX_LINE_CHARS`** — a longer line (a paragraph typed on one line, a
    long row) is wrapped at a word boundary with the shared reader's `wrap_line`, as
    ocr-postprocess's own text path does, and every wrap is a split the run's paradata record
    (`DigitalDocument.splits`; D13);
  * **no ATR** — the pages are `digital` (or `blank`, routed `empty`), whatever their decode
    flags say (`digital_to_json.assess_page`).

A reading that skipped content fails the run (D13): every lossy note of the shared reader
(`text_formats.LOSSY_NOTE_PREFIXES`) but `decode_replacement`, whose U+FFFD characters stay in the
lines for scoring to judge (atrium-digital-convert#9 point 3).
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api_util.digital_ir import (
    TEXT_LAYER_BLANK,
    TEXT_LAYER_DIGITAL,
    DigitalDocument,
    DigitalInputError,
    DigitalLine,
    DigitalPage,
    sha512_file,
)

#: The kinds the shared reader reads here: the born-digital types beyond PDF/DOCX that AMČR accepts.
KINDS: Tuple[str, ...] = ("odt", "ods", "xlsx", "rtf")

#: The text-only inputs (atrium-digital-convert#9 point 4), read as they are by this module.
TEXT_ONLY_KINDS: Tuple[str, ...] = ("txt", "csv", "tsv", "json")

#: Their media types (`source.media_type` unless the seed names one).
TEXT_ONLY_MEDIA_TYPES: Dict[str, str] = {
    "txt": "text/plain",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
}

#: Spreadsheet kinds: one group per page (sheet).
SHEET_KINDS = frozenset({"ods", "xlsx"})

#: Shared-reader notes that are lossy (`text_formats.LOSSY_NOTE_PREFIXES`) but keep the content:
#: undecodable bytes became U+FFFD, which scoring judges (#9 point 3).
_KEPT_LOSSY = ("decode_replacement",)

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
    if "sheet_repeat_capped" in text_doc.notes:
        # D13 (atrium-project#84): a repeated row or column past the cap is content the reader
        # did not read. The step fails; until #84 the rest was dropped with a note.
        from atrium_limits import ContentLimitError  # noqa: PLC0415  (repo root)

        raise ContentLimitError(
            "odf_repeat_cap",
            "trimmed",
            1,
            f"{spec.label}: a cell or row repeats more than ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP times",
        )
    _fail_on_loss(text_doc.notes, spec.label, [p.flags for p in text_doc.pages])

    document = DigitalDocument(
        doc_id=doc_id,
        origin=spec.default_origin,
        media_type=spec.media_type,
        sha512=sha512_file(path),
        filename=os.path.basename(path),
        reading_order="logical",
        kind=kind,
        notes=list(text_doc.notes),
    )
    document.use("text_formats")
    if kind in ("xlsx", "odt", "ods"):
        document.use("lxml")

    if kind in SHEET_KINDS:
        document.workflow = "textual"
        if "page_overflow_split" in text_doc.notes:
            document.splits.append(
                ("max_lines_per_page", reader_limits(max_file_mb, max_pages).max_lines_per_page,
                 sum(1 for p in text_doc.pages if "overflow" in p.flags),
                 "a sheet longer than the reader's line cap continues on further pages")
            )
    for index, text_page in enumerate(text_doc.pages, 1):
        label = (text_page.label or "").strip() or str(index)
        page = DigitalPage(page=label, page_index=index, text_layer=TEXT_LAYER_DIGITAL)
        lines = [line for line in text_page.lines if line and line.strip()]
        if kind in SHEET_KINDS:
            _add_lines(document, page, [(f"s{index}", text) for text in lines])
        else:
            for number, text in enumerate(lines):
                page.lines.append(DigitalLine(page=label, line=number, text=text.strip()))
        if not page.lines:
            page.text_layer = TEXT_LAYER_BLANK
        document.pages.append(page)
    _unique_labels(document)
    return document


def _fail_on_loss(notes: Iterable[str], label: str, page_flags: Iterable[Iterable[str]] = ()) -> None:
    """Fail the run when the reading skipped or altered content (D13; the module docstring)."""
    tf = _reader()
    lost = []
    for note in notes:
        if note.startswith(tf.LOSSY_NOTE_PREFIXES) and not note.startswith(_KEPT_LOSSY):
            lost.append(note)
    for flags in page_flags:
        lost.extend(flag for flag in flags if flag in tf.LOSSY_PAGE_FLAGS)
    if lost:
        raise DigitalInputError(
            "corrupt",
            f"{label} could not be read whole ({', '.join(sorted(set(lost)))}): the reader would skip "
            "or alter content, and a partial reading is not written (D13)",
        )


def _max_line_chars() -> int:
    try:
        import tool_limits  # noqa: PLC0415  (repo root)
    except ImportError:  # pragma: no cover - the repo root is on sys.path here
        return 1000
    return int(tool_limits.MAX_LINE_CHARS.get())


def _add_lines(document: DigitalDocument, page: DigitalPage, rows: Iterable[Tuple[str, str]]) -> None:
    """Append `(group, text)` rows to `page` as lines, canonicalised and wrapped at MAX_LINE_CHARS.

    Each line that had to be wrapped adds one to the document's `max_line_chars` split."""
    tf = _reader()
    width = _max_line_chars()
    wrapped = 0
    for group, raw in rows:
        line = tf.normalize_line(raw)
        if not line:
            continue
        pieces = tf.wrap_line(line, width)
        if len(pieces) > 1:
            wrapped += 1
        for piece in pieces:
            piece = piece.strip()
            if piece:
                page.lines.append(DigitalLine(page=page.page, line=len(page.lines), text=piece, group_id=group))
    if wrapped:
        _note_split(document, wrapped, width)


def _note_split(document: DigitalDocument, count: int, width: int) -> None:
    for i, (limit, value, previous, detail) in enumerate(document.splits):
        if limit == "max_line_chars":
            document.splits[i] = (limit, value, previous + count, detail)
            return
    document.splits.append(
        ("max_line_chars", width, count, f"lines longer than {width} characters wrapped at a word boundary")
    )


def _decode(path: str, label: str, max_file_mb: Optional[float]) -> Tuple[str, List[str]]:
    """The file's text (shared decoding) and its decode flags; a binary or too large file fails."""
    tf = _reader()
    try:
        size = os.path.getsize(path)
        if max_file_mb and size > float(max_file_mb) * 1024 * 1024:
            raise DigitalInputError("zip_limits_exceeded", f"{label}: {size} bytes over MAX_UPLOAD_MB")
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise DigitalInputError("corrupt", f"{label} could not be read ({exc})") from exc
    try:
        text, encoding, flags = tf.decode_bytes(data)
    except tf.IngestError as exc:
        raise DigitalInputError("unsupported", f"{label} is not text ({exc.code}: {exc.message})") from exc
    return tf.normalize_newlines(text), [f"encoding={encoding}", *flags]


def _txt_rows(text: str) -> List[List[Tuple[str, str]]]:
    """TXT pages (form-feed sections) as `(group, line)` rows; a blank line ends a paragraph."""
    tf = _reader()
    pages: List[List[Tuple[str, str]]] = []
    paragraph = 0
    for lines in tf.text_to_pages(text):
        rows: List[Tuple[str, str]] = []
        open_paragraph = False
        for line in lines:
            if not line.strip():
                if open_paragraph:
                    paragraph += 1
                open_paragraph = False
                continue
            rows.append((f"t{paragraph}", line))
            open_paragraph = True
        if open_paragraph:
            paragraph += 1
        pages.append(rows)
    return pages


def _csv_rows(text: str, kind: str, label: str) -> List[Tuple[str, str]]:
    """Every CSV/TSV row with a non-empty cell, its non-empty cells joined by a tab."""
    text = text.replace("\x00", "")
    if kind == "tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    previous = csv.field_size_limit()
    csv.field_size_limit(max(previous, len(text) + 1))
    rows: List[Tuple[str, str]] = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True):
            cells = [" ".join(cell.split()) for cell in row]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(("c0", "\t".join(cells)))
    except csv.Error as exc:
        raise DigitalInputError("corrupt", f"{label} could not be parsed ({exc})") from exc
    finally:
        csv.field_size_limit(previous)
    return rows


def _json_strings(value: Any) -> Iterable[str]:
    """Every string value of a parsed JSON document, in document order (iterative)."""
    stack: List[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _json_rows(text: str, label: str) -> List[Tuple[str, str]]:
    try:
        value = json.loads(text)
    except RecursionError as exc:
        raise DigitalInputError("corrupt", f"{label} is nested too deeply to read") from exc
    except ValueError as exc:
        raise DigitalInputError(
            "corrupt", f"{label} is not one JSON document ({str(exc)[:200]}); JSON Lines is not read"
        ) from exc
    rows: List[Tuple[str, str]] = []
    for n, string in enumerate(_json_strings(value)):
        rows.extend((f"j{n}", line) for line in string.split("\n"))
    return rows


def extract_text_only(
    path: str, doc_id: str, kind: str, max_file_mb: Optional[float] = None
) -> DigitalDocument:
    """Layer A for TXT, CSV, TSV and JSON: their content as it is (the module docstring)."""
    if kind not in TEXT_ONLY_KINDS:
        raise ValueError(f"digital_text reads text-only {TEXT_ONLY_KINDS}, not {kind!r}")
    label = kind.upper()
    text, flags = _decode(path, label, max_file_mb)
    document = DigitalDocument(
        doc_id=doc_id,
        origin=f"digital-born-{kind}",
        media_type=TEXT_ONLY_MEDIA_TYPES[kind],
        sha512=sha512_file(path),
        filename=os.path.basename(path),
        reading_order="logical",
        kind=kind,
        notes=flags,
        workflow="textual",
    )
    document.use("text_formats")
    if kind == "txt":
        pages = _txt_rows(text)
    elif kind in ("csv", "tsv"):
        pages = [_csv_rows(text, kind, label)]
    else:
        pages = [_json_rows(text, label)]
    for index, rows in enumerate(pages, 1):
        page = DigitalPage(page=str(index), page_index=index, text_layer=TEXT_LAYER_DIGITAL)
        _add_lines(document, page, rows)
        if not page.lines:
            page.text_layer = TEXT_LAYER_BLANK
        document.pages.append(page)
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
