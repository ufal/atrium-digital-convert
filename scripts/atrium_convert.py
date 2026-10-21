#!/usr/bin/env python3
"""Zero-dependency client for the ATRIUM digital-convert API (strategy §6 / Appendix B).

Sends born-digital documents (PDF, DOCX, ODT, ODS, XLSX, RTF; DOC and XLS when the server has
LibreOffice) to a running digital-convert service and prints one row per page:

  - default:     POST /reformat — the ATRIUM document record; per page its text layer and
                 whether it needs OCR;
  - --describe:  POST /describe — the same conversion, then a per-page assessment: the page
                 type (page-classification) and text quality (ocr-postprocess) when the server
                 has their URLs, and where each page should go next (nlp, ocr, htr, none).

  --out-dir DIR writes each record as DIR/<doc_id>.document.json (and DIR/<doc_id>.md with
  --markdown). Per-file summaries go to stderr; stdout stays clean for table/csv/json.

Exit codes:
    0 - success
    1 - client-side error (bad arguments, unreadable file)
    2 - server unreachable (connection refused / timeout)
    3 - server-side error (HTTP 4xx/5xx)
"""

import argparse
import csv
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("ATRIUM_DC_URL", "http://localhost:8000")
INPUT_SUFFIXES = {".pdf", ".docx", ".odt", ".ods", ".xlsx", ".rtf", ".doc", ".xls"}
MAX_UPLOAD_MB = 200  # mirrors the server's MAX_UPLOAD_MB default
RETRY_STATUS = {502, 503, 504}
# A 502 is retried only with one of these reasons (atrium_service.REASON_CODES); without one,
# `detail` names a cause that a retry does not change (atrium-nlp-enrich#41).
RETRY_502_REASONS = {"upstream_unavailable"}
RETRY_ATTEMPTS = 3
RETRY_WAIT_S = 10
TIMEOUT_S = 900  # a large PDF, plus /describe's calls to two other services

KNOWN_PIPELINE_SUFFIXES = [  # mirror of atrium_document.KNOWN_PIPELINE_SUFFIXES
    ".document.json",
    ".categories.json",
    ".teitok.xml",
    ".alto.xml",
    ".udpipe.conllu",
    ".conllu",
    ".xml",
    ".json",
    ".md",
    ".csv",
    ".txt",
    ".pdf",
    ".docx",
    ".docm",
    ".dotx",
    ".odt",
    ".ods",
    ".odp",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".pptm",
    ".epub",
    ".rtf",
    ".html",
    ".htm",
    ".xhtml",
    ".hocr",
    ".tei",
    ".jsonl",
    ".ndjson",
    ".tsv",
    ".tab",
    ".markdown",
    ".mdown",
    ".text",
    ".log",
    ".srt",
    ".vtt",
    ".eml",
    ".mbox",
    ".mbx",
    ".zip",
]

# What to do about a refusal, by the error body's `reason` (service/README.md, Errors).
HINTS = {
    "ocr_text_layer": "its text layer is an earlier OCR run: send the scan to the OCR route instead",
    "unsupported_media_type": "not a born-digital type this service reads; a scanned document goes to OCR",
    "source_digest_mismatch": "the seed's source.sha512 is not this file's: send the file the seed was made for",
    "invalid_record": "the --seed record cannot be opened: send the seed AMČR made",
    "limit_exceeded": "over a server limit (the body's `limit` names it): split the document",
}


def canonical_doc_id(path: Path) -> str:
    """Mirror the hub's ID derivation to ensure client parity."""
    name = path.name
    lower = name.lower()
    for suffix in KNOWN_PIPELINE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name.split(".")[0]


