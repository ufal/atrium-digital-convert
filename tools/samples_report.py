#!/usr/bin/env python3
"""tools/samples_report.py — measure a batch of PDFs run through digital-convert (#5).

Research tooling, run from a checkout and never part of an image (`.dockerignore` excludes
`tools/`). It serves a cluster job that runs every stage of this repository over a folder of real
PDFs (the 15 AMČR documents of atrium-digital-convert#5) and leaves one run directory behind.

    inventory  the facts the job sets its limits from: size, pages, digest, producer, and the
               largest page in pixels at page-classification's rendering resolution
    measure    run one command; record its exit code, wall and CPU time and peak memory
    report     read the run directory and the PDFs; write `<run>/report/`: the per-document
               tables, the per-page signals (`page_signals.csv`, one column per stage run), the
               pages whose only text is furniture or a little text over an image, with
               thumbnails, what the quality model did to born-digital text, the limits the run
               suggests, and the delivery set (one record and one `/describe` report per
               document)

    python tools/samples_report.py inventory --samples DIR --out RUN/02_inventory/inventory.tsv
    python tools/samples_report.py measure --out RUN/10_cli_light/X.run.json --timeout 7200 -- \\
        python api_util/digital_to_json.py X.pdf --document-json-out RUN/10_cli_light/X.document.json
    python tools/samples_report.py report --samples DIR --run RUN --thumbnails

The run directory; every stage is optional and one that did not run shows as "—":

    02_inventory/inventory.tsv, .env      `inventory` (the .env holds shell variables for the job)
    10_cli_light/<doc>.document.json      the CLI's record; <doc>.run.json (`measure`), <doc>.err
    11_cli_negative/results.tsv           check, expected, got, ok
    20_cli_docling/<doc>.*                as 10, with `--engine docling`
    30_markdown/<doc>.<detail>.md         `json_to_md.py --detail full|standard|minimal`
    40_service_contract/results.tsv       check, expected, got, ok
    41_reformat/<doc>.reformat.json       `/reformat markdown=true`; <doc>.run.json: http, wall_s,
                                          vmhwm_ready_kb, vmhwm_after_kb (the server's peak memory)
    50_describe_none/<doc>.describe.json  `/describe stages=none`; <doc>.run.json: http, wall_s
    61_describe_stages/<doc>.*            `/describe` with page-classification and ocr-postprocess
    62_describe_all/<doc>.*               `/describe stages=page-classification classify_pages=all`

Image coverage is the share of a page's crop box under the union of its image objects, measured
by `api_util/pdf_coverage.py`: the same helper the light engine applies to a page whose only text
is its running header or footer. Standard library, pypdfium2 and Pillow (pdfplumber's
dependency): all in `requirements_digital.txt`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: The repository root, for `api_util` (this file runs as a script from `tools/`).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The coverage helpers are the light engine's own (its furniture-only rule uses them), so the
# report measures every page exactly as the converter measures the pages it decides on; the
# route rules are the service's own (`/describe`), so the report re-applies exactly them.
from api_util import digital_report as R  # noqa: E402
from api_util.pdf_coverage import image_boxes, page_frame, union_share  # noqa: E402

#: page-classification renders a PDF page at this resolution and refuses the whole call when a
#: page would exceed this many pixels (its PDF_RENDER_DPI and MAX_IMAGE_PIXELS, v1.9.3-beta).
PC_RENDER_DPI = 300
PC_MAX_IMAGE_PIXELS = 178_956_970
#: The other defaults the limits table compares with (each service's tool_limits.py). This
#: repository's upload default is 200 since the #5 run (50 before), its stage timeout 360 since
#: the re-run (120 before).
DEFAULTS = {"upload": 200, "pages": 2000, "stage_timeout": 360, "pc_upload": 10, "pc_pages": 50}
#: ROUTE_DAMAGED_WORD_SHARE's default (tool_limits.py): a `digital` page with this share of its
#: body's words losing a letter (U+FFFD) goes to OCR.
DAMAGED_WORD_SHARE = 0.05
OP_UPLOAD_DEFAULT = 25

#: The candidate rule measured, and the grid of alternatives reported beside it.
COVERAGE_MIN = 0.85
BODY_CHARS_MAX = 200
COVERAGE_STEPS = (0.5, 0.7, 0.85, 0.95)
BODY_CHARS_STEPS = (0, 50, 200, 500, 1000)
MAX_THUMBS = 300
THUMB_DPI = 40

#: Czech letters outside WinAnsi: a producer that sets them in a second font subset made the
#: light engine split every word at them before #5 ("Ji č ín"). A token that is one of these
#: letters alone is that split; Czech has no one-letter word among them.
SPLIT_LETTERS = frozenset("čďěňřťůČĎĚŇŘŤŮ")
#: A glyph without a Unicode mapping: U+FFFD in a record since #5, `(cid:N)` before it.
_UNMAPPED_GLYPH = re.compile(r"\ufffd|\(cid:\d+\)")
#: A page where at least this share of its lines (and 10 or more) are 1-2 characters long is
#: mostly rotated text read one character per line (captions along a landscape page's edge,
#: chart axes), or a column of numbers.
SHORT_LINE_SHARE = 0.5

MIB = 1024 * 1024
DASH = "—"
TEXT_LAYERS = ("digital", "garbled", "ocr", "none", "blank")
DETAILS = ("full", "standard", "minimal")
ROUTES = ("nlp", "ocr", "htr", "none")
PC, OP = "page-classification", "ocr-postprocess"
#: Stage statuses that are not worth listing as a problem (service/stages.py).
QUIET = {"ok", "skipped", "not_requested", "not_configured"}

INVENTORY, CLI, NEGATIVE, DOCLING = (
    "02_inventory",
    "10_cli_light",
    "11_cli_negative",
    "20_cli_docling",
)
MARKDOWN, CONTRACT, REFORMAT = "30_markdown", "40_service_contract", "41_reformat"
DESCRIBE_NONE, DESCRIBE, DESCRIBE_ALL = "50_describe_none", "61_describe_stages", "62_describe_all"

INVENTORY_COLUMNS = (
    "doc file bytes mb pages sha512 pdf_version producer creator max_page_px "
    "pages_over_pc_pixels error"
).split()
SUMMARY_COLUMNS = (
    "doc mb pages producer cli_rc cli_reason cli_s cli_peak_mb record_mb "
    + " ".join(f"tl_{layer}" for layer in TEXT_LAYERS)
    + " needs_ocr needs_ocr_pages needs_ocr_kinds lines tables headings furniture_lines "
    "footnote_lines docling_rc docling_reason docling_s docling_peak_mb docling_lines "
    "docling_tables docling_headings docling_furniture_lines docling_footnote_lines "
    "md_full_chars md_standard_chars md_minimal_chars md_needs_ocr_cues reformat_http "
    "reformat_reason reformat_s server_peak_mb server_growth_mb parity none_http none_reason "
    "none_s none_routes none_doc_route stages_http stages_reason stages_s stages_routes "
    "stages_doc_route stages_pc stages_op stages_bands stages_categories all_http all_reason "
    "all_s all_pc all_categories high_coverage_digital candidates split_letters unmapped_glyphs "
    "unmapped_pages short_line_pages stamped_scans furniture_only_pages lost_letter_pages "
    "qm_digital qm_moved qm_moved_categories qm_moved_lost qm_prose_moves "
    + " ".join(f"qm_trash_{kind.replace(' ', '_')}" for kind in R.LINE_KINDS)
).split()
SIGNAL_COLUMNS = (
    "doc page_index page text_layer needs_ocr lines body_chars furniture_chars furniture_only "
    "text_area_share images image_coverage none_route stages_route stages_route_kind "
    "stages_category stages_category_confidence stages_band stages_judged stages_trash "
    "stages_prose_judged stages_prose_trash prose_rule all_route all_category "
    "all_category_confidence words damaged_words candidate thumbnail split_letters "
    "unmapped_glyphs short_line_share"
).split()
#: The `/describe` runs a page's columns come from, by column prefix.
REPORT_STAGES = (("none", DESCRIBE_NONE), ("stages", DESCRIBE), ("all", DESCRIBE_ALL))


# ── small helpers ───────────────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _num(value: Any) -> Optional[float]:
    """A number from a run.json value (the shell writes strings), or None."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return None if number is None else int(number)


