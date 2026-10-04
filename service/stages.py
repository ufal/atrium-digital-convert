"""service/stages.py — the adjacent stages `POST /describe` calls, and the guard on what they return.

`/reformat` calls no other service: in the AMČR deployment Temporal chains the stages
(atrium-digital-convert#1, 30 September). `/describe` (#2, 2026-10-04) is the optional endpoint
that converts a document AND asks the two stages that can describe its pages:

  * **page-classification** — `POST /predict_document` with the PDF, the record and the pages to
    classify (`pages=` page indices; by default the pages without a usable text layer). It
    answers with each page's categories and, given the record, writes `page_categories` and
    `pages[].category/category_confidence` into it under its own stamp.
  * **ocr-postprocess** — `POST /score_record` with the record (W3, agreed on #4): the common
    line-quality model scores the converter's lines and writes only `lines[].categ/
    quality_score/lang` and `pages[].quality_score/quality_band`, never over a line that carries
    the converter's own decode verdict (`Garbage`/`Inverted`). An older ocr-postprocess without
    that endpoint is asked per page through `POST /process` (`task_type=text`) instead; its
    answer then informs the report only.

Every stage is optional and every failure is contained:

  * a stage whose URL is not set (`PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`) is
    `not_configured`; one the request excluded is `not_requested`; one with nothing to do is
    `skipped` (no PDF, no page to classify, no line to score);
  * a connection error, a timeout (`STAGE_TIMEOUT_S`), a 429/5xx is `unavailable`; an
    unexpected 4xx or payload is `error`; a record that fails the guard below is `rejected`;
  * nothing raises past this module: `/describe` still answers 200 with the converter's record
    and the stage's status.

**The guard.** A stage's returned record is adopted only when it is the same document with the
converter's part intact: the same `doc_id` and `source`, the same `content` and `tables`, the
same page and line rows (no row added, none dropped), and the converter's own fields unchanged
(pages: `page_index`, `canvas`, `needs_ocr`, `needs_ocr_reason`; lines: `text`, `bbox`,
`group_id`, `style`). That is what catches an older page-classification that keys pages by
physical number on a PDF whose pages carry labels ("iv", "A-1"): its categories still reach the
report, its record write does not reach the record.

`requests` is the HTTP client (already a base requirement); every call runs in the request's
worker thread, never on the event loop.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from api_util.digital_report import summarize_quality

PAGE_CLASSIFICATION = "page-classification"
OCR_POSTPROCESS = "ocr-postprocess"
#: In the order `/describe` runs them: categories first, so the scorer sees the final page rows.
STAGES: Tuple[str, ...] = (PAGE_CLASSIFICATION, OCR_POSTPROCESS)

#: The environment variable that names each stage's base URL (e.g. `http://page-classification:8000`).
URL_ENV: Dict[str, str] = {
    PAGE_CLASSIFICATION: "PAGE_CLASSIFICATION_URL",
    OCR_POSTPROCESS: "OCR_POSTPROCESS_URL",
}

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_NOT_REQUESTED = "not_requested"
STATUS_SKIPPED = "skipped"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"
STATUS_REJECTED = "rejected"
STATUSES: Tuple[str, ...] = (
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_NOT_CONFIGURED,
    STATUS_NOT_REQUESTED,
    STATUS_SKIPPED,
    STATUS_UNAVAILABLE,
    STATUS_ERROR,
    STATUS_REJECTED,
)

#: The converter's fields in rows other stages also write: the guard holds them unchanged.
CONVERTER_PAGE_FIELDS: Tuple[str, ...] = ("page_index", "canvas", "needs_ocr", "needs_ocr_reason")
CONVERTER_LINE_FIELDS: Tuple[str, ...] = ("text", "bbox", "group_id", "style")

#: Short timeout for the best-effort `/info` probe that reads a stage's version.
_INFO_TIMEOUT_S = 5.0


@dataclass
class StageOutcome:
    """What one stage did for one `/describe` call (the response's `stages[]`)."""

    stage: str
    status: str
    detail: str = ""
    url: Optional[str] = None
    http_status: Optional[int] = None
    reason: Optional[str] = None
    elapsed_s: Optional[float] = None
    service_version: Optional[str] = None
    pages: Optional[List[int]] = None
    record_adopted: bool = False
    paradata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StageResult:
    """A stage's outcome plus what it contributed: per-page facts and maybe a record."""

    outcome: StageOutcome
    per_page: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    record: Optional[Dict[str, Any]] = None


class _Unavailable(Exception):
    def __init__(self, detail: str, http_status: Optional[int] = None):
        super().__init__(detail)
        self.detail = detail
        self.http_status = http_status


class _Failed(Exception):
    def __init__(
        self, detail: str, http_status: Optional[int] = None, reason: Optional[str] = None
    ):
        super().__init__(detail)
        self.detail = detail
        self.http_status = http_status
        self.reason = reason


class _Missing(Exception):
    """The endpoint does not exist on this server (404/405): an older version."""

    def __init__(self, http_status: int):
        super().__init__(str(http_status))
        self.http_status = http_status


# ── configuration ───────────────────────────────────────────────────────────────────────────


def stage_url(stage: str) -> Optional[str]:
    """The configured base URL of `stage`, without a trailing slash, or None.

    Each variable is read by its literal name (not through `URL_ENV`) so that
    tests/test_env_contract.py's source scan sees both reads.
    """
    raw = {
        PAGE_CLASSIFICATION: os.environ.get("PAGE_CLASSIFICATION_URL"),
        OCR_POSTPROCESS: os.environ.get("OCR_POSTPROCESS_URL"),
    }[stage]
    return (raw or "").strip().rstrip("/") or None


def configured() -> Dict[str, bool]:
    """{stage: configured?} — what `/info` reports (never the URLs themselves)."""
    return {stage: stage_url(stage) is not None for stage in STAGES}


def select_stages(raw: Optional[str]) -> List[str]:
    """The `stages` form field: empty → every stage; `none` → no stage; else a CSV of names."""
    text = (raw or "").strip()
    if not text:
        return list(STAGES)
    if text.lower() == "none":
        return []
    names = [part.strip() for part in text.split(",") if part.strip()]
    unknown = sorted(set(names) - set(STAGES))
    if unknown:
        raise ValueError(f"unknown stage(s) {unknown}; the stages are {list(STAGES)} (or `none`)")
    return [stage for stage in STAGES if stage in names]


def classifier_version() -> str:
    return (os.environ.get("PAGE_CLASSIFICATION_VERSION") or "all").strip() or "all"


def classifier_topn() -> int:
    raw = (os.environ.get("PAGE_CLASSIFICATION_TOPN") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


# ── HTTP ────────────────────────────────────────────────────────────────────────────────────


def _error_detail(response: Any) -> Tuple[str, Optional[str]]:
    """`(detail, reason)` of an ATRIUM error body, or the raw text."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:300] or f"HTTP {response.status_code}", None
    if isinstance(body, dict):
        return str(body.get("detail") or body)[:300], body.get("reason")
    return str(body)[:300], None


def _post(url: str, timeout: float, **kwargs: Any) -> Dict[str, Any]:
    import requests  # noqa: PLC0415  (imported on use; the service image has it)

    try:
        response = requests.post(url, timeout=timeout, **kwargs)
    except requests.Timeout as exc:
        raise _Unavailable(f"no answer from {url} within {timeout:g} s (STAGE_TIMEOUT_S)") from exc
    except requests.RequestException as exc:
        raise _Unavailable(f"cannot reach {url}: {type(exc).__name__}") from exc
    status = response.status_code
    if status in (404, 405):
        raise _Missing(status)
    if status == 429 or status >= 500:
        detail, _reason = _error_detail(response)
        raise _Unavailable(f"{url} answered {status}: {detail}", status)
    if status >= 400:
        detail, reason = _error_detail(response)
        raise _Failed(f"{url} refused the request ({status}): {detail}", status, reason)
    try:
        body = response.json()
    except ValueError as exc:
        raise _Failed(f"{url} answered {status} with a body that is not JSON", status) from exc
    if not isinstance(body, dict):
        raise _Failed(
            f"{url} answered {status} with a JSON {type(body).__name__}, not an object", status
        )
    return body


def service_version(base: str) -> Optional[str]:
    """The stage's `/info` version, best effort (None when it cannot be read)."""
    import requests  # noqa: PLC0415

    try:
        response = requests.get(f"{base}/info", timeout=_INFO_TIMEOUT_S)
        if response.ok:
            version = (response.json() or {}).get("version")
            return str(version) if version else None
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return None


def _record_part(record: Mapping[str, Any]) -> Tuple[str, bytes, str]:
    return (
        "document.json",
        json.dumps(record, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )


def _contained(outcome: StageOutcome, exc: Exception) -> StageOutcome:
    if isinstance(exc, _Unavailable):
        outcome.status, outcome.detail, outcome.http_status = (
            STATUS_UNAVAILABLE,
            exc.detail,
            exc.http_status,
        )
    elif isinstance(exc, _Failed):
        outcome.status, outcome.detail = STATUS_ERROR, exc.detail
        outcome.http_status, outcome.reason = exc.http_status, exc.reason
    elif isinstance(exc, _Missing):
        outcome.status, outcome.http_status = STATUS_ERROR, exc.http_status
        outcome.detail = (
            f"the endpoint does not exist on this server ({exc.http_status}); an older version?"
        )
    else:  # pragma: no cover - a bug in this module, still contained
        outcome.status, outcome.detail = STATUS_ERROR, f"{type(exc).__name__}: {exc}"
    return outcome


# ── the guard ───────────────────────────────────────────────────────────────────────────────


def guard(before: Mapping[str, Any], after: Any) -> Optional[str]:
    """Why a stage's returned record must not replace `before`, or None when it may."""
    if not isinstance(after, dict):
        return "no record in the answer"
    if after.get("doc_id") != before.get("doc_id"):
        return f"doc_id_changed: {before.get('doc_id')!r} came back as {after.get('doc_id')!r}"
    if after.get("source") != before.get("source"):
        return "source_changed: the record's source was rewritten"
    for block in ("content", "tables"):
        if after.get(block) != before.get(block):
            return f"converter_block_changed: {block!r} differs from the converter's"
    before_pages = {str(p.get("page")): p for p in before.get("pages") or [] if isinstance(p, dict)}
    after_pages = {str(p.get("page")): p for p in after.get("pages") or [] if isinstance(p, dict)}
    added = sorted(set(after_pages) - set(before_pages))
    if added:
        return (
            f"page_key_mismatch: page rows the converter did not write {added[:8]} — the stage keyed "
            f"pages by another scheme than the record's labels"
        )
    dropped = sorted(set(before_pages) - set(after_pages))
    if dropped:
        return f"page_rows_dropped: {dropped[:8]}"
    for key, page in before_pages.items():
        for name in CONVERTER_PAGE_FIELDS:
            if page.get(name) != after_pages[key].get(name):
                return f"converter_field_changed: pages[{key!r}].{name}"

    def _lines(record: Mapping[str, Any]) -> Dict[Tuple[str, Any], Mapping[str, Any]]:
        return {
            (str(row.get("page")), row.get("line")): row
            for row in record.get("lines") or []
            if isinstance(row, dict)
        }

    before_lines, after_lines = _lines(before), _lines(after)
    if set(before_lines) != set(after_lines):
        return "line_rows_changed: the stage added or dropped line rows"
    for key, row in before_lines.items():
        for name in CONVERTER_LINE_FIELDS:
            if row.get(name) != after_lines[key].get(name):
                return f"converter_field_changed: lines[{key[0]!r}, {key[1]}].{name}"
    return None


def adopt(result: StageResult, current: Dict[str, Any]) -> Dict[str, Any]:
    """The record to carry on with after `result`: its record when the guard allows, else `current`."""
    if result.record is None or result.outcome.status not in (STATUS_OK, STATUS_PARTIAL):
        return current
    problem = guard(current, result.record)
    if problem:
        result.outcome.status = STATUS_REJECTED
        result.outcome.reason = problem.split(":", 1)[0]
        result.outcome.detail = f"{problem}. The record was kept as it was; the stage's answer is still in the per-page report."
        return current
    result.outcome.record_adopted = True
    return result.record


# ── page-classification ─────────────────────────────────────────────────────────────────────


def run_page_classification(
    base: str,
    pdf_bytes: bytes,
    filename: str,
    record: Mapping[str, Any],
    page_indices: Sequence[int],
    timeout: float,
) -> StageResult:
    """Classify `page_indices` (1-based) of a PDF; never raises."""
    outcome = StageOutcome(
        stage=PAGE_CLASSIFICATION, status=STATUS_OK, url=base, pages=list(page_indices)
    )
    started = time.monotonic()
    result = StageResult(outcome=outcome)
    try:
        outcome.service_version = service_version(base)
        body = _post(
            f"{base}/predict_document",
            timeout,
            files={
                "file": (filename or "document.pdf", pdf_bytes, "application/pdf"),
                "document_json": _record_part(record),
            },
            data={
                "version": classifier_version(),
                "topn": str(classifier_topn()),
                "pages": ",".join(str(index) for index in page_indices),
            },
        )
    except (_Unavailable, _Failed, _Missing) as exc:
        _contained(outcome, exc)
        outcome.elapsed_s = round(time.monotonic() - started, 3)
        return result
    outcome.elapsed_s = round(time.monotonic() - started, 3)
    wanted = set(page_indices)
    for entry in body.get("pages") or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("page"))
        except (TypeError, ValueError):
            continue
        if index not in wanted:
            continue  # an older server classifies every page; only the requested ones count
        predictions = [p for p in entry.get("predictions") or [] if isinstance(p, dict)]
        if not predictions:
            continue
        top = predictions[0]
        result.per_page[index] = {
            "label": top.get("label"),
            "confidence": top.get("score"),
            "top": predictions,
            "source": PAGE_CLASSIFICATION,
        }
    missing = sorted(wanted - set(result.per_page))
    if missing:
        outcome.status = STATUS_PARTIAL if result.per_page else STATUS_ERROR
        outcome.detail = f"no categories came back for page(s) {missing[:8]}"
    else:
        outcome.detail = f"{len(result.per_page)} page(s) classified"
    record_back = body.get("document_json")
    result.record = record_back if isinstance(record_back, dict) else None
    paradata = body.get("paradata")
    outcome.paradata = paradata if isinstance(paradata, dict) else None
    return result


# ── ocr-postprocess ─────────────────────────────────────────────────────────────────────────

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def run_ocr_postprocess(
    base: str,
    record: Mapping[str, Any],
    pages: Mapping[int, Tuple[str, Sequence[str]]],
    timeout: float,
) -> StageResult:
    """Score the record's lines with the common quality model; never raises.

    `pages` maps page_index → (page label, the page's line texts in line order): what the
    per-page fallback sends, and how the `/score_record` answer (keyed by label) is mapped back.
    """
    outcome = StageOutcome(stage=OCR_POSTPROCESS, status=STATUS_OK, url=base, pages=sorted(pages))
    result = StageResult(outcome=outcome)
    started = time.monotonic()
    index_of = {label: index for index, (label, _lines) in pages.items()}
    try:
        outcome.service_version = service_version(base)
        body = _post(f"{base}/score_record", timeout, files={"document_json": _record_part(record)})
    except _Missing:
        _score_per_page(base, record, pages, timeout, result)
        outcome.elapsed_s = round(time.monotonic() - started, 3)
        return result
    except (_Unavailable, _Failed) as exc:
        _contained(outcome, exc)
        outcome.elapsed_s = round(time.monotonic() - started, 3)
        return result
    outcome.elapsed_s = round(time.monotonic() - started, 3)

    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for row in body.get("cleaned_lines") or []:
        if isinstance(row, dict) and row.get("page") is not None:
            by_label.setdefault(str(row["page"]), []).append(row)
    summaries = {
        str(entry.get("page")): entry
        for entry in body.get("pages") or []
        if isinstance(entry, dict)
    }
    for label, rows in by_label.items():
        index = index_of.get(label)
        if index is None:
            continue
        extra = {}
        skipped = (summaries.get(label) or {}).get("skipped_decode_verdict")
        if skipped is not None:
            extra["skipped_decode_verdict"] = skipped
        summary = summarize_quality(rows, **extra)
        if summary:
            result.per_page[index] = summary
    paradata = body.get("paradata")
    outcome.paradata = paradata if isinstance(paradata, dict) else None
    if not result.per_page:
        # Every line kept the converter's decode verdict or had no text: nothing was scored, and
        # the record came back as it was sent.
        verdicts = sum(
            int(entry.get("skipped_decode_verdict") or 0) for entry in summaries.values()
        )
        outcome.status = STATUS_SKIPPED
        outcome.detail = (
            f"no line to score ({verdicts} line(s) keep the converter's decode verdict)"
        )
        return result
    outcome.detail = f"{len(result.per_page)} page(s) scored"
    record_back = body.get("document_json")
    result.record = record_back if isinstance(record_back, dict) else None
    return result


def _score_per_page(
    base: str,
    record: Mapping[str, Any],
    pages: Mapping[int, Tuple[str, Sequence[str]]],
    timeout: float,
    result: StageResult,
) -> None:
    """The fallback for an ocr-postprocess without `/score_record`: one `/process` per page."""
    outcome = result.outcome
    doc_id = _SAFE_NAME.sub("_", str(record.get("doc_id") or "document"))[:80]
    failed: List[int] = []
    for index, (_label, lines) in sorted(pages.items()):
        texts = [text.strip() for text in lines if text and text.strip()]
        if not texts:
            continue
        try:
            body = _post(
                f"{base}/process",
                timeout,
                files={
                    "file": (
                        f"{doc_id}-p{index}.txt",
                        "\n".join(texts).encode("utf-8"),
                        "text/plain",
                    )
                },
                data={"task_type": "text"},
            )
        except _Unavailable as exc:
            _contained(outcome, exc)
            return  # the service is down: do not hammer it page by page
        except (_Failed, _Missing):
            failed.append(index)
            continue
        rows = [row for row in body.get("cleaned_lines") or [] if isinstance(row, dict)]
        summary = summarize_quality(rows, mode="per-page /process", aligned=len(rows) == len(texts))
        if summary:
            result.per_page[index] = summary
    outcome.detail = (
        "this ocr-postprocess has no /score_record; scored page by page through /process "
        f"(report only, the record is not written): {len(result.per_page)} page(s) scored"
    )
    if failed:
        outcome.status = STATUS_PARTIAL if result.per_page else STATUS_ERROR
        outcome.detail += f"; page(s) {failed[:8]} failed"