def build_multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Encode form fields and {field: path} files as multipart/form-data using only the stdlib."""
    boundary = uuid.uuid4().hex
    lines = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode())

    for field, file_path in files.items():
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{field}"; filename="{file_path.name}"'.encode())
        lines.append(f"Content-Type: {mime}".encode())
        lines.append(b"")
        lines.append(file_path.read_bytes())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")

    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def is_retryable(status: int, body: str) -> bool:
    """A 503/504 is. A 502 in the service's error body is retried only with a reason in
    RETRY_502_REASONS; a 502 without that body came from a proxy in front of the service and is."""
    if status != 502:
        return True
    try:
        error = json.loads(body)
    except ValueError:
        return True  # not the service's answer: a proxy or gateway page
    if not isinstance(error, dict) or "reason" not in error:
        return True
    return error["reason"] in RETRY_502_REASONS


def refusal_hint(status: int, body: str) -> str:
    """One line on what to do about a refusal, from the error body."""
    try:
        error = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(error, dict):
        return ""
    if status == 429:
        return "all conversion slots are busy: retry after the Retry-After seconds"
    if status == 501:
        return "this server has no LibreOffice for DOC/XLS: use the image, or convert to DOCX/XLSX first"
    hint = HINTS.get(error.get("reason"), "")
    suffixes = [item for item in error.get("accepted") or [] if str(item).startswith(".")]
    if suffixes:
        hint += f" (accepted: {', '.join(suffixes)})"
    return hint


def http_json(url: str, data: bytes = None, content_type: str = None, timeout: int = TIMEOUT_S) -> dict:
    """POST (or GET when data is None) and decode JSON, with retry on 503/504 and a retryable 502."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in RETRY_STATUS and attempt < RETRY_ATTEMPTS and is_retryable(e.code, detail):
                print(f"[retry {attempt}/{RETRY_ATTEMPTS}] HTTP {e.code}, waiting {RETRY_WAIT_S}s...", file=sys.stderr)
                time.sleep(RETRY_WAIT_S)
                last_error = f"HTTP {e.code}: {detail}"
                continue
            print(f"Server error - HTTP {e.code}: {detail}", file=sys.stderr)
            hint = refusal_hint(e.code, detail)
            if hint:
                print(f"  -> {hint}", file=sys.stderr)
            sys.exit(3)
        except (urllib.error.URLError, TimeoutError) as e:
            print(
                f"Cannot reach the API at {url} ({e}).\nIs the server running? Start it with: bash scripts/server.sh",
                file=sys.stderr,
            )
            sys.exit(2)
    print(f"Server error after {RETRY_ATTEMPTS} attempts - {last_error}", file=sys.stderr)
    sys.exit(3)


def process_file(base_url: str, path: Path, endpoint: str, seed: Path = None, **params) -> dict:
    """Pre-check, then upload one document (and its seed) to /reformat or /describe."""
    if path.suffix.lower() not in INPUT_SUFFIXES:
        print(f"Skipping {path}: not a born-digital type this service reads", file=sys.stderr)
        return {}
    size = path.stat().st_size
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        print(f"Skipping {path}: exceeds the {MAX_UPLOAD_MB} MB server limit - split the document", file=sys.stderr)
        return {}
    files = {"file": path}
    if seed is not None:
        files["document_json"] = seed
    body, content_type = build_multipart(params, files)
    return http_json(f"{base_url}/{endpoint}", data=body, content_type=content_type)


def reformat_rows(path: Path, result: dict) -> list[tuple]:
    """(FILE, PAGE, TEXT_LAYER, LINES, NEEDS_OCR, REASON) per page of the record."""
    record = result.get("document_json") or {}
    lines = {}
    for line in record.get("lines") or []:
        lines[str(line.get("page"))] = lines.get(str(line.get("page")), 0) + 1
    rows = []
    for page in record.get("pages") or []:
        key = str(page.get("page"))
        needs = "yes" if page.get("needs_ocr") else "no"
        rows.append(
            (path.name, key, page.get("text_layer") or "", lines.get(key, 0), needs, page.get("needs_ocr_reason") or "")
        )
    return rows


def describe_rows(path: Path, result: dict) -> list[tuple]:
    """(FILE, PAGE, TEXT_LAYER, CATEGORY, QUALITY, ROUTE, REASON) per page of the assessment."""
    rows = []
    for page in result.get("pages") or []:
        category = page.get("category") or {}
        label = category.get("label") or ""
        if label and category.get("confidence") is not None:
            label = f"{label} {category['confidence']:.2f}"
        quality = page.get("quality") or {}
        band = quality.get("band") or ""
        if band:
            band = f"{band} ({quality.get('source')})"
        rows.append(
            (
                path.name,
                str(page.get("page")),
                page.get("text_layer") or "",
                label,
                band,
                page.get("route") or "",
                page.get("route_reason") or "",
            )
        )
    return rows


def print_table(header: tuple, rows: list[tuple], as_csv: bool) -> None:
    """Aligned table on stdout (the last column, a free-text reason, cut to 70 characters), or CSV
    through the csv module, complete."""
    if as_csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(header)
        writer.writerows(rows)
        return
    rows = [(*row[:-1], str(row[-1])[:67] + "..." if len(str(row[-1])) > 70 else row[-1]) for row in rows]
    widths = [max(len(str(value)) for value in column) for column in zip(header, *rows)]
    for row in (header, *rows):
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def summarize(path: Path, result: dict, describe: bool) -> None:
    """One stderr line per document."""
    reader = result.get("reader") or {}
    line = f"{path.name}: doc_id {result.get('doc_id')}, {reader.get('kind')}, {reader.get('pages')} pages"
    if describe:
        summary = result.get("summary") or {}
        stages = ", ".join(f"{s.get('stage')} {s.get('status')}" for s in result.get("stages") or [])
        line += f"; route {summary.get('document_route')}, re-acquire pages {summary.get('reacquire_pages')}"
        line += f"; stages: {stages}"
    else:
        flagged = [p.get("page") for p in (result.get("document_json") or {}).get("pages") or [] if p.get("needs_ocr")]
        line += f"; needs_ocr pages {flagged}"
    if result.get("document_json_schema_error"):
        line += "; the record does not validate (document_json_schema_error)"
    print(line, file=sys.stderr)