def _r(value: Optional[float], digits: int = 1) -> Any:
    return "" if value is None else round(value, digits)


def _ranges(indices: Iterable[Any], limit: int = 160) -> str:
    """`[1, 2, 3, 7, 9, 10]` → `1-3,7,9-10`, cut at `limit` characters."""
    parts: List[str] = []
    for n in sorted({int(i) for i in indices if isinstance(i, (int, float))}):
        if parts and parts[-1].split("-")[-1] == str(n - 1):
            parts[-1] = f"{parts[-1].split('-')[0]}-{n}"
        else:
            parts.append(str(n))
    text = ",".join(parts)
    return text if len(text) <= limit else text[: limit - 1].rsplit(",", 1)[0] + ",…"


def _histogram(values: Iterable[Any]) -> str:
    counts = Counter(str(v) for v in values if v not in (None, ""))
    return " · ".join(f"{name} {count}" for name, count in counts.most_common())


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    def cell(value: Any) -> str:
        return (DASH if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(lines + ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows])


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))
    except OSError:
        return []


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── inventory ───────────────────────────────────────────────────────────────────────────────


def list_pdfs(samples: Path) -> List[Path]:
    return sorted(p for p in samples.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def pdf_facts(path: Path) -> Dict[str, Any]:
    """One inventory row. A PDF pypdfium2 cannot open still gets a row, with the error."""
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MIB), b""):
            digest.update(chunk)
    size = path.stat().st_size
    facts: Dict[str, Any] = {
        "doc": path.stem,
        "file": path.name,
        "bytes": size,
        "mb": round(size / MIB, 2),
        "sha512": digest.hexdigest(),
    }
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415  (imported on use)

        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # encrypted, corrupt, or no pypdfium2
        facts["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return facts
    try:
        facts["pages"] = len(pdf)
        try:
            meta = pdf.get_metadata_dict()
            facts["pdf_version"] = pdf.get_version()
            for key in ("Producer", "Creator"):
                facts[key.lower()] = " ".join(str(meta.get(key) or "").split())[:80]
        except Exception:
            pass
        scale, largest, over = PC_RENDER_DPI / 72, 0, 0
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                width, height = page.get_size()
            finally:
                page.close()
            pixels = math.ceil(width * scale) * math.ceil(height * scale)
            largest, over = max(largest, pixels), over + (pixels > PC_MAX_IMAGE_PIXELS)
        facts["max_page_px"], facts["pages_over_pc_pixels"] = largest, over
    finally:
        pdf.close()
    return facts


def inventory_env(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the job sets its limits and test inputs from, as shell variables."""
    readable = [r for r in rows if r.get("pages")]
    if not readable:
        return {"N_DOCS": len(rows)}
    largest = max(readable, key=lambda r: float(r["mb"]))
    longest = max(readable, key=lambda r: int(r["pages"]))
    smallest = min(readable, key=lambda r: float(r["mb"]))
    return {
        "N_DOCS": len(rows),
        "LARGEST_FILE": largest["file"],
        "LARGEST_MB": largest["mb"],
        "UPLOAD_MB": max(DEFAULTS["upload"], math.ceil(float(largest["mb"]) / 10) * 10),
        "LONGEST_FILE": longest["file"],
        "MAX_DOC_PAGES": longest["pages"],
        "SMALLEST_FILE": smallest["file"],
        "MAX_PAGE_PX": max(int(r.get("max_page_px") or 0) for r in readable),
    }


def write_inventory(samples: Path, out: Path) -> List[Dict[str, Any]]:
    """`out` (TSV, one row per PDF) and `out` with `.env` (`inventory_env`, shell-quoted)."""
    rows = [pdf_facts(path) for path in list_pdfs(samples)]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, INVENTORY_COLUMNS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    env = "".join(f"{k}={shlex.quote(str(v))}\n" for k, v in inventory_env(rows).items())
    out.with_suffix(".env").write_text(env, encoding="utf-8")
    return rows


# ── measure ─────────────────────────────────────────────────────────────────────────────────


def measure(command: Sequence[str], out: Path, timeout: float = 0.0) -> int:
    """Run `command`; write `{rc, timed_out, wall_s, cpu_s, peak_rss_mb, …}` to `out`.

    The peak is `RUSAGE_CHILDREN`'s, the largest resident set among the waited-for children,
    and this process waits for exactly one command: it is that command's own peak. A command
    still running after `timeout` seconds is killed and recorded as rc 124.
    """
    import resource  # noqa: PLC0415  (POSIX only; `report` does not need it)

    started, timed_out = time.monotonic(), False
    try:
        rc = subprocess.run(list(command), timeout=timeout or None).returncode
    except subprocess.TimeoutExpired:
        rc, timed_out = 124, True
    except OSError as exc:
        print(f"[samples_report] cannot run {command[0]!r}: {exc}", file=sys.stderr)
        rc = 127
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak_kb = usage.ru_maxrss / (1024 if sys.platform == "darwin" else 1)  # bytes on macOS
    result = {
        "rc": rc,
        "timed_out": timed_out,
        "wall_s": round(time.monotonic() - started, 2),
        "cpu_s": round(usage.ru_utime + usage.ru_stime, 2),
        "peak_rss_mb": round(peak_kb / 1024, 1),
        "timeout_s": timeout or None,
        "cmd": list(command),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return rc


# ── reading the run ─────────────────────────────────────────────────────────────────────────

_CLI_REASON = re.compile(r"\[digital-convert\] ([a-z_]+):")


def cli_reason(err: Path, meta: Mapping[str, Any]) -> str:
    """Why a CLI run wrote no record: the converter's reason code, or its last line."""
    rc = _int(meta.get("rc"))
    if rc == 0:
        return ""
    if meta.get("timed_out"):
        return "timeout"
    try:
        text = err.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    found = _CLI_REASON.findall(text)
    if found:
        return found[-1]
    # Exit 2 is any RuntimeError: a missing dependency, but also Docling failing on the file
    # ("Docling could not convert X (OSError: …)"), which all 15 PDFs of the #5 run did. Its
    # own words say which; "dependency_missing" only when there are none.
    said = [
        line.split("[digital-convert]", 1)[1].strip()
        for line in text.splitlines()
        if "[digital-convert]" in line
    ]
    if said:
        return said[-1][:160]
    last = [line.strip() for line in text.splitlines() if line.strip()]
    return "dependency_missing" if rc == 2 else (last[-1] if last else f"exit {rc}")[:120]


def reason_kind(reason: str) -> str:
    """A short name for a `needs_ocr_reason` (the wordings of `digital_to_json.assess_page`)."""
    if reason.startswith("no extractable text layer"):
        head = reason.split("—", 1)[0]
        drawn = "images" if "image" in head else "paths" if "vector path" in head else "nothing"
        return f"no text, draws {drawn}"
    for prefix, kind in (
        ("embedded text layer does not decode", "mojibake"),
        ("garbled text layer", "undecodable characters"),
        ("the text layer is a prior OCR run", "prior OCR layer"),
        ("no text layer under the page furniture", "page number over a scan"),
    ):
        if reason.startswith(prefix):
            return kind
    return reason.split(":", 1)[0][:40] or "unknown"


def record_facts(record: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """The summary columns one record gives; with `prefix` only the structure counts."""
    pages = [p for p in record.get("pages") or [] if isinstance(p, dict)]
    lines = [ln for ln in record.get("lines") or [] if isinstance(ln, dict)]
    regions = Counter((ln.get("style") or {}).get("region") for ln in lines)
    facts: Dict[str, Any] = {
        f"{prefix}lines": len(lines),
        f"{prefix}tables": len(record.get("tables") or []),
        f"{prefix}headings": sum(1 for ln in lines if (ln.get("style") or {}).get("heading_level")),
        f"{prefix}furniture_lines": regions["page_header"] + regions["page_footer"],
        f"{prefix}footnote_lines": regions["footnote"],
    }
    if prefix:
        return facts
    layers = Counter(p.get("text_layer") for p in pages)
    flagged = [p for p in pages if p.get("needs_ocr")]
    kinds = Counter(reason_kind(str(p.get("needs_ocr_reason") or "")) for p in flagged)
    facts.update({f"tl_{layer}": layers.get(layer, 0) for layer in TEXT_LAYERS})
    facts["needs_ocr"] = len(flagged)
    facts["needs_ocr_pages"] = _ranges(p.get("page_index") for p in flagged)
    facts["needs_ocr_kinds"] = "; ".join(f"{k} ×{n}" for k, n in kinds.most_common())
    # The size of the record part `/describe` sends a stage (service/stages.py `_record_part`).
    facts["record_mb"] = round(len(json.dumps(record, ensure_ascii=False).encode()) / MIB, 2)
    return facts


def text_defects(lines: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The #5 text defects in one page's lines: split letters, unmapped glyphs, short lines."""
    texts = [str(line.get("text") or "") for line in lines]
    short = sum(1 for text in texts if len(text.strip()) <= 2)
    return {
        "split_letters": sum(
            1 for text in texts for token in text.split() if token in SPLIT_LETTERS
        ),
        "unmapped_glyphs": sum(len(_UNMAPPED_GLYPH.findall(text)) for text in texts),
        "short_line_share": round(short / len(texts), 3) if len(texts) >= 10 else "",
    }


def plane(record: Mapping[str, Any]) -> Dict[str, list]:
    """What digital-convert writes, without run stamps: what the parity check compares."""
    return {
        "pages": [
            (p.get("page"), p.get("page_index"), p.get("text_layer"), bool(p.get("needs_ocr")))
            for p in record.get("pages") or []
        ],
        "lines": [
            (ln.get("page"), ln.get("line"), ln.get("text"), tuple(ln.get("bbox") or ()))
            for ln in record.get("lines") or []
        ],
        "tables": [
            (t.get("page"), t.get("n_rows"), t.get("n_cols"), len(t.get("cells") or []))
            for t in record.get("tables") or []
        ],
    }


def parity(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    """`same`, or where two records' planes first differ."""
    a, b = plane(left), plane(right)
    for block in ("pages", "lines", "tables"):
        if a[block] != b[block]:
            if len(a[block]) != len(b[block]):
                return f"{block}: {len(a[block])} vs {len(b[block])}"
            pairs = zip(a[block], b[block], strict=True)
            first = next(i for i, (x, y) in enumerate(pairs) if x != y)
            return f"{block}[{first}] differs"
    return "same"


def stage_cell(entry: Optional[Mapping[str, Any]]) -> str:
    if not entry:
        return ""
    elapsed, http = _num(entry.get("elapsed_s")), entry.get("http_status")
    text = str(entry.get("status") or "") + (f" {elapsed:.0f} s" if elapsed is not None else "")
    return text + (f" (http {http})" if http else "")


class Run:
    """The run directory, read one document at a time."""

    def __init__(self, root: Path, samples: Optional[Path] = None):
        self.root, self.samples = root, samples
        self.inventory: List[Dict[str, Any]] = _read_tsv(root / INVENTORY / "inventory.tsv")
        if not self.inventory and samples and samples.is_dir():
            self.inventory = [pdf_facts(path) for path in list_pdfs(samples)]

    def path(self, stage: str, name: str) -> Path:
        return self.root / stage / name

    def json(self, stage: str, name: str) -> Optional[Dict[str, Any]]:
        body = _load_json(self.path(stage, name))
        return body if isinstance(body, dict) else None

    def pdf(self, row: Mapping[str, Any]) -> Optional[Path]:
        path = self.samples / str(row.get("file")) if self.samples else None
        return path if path is not None and path.is_file() else None

    def cli(self, stage: str, doc: str, prefix: str) -> Tuple[Dict[str, Any], Any]:
        """The columns of a `measure`d CLI run, and its record when it wrote one."""
        meta = self.json(stage, f"{doc}.run.json")
        if meta is None:
            return {}, None
        rc = _int(meta.get("rc"))
        columns = {
            f"{prefix}_rc": rc,
            f"{prefix}_reason": cli_reason(self.path(stage, f"{doc}.err"), meta),
            f"{prefix}_s": _r(_num(meta.get("wall_s"))),
            f"{prefix}_peak_mb": _r(_num(meta.get("peak_rss_mb"))),
        }
        return columns, self.json(stage, f"{doc}.document.json") if rc == 0 else None

    def http(self, stage: str, doc: str, prefix: str, kind: str) -> Tuple[Dict[str, Any], Any]:
        """The columns of one HTTP call, and its body when it answered 200."""
        meta = self.json(stage, f"{doc}.run.json")
        if meta is None:
            return {}, None
        http, body = _int(meta.get("http")), self.json(stage, f"{doc}.{kind}.json")
        columns = {f"{prefix}_http": http, f"{prefix}_s": _r(_num(meta.get("wall_s")))}
        if http != 200:
            columns[f"{prefix}_reason"] = (body or {}).get("reason") or str(
                (body or {}).get("detail") or ""
            )[:80]
        return columns, body if http == 200 else None


# ── one document ────────────────────────────────────────────────────────────────────────────


def describe_columns(body: Mapping[str, Any], prefix: str) -> Dict[str, Any]:
    summary = body.get("summary") or {}
    routes = summary.get("routes") or {}
    stages = {s.get("stage"): s for s in body.get("stages") or [] if isinstance(s, dict)}
    pages = [p for p in body.get("pages") or [] if isinstance(p, dict)]
    return {
        f"{prefix}_routes": " · ".join(f"{r} {routes.get(r, 0)}" for r in ROUTES),
        f"{prefix}_doc_route": summary.get("document_route"),
        f"{prefix}_pc": stage_cell(stages.get(PC)),
        f"{prefix}_op": stage_cell(stages.get(OP)),
        f"{prefix}_categories": _histogram((p.get("category") or {}).get("label") for p in pages),
        f"{prefix}_bands": _histogram(
            (p.get("quality") or {}).get("band")
            for p in pages
            if (p.get("quality") or {}).get("source") == OP
        ),
    }


def route_kind(reason: Optional[str]) -> str:
    """Why `/describe` routed a page, in a word: the quality model, lost letters, the text
    layer as it is, the page type, or a blank page."""
    text = str(reason or "")
    if not text:
        return ""
    if text.startswith(R.QUALITY_ROUTE_PREFIX):
        return "quality model"
    if "lose a letter" in text:
        return "lost letters"
    if text.startswith("trustworthy text layer"):
        return "text layer"
    if text.startswith("empty page") or "a blank page" in text:
        return "blank"
    return "page type"


def _judged_counts(counts: Optional[Mapping[str, Any]]) -> Tuple[int, int]:
    """`(judged, trash)`: the Clear/Noisy/Trash lines of a `lines_by_category` count."""
    counts = counts or {}
    judged = sum(int(counts.get(name, 0) or 0) for name in ("Clear", "Noisy", "Trash"))
    return judged, int(counts.get("Trash", 0) or 0)


def _scored_lines(body: Optional[Mapping[str, Any]]) -> Dict[str, List[Mapping[str, Any]]]:
    """The lines of a `/describe` answer's record that ocr-postprocess scored, by page label."""
    by_label: Dict[str, List[Mapping[str, Any]]] = {}
    for line in ((body or {}).get("document_json") or {}).get("lines") or []:
        if isinstance(line, dict) and line.get("categ") in ("Clear", "Noisy", "Trash"):
            by_label.setdefault(str(line.get("page")), []).append(line)
    return by_label


def _prose_quality(page: Mapping[str, Any], scored: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """A page's ocr-postprocess quality with its prose-line counts: as `/describe` gave them
    (since #5's re-run), else counted on the scored record lines the answer carries."""
    quality = dict(page.get("quality") or {})
    if quality.get("source") != OP or "prose_lines_by_category" in quality:
        return quality
    prose = Counter(
        str(line.get("categ"))
        for line in scored
        if R.is_prose_line(str(line.get("text") or ""), line.get("group_id"))
    )
    quality["prose_lines_by_category"] = dict(prose)
    return quality


def _loses_letters(row: Mapping[str, Any], share: float = DAMAGED_WORD_SHARE) -> bool:
    words = _int(row.get("words")) or 0
    return (
        row.get("text_layer") == "digital"
        and bool(words)
        and ((_int(row.get("damaged_words")) or 0) / words >= share)
    )


def page_signals(
    doc: str,
    pdf_path: Optional[Path],
    record: Mapping[str, Any],
    reports: Mapping[str, Optional[Mapping[str, Any]]],
    rule: Tuple[float, int],
    thumbs: Optional[Path] = None,
    budget: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """One row per page of a converted document (SIGNAL_COLUMNS).

    `reports` holds the `/describe` answers by run, `none` (50), `stages` (61) and `all` (62);
    each page's `<run>_…` columns come from that run alone.
    """
    coverage_min, body_chars_max = rule
    by_label: Dict[str, List[Mapping[str, Any]]] = {}
    for line in record.get("lines") or []:
        by_label.setdefault(str(line.get("page")), []).append(line)
    pages = {
        prefix: {
            int(p.get("page_index") or 0): p
            for p in (body or {}).get("pages") or []
            if isinstance(p, dict)
        }
        for prefix, body in ((prefix, reports.get(prefix)) for prefix, _ in REPORT_STAGES)
    }
    scored = _scored_lines(reports.get("stages"))
    pdf = None
    if pdf_path is not None:
        try:
            import pypdfium2 as pdfium  # noqa: PLC0415

            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception as exc:
            print(f"[samples_report] {doc}: no image census ({exc})", file=sys.stderr)
    rows: List[Dict[str, Any]] = []
    try:
        for page_row in record.get("pages") or []:
            index = int(page_row.get("page_index") or 0)
            label = str(page_row.get("page"))
            lines = by_label.get(label, [])
            body_lines = [
                ln
                for ln in lines
                if (ln.get("style") or {}).get("region") not in ("page_header", "page_footer")
            ]
            body = sum(len(str(ln.get("text") or "")) for ln in body_lines)
            area = sum(
                max(b[2] - b[0], 0) * max(b[3] - b[1], 0)
                for b in (ln.get("bbox") or () for ln in lines)
                if len(b) == 4
            )
            canvas = page_row.get("canvas") or {}
            canvas_area = float(canvas.get("width") or 0) * float(canvas.get("height") or 0)
            damaged, words = R.count_damaged_words(str(ln.get("text") or "") for ln in body_lines)
            row: Dict[str, Any] = {
                "doc": doc,
                "page_index": index,
                "page": page_row.get("page"),
                "text_layer": page_row.get("text_layer"),
                "needs_ocr": int(bool(page_row.get("needs_ocr"))),
                "lines": len(lines),
                "body_chars": body,
                "furniture_chars": sum(len(str(ln.get("text") or "")) for ln in lines) - body,
                "furniture_only": int(bool(lines) and not body_lines),
                "text_area_share": round(min(area / canvas_area, 1.0), 4) if canvas_area else "",
                "words": words,
                "damaged_words": damaged,
                "candidate": 0,
                **text_defects(lines),
            }
            for prefix, _ in REPORT_STAGES:
                described = pages[prefix].get(index)
                if described is None:
                    continue
                row[f"{prefix}_route"] = described.get("route")
                if prefix != "none":
                    category = described.get("category") or {}
                    row[f"{prefix}_category"] = category.get("label")
                    row[f"{prefix}_category_confidence"] = _r(_num(category.get("confidence")), 3)
            described = pages["stages"].get(index)
            if described is not None:
                row["stages_route_kind"] = route_kind(described.get("route_reason"))
                quality = _prose_quality(described, scored.get(label, []))
                if quality.get("source") == OP:
                    row["stages_band"] = quality.get("band")
                    row["stages_judged"], row["stages_trash"] = _judged_counts(
                        quality.get("lines_by_category")
                    )
                    row["stages_prose_judged"], row["stages_prose_trash"] = _judged_counts(
                        quality.get("prose_lines_by_category")
                    )
                    if row["text_layer"] == "digital":
                        row["prose_rule"] = int(R.route_page("digital", None, quality)[0] == "ocr")
            page = pdf[index - 1] if pdf is not None and 1 <= index <= len(pdf) else None
            if page is not None:
                try:
                    boxes = image_boxes(page)
                    row["images"] = len(boxes)
                    row["image_coverage"] = round(union_share(boxes, page_frame(page)), 4)
                except Exception as exc:
                    print(f"[samples_report] {doc} p{index}: {exc}", file=sys.stderr)
            row["candidate"] = int(
                row["text_layer"] == "digital"
                and row.get("image_coverage", -1) >= coverage_min
                and body <= body_chars_max
            )
            # candidates.md shows these pages: render them while the budget lasts.
            shown = row["candidate"] or row["furniture_only"]
            if shown and thumbs is not None and page is not None and budget and budget[0]:
                try:
                    thumbs.mkdir(parents=True, exist_ok=True)
                    name = f"{doc}_p{index}.png"
                    page.render(scale=THUMB_DPI / 72).to_pil().save(thumbs / name)
                    row["thumbnail"], budget[0] = f"thumbs/{name}", budget[0] - 1
                except Exception as exc:
                    print(f"[samples_report] {doc} p{index}: no thumbnail ({exc})", file=sys.stderr)
            if page is not None:
                page.close()
            rows.append(row)
    finally:
        if pdf is not None:
            pdf.close()
    return rows


def quality_columns(
    stages: Optional[Mapping[str, Any]], signals: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    """What ocr-postprocess's quality model did to the `digital` pages of run 61 (#5): the
    pages it moved to OCR, their page types, its Trash lines by kind (`digital_report.line_kind`),
    and what the prose rule moves. Empty when the model did not score the document."""
    digital = [s for s in signals if s.get("text_layer") == "digital"]
    if not stages or not any(s.get("stages_band") for s in digital):
        return {}
    moved = [s for s in digital if s.get("stages_route_kind") == "quality model"]
    labels = {str(s.get("page")) for s in digital}
    kinds: Counter = Counter()
    for label, lines in _scored_lines(stages).items():
        if label in labels:
            kinds.update(
                R.line_kind(str(ln.get("text") or ""), ln.get("group_id"))
                for ln in lines
                if ln.get("categ") == "Trash"
            )
    columns: Dict[str, Any] = {
        "qm_digital": len(digital),
        "qm_moved": len(moved),
        "qm_moved_categories": _histogram(
            s.get("all_category") or s.get("stages_category") for s in moved
        ),
        "qm_moved_lost": sum(1 for s in digital if s.get("stages_route_kind") == "lost letters"),
        "qm_prose_moves": sum(_int(s.get("prose_rule")) or 0 for s in digital),
    }
    for kind in R.LINE_KINDS:
        columns[f"qm_trash_{kind.replace(' ', '_')}"] = kinds.get(kind, 0)
    return columns


def deliver(out: Path, doc: str, bodies: Mapping[str, Any]) -> List[str]:
    """The JSONs promised in #5: the best record and a `/describe` report, per document."""
    stages = bodies.get(DESCRIBE) or {}
    adopted = any(
        s.get("record_adopted") for s in stages.get("stages") or [] if isinstance(s, dict)
    )
    record, record_from = None, ""
    for stage, ok in ((DESCRIBE, adopted), (REFORMAT, True), (CLI, True)):
        body = bodies.get(stage)
        if ok and body:
            record, record_from = (body if stage == CLI else body.get("document_json")), stage
            break
    report_from = next((s for s in (DESCRIBE, DESCRIBE_NONE) if bodies.get(s)), "")
    if record or report_from:
        out.mkdir(parents=True, exist_ok=True)
    if record:
        text = json.dumps(record, ensure_ascii=False)
        (out / f"{doc}.document.json").write_text(text, encoding="utf-8")
    if report_from:
        report = {k: v for k, v in bodies[report_from].items() if k != "document_json"}
        text = json.dumps(report, ensure_ascii=False)
        (out / f"{doc}.describe.json").write_text(text, encoding="utf-8")
    return [doc, record_from, report_from]


def document_row(
    run: Run, inv: Mapping[str, Any], rule: Tuple[float, int], out: Path, budget: List[int]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[List[Any]], List[Any]]:
    """(summary row, page signals, stage problems, delivery row) of one document."""
    doc = str(inv.get("doc"))
    row: Dict[str, Any] = {k: inv.get(k) for k in ("doc", "mb", "pages")}
    row["producer"] = inv.get("producer") or inv.get("creator")
    columns, record = run.cli(CLI, doc, "cli")
    row.update(columns)
    if record:
        row.update(record_facts(record))
    columns, docling = run.cli(DOCLING, doc, "docling")
    row.update(columns)
    if docling:
        row.update(record_facts(docling, prefix="docling_"))
    for detail in DETAILS:
        md = run.path(MARKDOWN, f"{doc}.{detail}.md")
        if md.is_file():
            text = md.read_text(encoding="utf-8", errors="replace")
            row[f"md_{detail}_chars"] = len(text)
            if detail == "full":
                row["md_needs_ocr_cues"] = text.count("<!-- NEEDS_OCR")

    bodies: Dict[str, Any] = {CLI: record}
    for stage, prefix, kind in (
        (REFORMAT, "reformat", "reformat"),
        (DESCRIBE_NONE, "none", "describe"),
        (DESCRIBE, "stages", "describe"),
        (DESCRIBE_ALL, "all", "describe"),
    ):
        columns, bodies[stage] = run.http(stage, doc, prefix, kind)
        row.update(columns)
        if bodies[stage] and kind == "describe":
            row.update(describe_columns(bodies[stage], prefix))
    meta = run.json(REFORMAT, f"{doc}.run.json") or {}
    ready, after = _num(meta.get("vmhwm_ready_kb")), _num(meta.get("vmhwm_after_kb"))
    if after is not None:
        row["server_peak_mb"] = _r(after / 1024)
        if ready is not None:
            row["server_growth_mb"] = _r((after - ready) / 1024)
    if bodies[REFORMAT] and record:
        row["parity"] = parity(record, bodies[REFORMAT].get("document_json") or {})

    problems = [
        [doc, stage, s.get("stage"), stage_cell(s), str(s.get("detail") or "")[:200]]
        for stage in (DESCRIBE, DESCRIBE_ALL)
        for s in (bodies[stage] or {}).get("stages") or []
        if isinstance(s, dict) and s.get("status") not in QUIET
    ]
    signals: List[Dict[str, Any]] = []
    if record:
        reports = {prefix: bodies[stage] for prefix, stage in REPORT_STAGES}
        thumbs = out / "thumbs" if budget[1] else None
        signals = page_signals(doc, run.pdf(inv), record, reports, rule, thumbs, budget)
        row["candidates"] = sum(s["candidate"] for s in signals)
        row["split_letters"] = sum(s["split_letters"] for s in signals)
        row["unmapped_glyphs"] = sum(s["unmapped_glyphs"] for s in signals)
        row["unmapped_pages"] = sum(1 for s in signals if s["unmapped_glyphs"])
        row["stamped_scans"] = sum(1 for s in signals if s["text_layer"] == "none" and s["lines"])
        row["short_line_pages"] = sum(
            1 for s in signals if (_num(s["short_line_share"]) or 0) >= SHORT_LINE_SHARE
        )
        row["high_coverage_digital"] = sum(
            1
            for s in signals
            if s["text_layer"] == "digital" and s.get("image_coverage", -1) >= rule[0]
        )
        row["furniture_only_pages"] = sum(1 for s in signals if s["furniture_only"])
        row["lost_letter_pages"] = sum(1 for s in signals if _loses_letters(s))
        row.update(quality_columns(bodies[DESCRIBE], signals))
    return row, signals, problems, deliver(out / "deliver", doc, bodies)


# ── the report ──────────────────────────────────────────────────────────────────────────────


def build_report(
    run_dir: Path,
    samples: Optional[Path] = None,
    thumbnails: bool = False,
    coverage_min: float = COVERAGE_MIN,
    body_chars_max: int = BODY_CHARS_MAX,
) -> Path:
    """Write `<run>/report/` (see the module docstring); return the summary's path."""
    run, out = Run(run_dir, samples), run_dir / "report"
    out.mkdir(parents=True, exist_ok=True)
    rule, budget = (coverage_min, body_chars_max), [MAX_THUMBS, int(thumbnails)]
    rows, signals, problems, delivered = [], [], [], []
    for inv in run.inventory:
        row, page_rows, doc_problems, delivery = document_row(run, inv, rule, out, budget)
        rows.append(row)
        signals += page_rows
        problems += doc_problems
        delivered.append(delivery)
    _write_csv(out / "summary.csv", SUMMARY_COLUMNS, rows)
    _write_csv(out / "page_signals.csv", SIGNAL_COLUMNS, signals)
    (out / "candidates.md").write_text(candidates_md(signals, rule), encoding="utf-8")
    (out / "limits.md").write_text(limits_md(run, rows), encoding="utf-8")
    if (out / "deliver").is_dir():
        (out / "deliver" / "README.md").write_text(
            "# The records and reports of the run\n\n`<doc>.document.json` is the document's "
            "`atrium_document` record: from `/describe` with the stages when a stage's write was "
            "adopted, else from `/reformat`, else from the command line. `<doc>.describe.json` is "
            "the per-page assessment (`pages`, `summary`, `stages`, `paradata`) without the record "
            "it embeds.\n\n"
            + _md_table(("document", "record from", "report from"), delivered)
            + "\n",
            encoding="utf-8",
        )
    summary = out / "summary.md"
    summary.write_text(summary_md(run, rows, signals, problems, rule), encoding="utf-8")
    return summary


def candidates_md(rows: Sequence[Mapping[str, Any]], rule: Tuple[float, int]) -> str:
    """The pages whose only text is furniture or a little text over an image (#5)."""
    digital = [r for r in rows if r.get("text_layer") == "digital" and "image_coverage" in r]
    grid = [
        [f"≥ {cov:g}"]
        + [
            sum(r["image_coverage"] >= cov and r["body_chars"] <= c for r in digital)
            for c in BODY_CHARS_STEPS
        ]
        + [sum(r["image_coverage"] >= cov for r in digital)]
        for cov in COVERAGE_STEPS
    ]
    flagged = [r for r in rows if r.get("furniture_only") and r.get("text_layer") == "none"]
    near = [r for r in rows if r.get("furniture_only") and r.get("text_layer") == "digital"]
    picked = [r for r in rows if r.get("candidate")]
    headers = (
        "document", "page", "index", "coverage", "body chars", "header/footer chars",
        "page type", "route", "run", "thumbnail",
    )  # fmt: skip

    def described(r: Mapping[str, Any]) -> Tuple[Any, Any, str]:
        """Page type and route from ONE `/describe` run: 62 (every page classified), else 61,
        else 50 (no page type)."""
        for prefix, stage in (("all", DESCRIBE_ALL), ("stages", DESCRIBE)):
            if r.get(f"{prefix}_route"):
                return r.get(f"{prefix}_category"), r.get(f"{prefix}_route"), stage[:2]
        return None, r.get("none_route"), DESCRIBE_NONE[:2] if r.get("none_route") else ""

    def listing(selected: Sequence[Mapping[str, Any]]) -> str:
        return _md_table(
            headers,
            [
                [r["doc"], r["page"], r["page_index"], r.get("image_coverage"), r["body_chars"],
                 r["furniture_chars"], *described(r),
                 f"![]({r['thumbnail']})" if r.get("thumbnail") else ""]
                for r in selected
            ],
        )  # fmt: skip

    return (
        "# Pages whose only text is furniture, or a little text over an image\n\n"
        "A scanned page that the PDF's producer numbered or stamped has a text layer: that number "
        "or stamp (atrium-digital-convert#5). The light engine makes such a page `none`, with "
        "`needs_ocr`, when its only text is a running header or footer and its images cover at "
        f"least `FURNITURE_ONLY_IMAGE_COVERAGE` of it ({rule[0]:g} here).\n\n"
        f"**Flagged by that rule:** {len(flagged)} page(s).\n\n"
        + listing(flagged)
        + f"\n\n**Near misses, only furniture under the threshold:** {len(near)} `digital` page(s). "
        "`/describe` asks page-classification about them too, and routes them by page type: a "
        "type that carries text (a drawing with a legend, a page of text) sends the page to "
        "OCR, a figure keeps its route.\n\n"
        + listing(near)
        + f"\n\n**A little text over a full-page image:** `text_layer = digital`, image coverage "
        f"≥ {rule[0]:g}, at most {rule[1]} body characters (the lines outside the running header "
        f"and footer): {len(picked)} of {len(digital)} `digital` pages. No rule acts on these.\n\n"
        + listing(picked)
        + "\n\n**Other thresholds:** `digital` pages per coverage floor (rows) and body-character "
        "ceiling (columns); the last column ignores the characters.\n\n"
        + _md_table(["coverage", *(f"≤ {c}" for c in BODY_CHARS_STEPS), "any"], grid)
        + "\n"
    )


def limits_md(run: Run, rows: Sequence[Mapping[str, Any]]) -> str:
    def top(key: str, source: Iterable[Mapping[str, Any]] = run.inventory) -> float:
        return max((_num(r.get(key)) or 0 for r in source), default=0.0)

    largest, max_pages, max_px = top("mb"), int(top("pages")), int(top("max_page_px"))
    record, flagged = top("record_mb", rows), int(top("needs_ocr", rows))
    over = sum(_int(r.get("pages_over_pc_pixels")) or 0 for r in run.inventory)
    upload = max(DEFAULTS["upload"], math.ceil(largest / 10) * 10)
    stage_calls = [
        (_num(s.get("elapsed_s")), str(s.get("status")), str(s.get("detail") or ""))
        for name in (DESCRIBE, DESCRIBE_ALL)
        for path in sorted((run.root / name).glob("*.describe.json"))
        for s in (_load_json(path) or {}).get("stages") or []
        if isinstance(s, dict)
    ]
    answered = [t for t, status, _ in stage_calls if t is not None and status in ("ok", "partial")]
    timed_out = [
        d for _, status, d in stage_calls if status == "unavailable" and "STAGE_TIMEOUT_S" in d
    ]
    table = [
        ["digital-convert", "MAX_UPLOAD_MB", DEFAULTS["upload"], upload, "the largest file, rounded up to 10 MB"],
        ["digital-convert", "MAX_PAGES", DEFAULTS["pages"], max(DEFAULTS["pages"], math.ceil(max_pages / 500) * 500),
         "unchanged unless a document has more pages"],
        ["digital-convert", "STAGE_TIMEOUT_S", DEFAULTS["stage_timeout"],
         max(DEFAULTS["stage_timeout"], math.ceil(max(answered) * 1.5 / 60) * 60) if answered else "",
         f"the slowest stage call ({max(answered):.0f} s) × 1.5" if answered else "needs runs 61/62"],
        ["page-classification", "MAX_UPLOAD_MB", DEFAULTS["pc_upload"],
         max(DEFAULTS["pc_upload"], math.ceil(max(largest, record) / 10) * 10),
         "per part: the PDF, and the record `/describe` sends with it"],
        ["page-classification", "MAX_PDF_PAGES", DEFAULTS["pc_pages"], max(DEFAULTS["pc_pages"], flagged),
         f"pages classified in one call: the most flagged pages; {max(50, max_pages)} for `classify_pages=all`"],
        ["page-classification", "MAX_IMAGE_PIXELS", f"{PC_MAX_IMAGE_PIXELS:,}",
         f"{max(PC_MAX_IMAGE_PIXELS, max_px):,}",
         f"{over} page(s) over the default, and one such page fails the whole call; the largest "
         f"page renders to {max_px * 3 / MIB:,.0f} MB"],
        ["ocr-postprocess", "MAX_UPLOAD_MB", OP_UPLOAD_DEFAULT,
         max(OP_UPLOAD_DEFAULT, math.ceil(record / 10) * 10), "the record part of `/score_record`"],
    ]  # fmt: skip
    text = [
        "# Limits the run suggests",
        "",
        f"Largest file {largest:g} MB, most pages {max_pages}, largest page {max_px:,} px at "
        f"{PC_RENDER_DPI} dpi, largest record {record:g} MB (as `/describe` sends it to a stage), "
        f"most flagged pages in one document {flagged}.",
        "",
        _md_table(("service", "setting", "default", "suggested", "why"), table),
    ]
    # Per job, not per MB of file: the #5 run showed a conversion's peak following the text it
    # reads (a 4.8 MB, 178-page report peaked highest), so a ratio to the file size misleads.
    peaks = [r for r in rows if _num(r.get("server_peak_mb")) or _num(r.get("cli_peak_mb"))]
    if peaks:
        key = (
            "server_peak_mb" if any(_num(r.get("server_peak_mb")) for r in peaks) else "cli_peak_mb"
        )
        worst = max(peaks, key=lambda r: _num(r.get(key)) or 0)
        biggest = max(peaks, key=lambda r: _num(r.get("mb")) or 0)
        peak = _num(worst.get(key)) or 0
        budget = math.ceil(peak / 512) / 2
        where = (
            "the server's peak (a fresh server per document)"
            if key == "server_peak_mb"
            else "the command line's peak"
        )
        text += [
            "",
            f"**Memory (measured).** The highest was {where}: {peak:,.0f} MB, for {worst.get('doc')} "
            f"({worst.get('mb')} MB, {worst.get('pages')} pages, {worst.get('lines') or '?'} lines). "
            f"The largest file, {biggest.get('doc')} ({biggest.get('mb')} MB), peaked at "
            f"{_num(biggest.get(key)) or 0:,.0f} MB: memory follows the text a document holds, not "
            f"its size. Budget about {budget:g} GB plus the upload limit per concurrent job "
            f"(`MAX_CONCURRENT_JOBS`).",
        ]
    docling = [r for r in rows if r.get("docling_rc") == 0]
    if docling:
        seconds = [_num(r.get("docling_s")) or 0 for r in docling]
        peaks_gb = [(_num(r.get("docling_peak_mb")) or 0) / 1000 for r in docling]
        gpu = next(
            (line.split(":", 1)[1].strip() for line in _env_lines(run) if line.startswith("gpu:")),
            "",
        )
        text += [
            "",
            f"**Docling (`--engine docling`, stage 20).** {len(docling)} of {len(rows)} documents "
            f"converted, in {min(seconds):g}–{max(seconds):g} s each, peaking at "
            f"{min(peaks_gb):.1f}–{max(peaks_gb):.1f} GB{f' (GPU: {gpu})' if gpu else ''}. The service "
            "image runs the light engine only; budget Docling per job when a batch runs it.",
        ]
    if timed_out:
        text += [
            "",
            f"**Timeouts.** {len(timed_out)} stage call(s) hit STAGE_TIMEOUT_S, so the "
            "suggestion above is a lower bound: raise the timeout and re-run 61/62.",
        ]
    return "\n".join(text) + "\n"


#: The summary's tables: (title, the column that says the stage ran, [(header, key), …]).
SECTIONS: Tuple[Tuple[str, str, Tuple[Tuple[str, str], ...]], ...] = (
    (
        "Documents and text layers (command line, light engine)",
        "cli_rc",
        (("document", "doc"), ("MB", "mb"), ("pages", "pages"), ("producer", "producer"),
         ("exit", "cli_exit"), ("s", "cli_s"), ("peak MB", "cli_peak_mb"),
         ("digital", "tl_digital"), ("garbled", "tl_garbled"), ("ocr", "tl_ocr"),
         ("none", "tl_none"), ("needs_ocr pages", "needs_ocr_pages"), ("why", "needs_ocr_kinds")),
    ),
    (
        "Text defects (command line, light engine)",
        "cli_rc",
        (("document", "doc"), ("split letters", "split_letters"),
         ("unmapped glyphs", "unmapped_glyphs"), ("pages with them", "unmapped_pages"),
         ("garbled pages", "tl_garbled"), ("page number over a scan", "stamped_scans"),
         ("pages of 1-2 character lines", "short_line_pages")),
    ),
    (
        "Light engine / Docling",
        "docling_rc",
        (("document", "doc"), ("exit", "cmp_exit"), ("s", "cmp_s"), ("peak MB", "cmp_peak_mb"),
         ("lines", "cmp_lines"), ("tables", "cmp_tables"), ("headings", "cmp_headings"),
         ("header/footer lines", "cmp_furniture_lines"), ("footnote lines", "cmp_footnote_lines")),
    ),
    (
        "The service: `/reformat` and the Markdown",
        "reformat_http",
        (("document", "doc"), ("http", "reformat_cell"), ("s", "reformat_s"),
         ("server peak MB", "server_peak_mb"), ("growth MB", "server_growth_mb"),
         ("CLI = service", "parity"), ("record MB", "record_mb"), ("md full chars", "md_full_chars"),
         ("md standard chars", "md_standard_chars"), ("md minimal chars", "md_minimal_chars"),
         ("NEEDS_OCR cues / flagged pages", "cues")),
    ),
    (
        "`/describe`: routes and stages",
        "none_http",
        (("document", "doc"), ("converter only", "none_cell"), ("with stages", "stages_cell"),
         ("s", "stages_s"), ("document route", "doc_route"), ("page-classification", "stages_pc"),
         ("ocr-postprocess", "stages_op"), ("quality bands", "stages_bands"),
         ("categories, all pages", "all_cell")),
    ),
    (
        "Quality model on born-digital text",
        "qm_digital",
        (("document", "doc"), ("digital pages", "qm_digital"),
         ("moved to OCR by the quality model", "qm_moved"), ("their page types", "qm_moved_categories"),
         ("Trash lines: table · short · few letters · dot leaders · prose", "qm_trash"),
         ("the prose rule moves", "qm_prose_moves"),
         ("moved for lost letters", "qm_moved_lost"),
         ("pages losing letters", "lost_letter_pages")),
    ),
)  # fmt: skip

#: Under the "Quality model" table: what its columns count.
QUALITY_NOTE = (
    "Counted on the `digital` pages of `61_describe_stages`, as that run routed them. A line is "
    "prose when it is outside a table, at least 20 characters long, at least half letters and "
    'not a table-of-contents line (`api_util/digital_report.py`); "the prose rule moves" are '
    'the pages whose prose lines (5 or more) the model calls more than half Trash. "Pages losing '
    'letters": at least {share:.0%} of the words of the body text hold U+FFFD (a glyph without '
    "a Unicode mapping), measured on the command line's record; `/describe` sends them to OCR."
)


def _display(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The composite cells of the summary tables."""

    def joined(*keys: str) -> str:
        values = [row.get(k) for k in keys]
        return " ".join(str(v) for v in values if v not in (None, ""))

    def pair(key: str) -> str:
        light, heavy = row.get(f"cli_{key}", row.get(key)), row.get(f"docling_{key}")
        return (
            f"{DASH if light in (None, '') else light} / {DASH if heavy in (None, '') else heavy}"
        )

    out = dict(row)
    out["cli_exit"], out["reformat_cell"] = (
        joined("cli_rc", "cli_reason"),
        joined("reformat_http", "reformat_reason"),
    )
    out["cmp_exit"] = (
        f"{joined('cli_rc', 'cli_reason') or DASH} / {joined('docling_rc', 'docling_reason') or DASH}"
    )
    for key in ("s", "peak_mb", "lines", "tables", "headings", "furniture_lines", "footnote_lines"):
        out[f"cmp_{key}"] = pair(key)
    if row.get("qm_digital") not in (None, ""):
        out["qm_trash"] = " · ".join(
            str(row.get(f"qm_trash_{kind.replace(' ', '_')}") or 0) for kind in R.LINE_KINDS
        )
    if row.get("md_needs_ocr_cues") not in (None, ""):
        out["cues"] = f"{row['md_needs_ocr_cues']} / {row.get('needs_ocr', DASH)}"
    for prefix in ("none", "stages", "all"):
        ran = row.get(f"{prefix}_http") not in (None, "")
        good = row.get(f"{prefix}_http") == 200
        detail = row.get(f"{prefix}_categories" if prefix == "all" else f"{prefix}_routes")
        out[f"{prefix}_cell"] = (
            detail if good else joined(f"{prefix}_http", f"{prefix}_reason") if ran else ""
        )
    out["doc_route"] = row.get("stages_doc_route") or row.get("none_doc_route")
    return out  # fmt: skip


def _env_lines(run: Run) -> List[str]:
    """The lines of the run's `env.txt` the report quotes: job, node, GPU, Python, settings, heads."""
    env = run.root / "env.txt"
    lines = env.read_text(encoding="utf-8", errors="replace").splitlines() if env.is_file() else []
    keep = ("job", "node", "gpu", "python", "settings")
    return [line for line in lines if line.split(":", 1)[0] in keep or line.startswith("atrium-")]


def summary_md(
    run: Run,
    rows: Sequence[Mapping[str, Any]],
    signals: Sequence[Mapping[str, Any]],
    problems: Sequence[Sequence[Any]],
    rule: Tuple[float, int],
) -> str:
    env_lines = _env_lines(run)
    pages = sum(_int(r.get("pages")) or 0 for r in rows)
    size = sum(_num(r.get("mb")) or 0 for r in rows)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# digital-convert over the sample PDFs (atrium-digital-convert#5)",
        "",
        f"Run `{run.root}` · report {stamp} · {len(rows)} documents, {pages} pages, {size:,.1f} MB.",
    ]
    if env_lines:
        out += ["", *(f"* {line}" for line in env_lines)]
    shown = [_display(r) for r in rows]
    for title, ran, columns in SECTIONS:
        out += ["", f"## {title}", ""]
        if not any(r.get(ran) not in (None, "") for r in rows):
            out.append("Not run.")
            continue
        out.append(
            _md_table([h for h, _ in columns], [[r.get(k) for _, k in columns] for r in shown])
        )
        if title.startswith("`/describe`") and problems:
            out += ["", "Stage answers that were not `ok`:", ""]
            out.append(_md_table(("document", "run", "stage", "status", "detail"), problems))
        if title.startswith("Quality model"):
            out += ["", QUALITY_NOTE.format(share=DAMAGED_WORD_SHARE)]
    for title, stage in (("Command-line refusals", NEGATIVE), ("Service contract", CONTRACT)):
        checks = _read_tsv(run.root / stage / "results.tsv")
        if checks:
            out += ["", f"## {title} (`{stage}`)", ""]
            keys = ("check", "expected", "got", "ok")
            out.append(_md_table(keys, [[c.get(k) for k in keys] for c in checks]))
    digital = sum(1 for s in signals if s.get("text_layer") == "digital")
    picked = sum(1 for s in signals if s.get("candidate"))
    flagged = sum(1 for s in signals if s.get("furniture_only") and s.get("text_layer") == "none")
    near = sum(1 for s in signals if s.get("furniture_only") and s.get("text_layer") == "digital")
    failed = [
        r["doc"]
        for r in rows
        if r.get("cli_rc") not in (None, "", 0)
        or any(
            r.get(f"{p}_http") not in (None, "", 200) for p in ("reformat", "none", "stages", "all")
        )
    ]
    docling_failed = Counter(
        str(r.get("docling_reason") or f"exit {r.get('docling_rc')}")
        for r in rows
        if r.get("docling_rc") not in (None, "", 0)
    )
    out += [
        "",
        "## Pages whose only text is furniture, or little text over an image",
        "",
        f"{flagged} page(s) are `none` by the furniture-only rule (their only text a running "
        f"header or footer over images covering ≥ {rule[0]:g} of the page); {near} `digital` "
        "page(s) carry only furniture under that coverage (routed by their page type); and "
        f"{picked} of {digital} `digital` pages have image coverage ≥ {rule[0]:g} and at most "
        f"{rule[1]} body characters. [candidates.md](candidates.md) lists them with thumbnails, "
        "page type and route, and the counts under other thresholds; "
        "[page_signals.csv](page_signals.csv) has every page.",
        "",
        "## Elsewhere",
        "",
        "* [limits.md](limits.md): the limits the run suggests for the pilot;",
        "* [summary.csv](summary.csv): these tables and more columns, one row per document;",
        "* `deliver/`: one record and one `/describe` report per document (see its README);",
        f"* `{MARKDOWN}/detail_budget.md`: what each Markdown profile costs (when stage 30 ran).",
    ]
    if failed:
        out += ["", f"**Refused or failed somewhere:** {', '.join(failed)}."]
    if docling_failed:
        out += [
            "",
            f"**Docling wrote no record for {sum(docling_failed.values())} document(s):** "
            + "; ".join(f"{reason} ×{n}" for reason, n in docling_failed.most_common())
            + ".",
        ]
    return "\n".join(out) + "\n"


# ── command line ────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="samples_report.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="size, pages, digest and producer of each PDF")
    inv.add_argument("--samples", type=Path, required=True, help="the folder of PDFs")
    inv.add_argument("--out", type=Path, required=True, help="the TSV to write (and its .env)")
    mea = sub.add_parser("measure", help="run one command; record rc, time and peak memory")
    mea.add_argument("--out", type=Path, required=True, help="the JSON to write")
    mea.add_argument("--timeout", type=float, default=0.0, help="seconds; 0 = none")
    mea.add_argument("cmd", nargs=argparse.REMAINDER, help="-- the command and its arguments")
    rep = sub.add_parser("report", help="the per-document table, page signals, limits, delivery")
    rep.add_argument("--run", type=Path, required=True, help="the run directory")
    rep.add_argument("--samples", type=Path, default=None, help="the folder of PDFs")
    rep.add_argument("--thumbnails", action="store_true", help="render the candidate pages")
    rep.add_argument("--coverage-min", type=float, default=COVERAGE_MIN)
    rep.add_argument("--body-chars-max", type=int, default=BODY_CHARS_MAX)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inventory":
        if not args.samples.is_dir():
            print(f"[samples_report] no such folder: {args.samples}", file=sys.stderr)
            return 2
        rows = write_inventory(args.samples, args.out)
        print(f"[samples_report] {len(rows)} PDF(s) → {args.out}")
        return 0 if rows else 2
    if args.command == "measure":
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            print("[samples_report] measure: no command after --", file=sys.stderr)
            return 2
        return measure(command, args.out, args.timeout)
    summary = build_report(
        args.run, args.samples, args.thumbnails, args.coverage_min, args.body_chars_max
    )
    print(f"[samples_report] report → {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