def write_outputs(out_dir: Path, result: dict) -> None:
    """DIR/<doc_id>.document.json, and DIR/<doc_id>.md when the server rendered Markdown."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_id = result.get("doc_id") or "document"
    record_path = out_dir / f"{doc_id}.document.json"
    record_path.write_text(
        json.dumps(result.get("document_json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  wrote {record_path}", file=sys.stderr)
    if result.get("markdown") is not None:
        md_path = out_dir / f"{doc_id}.md"
        md_path.write_text(result["markdown"], encoding="utf-8")
        print(f"  wrote {md_path}", file=sys.stderr)


class UsageParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; the client contract keeps 2 for "server unreachable"."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = UsageParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="born-digital documents (.pdf .docx .odt .ods .xlsx .rtf .doc .xls)")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default: {DEFAULT_BASE_URL}, env: ATRIUM_DC_URL)"
    )
    parser.add_argument("--describe", action="store_true", help="POST /describe: assess and route every page")
    parser.add_argument(
        "--seed",
        type=Path,
        help="a record to send as document_json (the AMČR seed, or an earlier record); one file only",
    )
    parser.add_argument("--markdown", action="store_true", help="/reformat: also render the record as Markdown")
    parser.add_argument(
        "--stages",
        help="/describe: page-classification, ocr-postprocess (comma-separated), or none; default every configured one",
    )
    parser.add_argument(
        "--classify-pages",
        choices=["needs_ocr", "all"],
        default="needs_ocr",
        help="/describe: which pages page-classification is asked about (default: needs_ocr)",
    )
    parser.add_argument("--no-text", action="store_true", help="/describe: leave pages[].text out of the answer")
    parser.add_argument(
        "--docx-page-breaks",
        choices=["auto", "explicit", "none"],
        default="auto",
        help="how DOCX pages are cut (default: auto)",
    )
    parser.add_argument("--out-dir", type=Path, help="write <doc_id>.document.json (and .md) here")
    parser.add_argument("--format", choices=["table", "csv", "json"], default="table", help="output format")
    parser.add_argument("--info", action="store_true", help="print service capabilities and limits, then exit")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    if args.info:
        print(json.dumps(http_json(f"{base_url}/info", timeout=60), indent=2))
        return

    if not args.files:
        parser.error("no input files given (or use --info)")
    if args.seed is not None and len(args.files) != 1:
        parser.error("--seed describes one document: give one file with it")
    if args.seed is not None and not args.seed.is_file():
        print(f"File not found: {args.seed}", file=sys.stderr)
        sys.exit(1)
    if args.describe and args.markdown:
        parser.error("--markdown is a /reformat option; /describe does not render Markdown")

    endpoint = "describe" if args.describe else "reformat"
    params = {"docx_page_breaks": args.docx_page_breaks}
    if args.describe:
        params["classify_pages"] = args.classify_pages
        params["include_text"] = "false" if args.no_text else "true"
        if args.stages is not None:
            params["stages"] = args.stages
    elif args.markdown:
        params["markdown"] = "true"

    raw_results = {}
    rows = []
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)

        result = process_file(base_url, path, endpoint, seed=args.seed, **params)
        if not result:
            continue
        doc_id = result.get("doc_id") or canonical_doc_id(path)
        raw_results[doc_id] = result
        summarize(path, result, args.describe)
        if args.out_dir is not None:
            write_outputs(args.out_dir, result)
        rows.extend(describe_rows(path, result) if args.describe else reformat_rows(path, result))

    if not raw_results:
        print("No results produced.", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(raw_results, indent=2, ensure_ascii=False))
    elif args.describe:
        header = ("FILE", "PAGE", "TEXT_LAYER", "CATEGORY", "QUALITY", "ROUTE", "REASON")
        print_table(header, rows, as_csv=(args.format == "csv"))
    else:
        header = ("FILE", "PAGE", "TEXT_LAYER", "LINES", "NEEDS_OCR", "REASON")
        print_table(header, rows, as_csv=(args.format == "csv"))


if __name__ == "__main__":
    main()
