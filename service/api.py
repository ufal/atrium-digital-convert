"""service/api.py — the HTTP service of atrium-digital-convert, the born-digital stage (#2, `api-digital`).

Two operations, one converter:

* ``POST /reformat`` — the production endpoint the AMČR route calls. A born-digital file (PDF,
  DOCX, ODT, ODS, XLSX, RTF; DOC and XLS through LibreOffice) and optionally the AMČR seed in;
  the ``atrium_document`` record out, with Markdown only on request and the run's Process Run
  Crate ``CreateAction`` as ``paradata``. It calls NO other service: in the AMČR deployment
  Temporal chains the stages (atrium-digital-convert#1, 30 September).
* ``POST /describe`` — the same conversion, then a per-page assessment
  (``api_util/digital_report.py``): is each page's text layer usable, what kind of page it is
  (page-classification), does its text read (ocr-postprocess), and where should it go next —
  ``nlp``, ``ocr``, ``htr`` or ``none``. The two stages are called only when their URLs are
  configured, through ``service/stages.py``, and every stage failure is a ``stages[]`` entry,
  never a failed request. Each stage writes into the record only through its own accretion, and
  the converter guards what comes back.

The common contract (atrium-project#32/#53/#55/#71): ``/health``, ``/ready``, ``/info`` with every
setting of ``tool_limits.py``; typed responses documented in the committed
``service/openapi.json``; the §4.4 error body with registered reasons; a SIGTERM drain. Refusals:

* 415 ``unsupported_media_type`` — not a born-digital type this service reads (``cause``:
  ``unsupported`` or ``legacy_office_unsupported``; ``accepted`` lists the types);
* 422 ``ocr_text_layer`` — a PDF whose text layer is an earlier OCR run (``OCR_LAYER_DOCUMENT_SHARE``);
* 422 ``source_digest_mismatch`` — the seed's ``source.sha512`` is not the file's digest;
* 422 ``invalid_record`` — a ``document_json`` part that cannot be opened;
* 422 (``cause``: ``encrypted``, ``corrupt``, ``zip_limits_exceeded``, ``conversion_failed``);
* 413/422 ``limit_exceeded`` — ``MAX_UPLOAD_MB``, ``MAX_PAGES``, ``LIBREOFFICE_TIMEOUT_S``;
* 429 ``busy`` — ``MAX_CONCURRENT_JOBS`` conversions already running;
* 501 (``cause``: ``dependency_missing``) — LibreOffice is not installed for a DOC/XLS;
* 503 — the service is draining.

``source_digest_mismatch`` is registered here, beside the shared registry, until the hub's
canonical ``atrium_service.py`` carries it (then ``setdefault`` below is a no-op and goes).

Regenerate the spec after an API change::

    python atrium_openapi.py export --app service.api:app --out service/openapi.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

import atrium_rocrate
from api_util import digital_to_json as d2j
from api_util.digital_ir import DigitalDocument, DigitalInputError
from api_util.digital_report import build_report
from atrium_document import canonical_doc_id, validate_baseline, validate_document
from atrium_limits import LimitExceeded
from atrium_paradata import ParadataLogger
from tool_limits import LIMITS, MAX_CONCURRENT_JOBS, MAX_UPLOAD, ROUTE_TRASH_SHARE, STAGE_TIMEOUT_S

from . import stages

# Shared ATRIUM meta-contract helpers (§4). Byte-identical across every service,
# enforced by para-drift.reusable.yml.
from .atrium_service import (
    REASON_CODES,
    REASON_STATUSES,
    AtriumDocument,
    AtriumHTTPError,
    CreateAction,
    InfoBase,
    LimitNote,
    ServiceState,
    add_cors,
    attach_error_handlers,
    attach_health,
    attach_inflight_middleware,
    attach_openapi_contract,
    build_info,
    busy,
    error_responses,
    operation_id,
    parse_record_part,
    read_tool_version,
    read_upload_bounded,
    serve_lifecycle,
)

logger = logging.getLogger(__name__)

#: The tool id (/info `service`, the spec's `x-atrium-service`): the repository name.
SERVICE = "atrium-digital-convert"
#: The id the previous release of this repository published (v1.0.0-beta, the llm-enrich
#: snapshot): declared so the release gate accepts the rename (atrium-project#72).
PREVIOUS_SERVICE = "atrium-llm-enrich"
#: The program id the record and the paradata carry (unchanged by the repository move).
PROGRAM = "digital-convert"

#: The reason AMČR accepted on #2 (2026-09-30). Registered locally until the hub's canonical
#: registry has it; `setdefault` keeps the canonical wording once it does.
REASON_CODES.setdefault(
    "source_digest_mismatch",
    "The record sent with the request names the original by its `source.sha512`, and the uploaded "
    "file is not that file (HTTP 422): the record would describe one file under another file's "
    "identity. Send the original the seed was made for, or a seed made for this file; do not retry "
    "unchanged.",
)
REASON_STATUSES.setdefault("source_digest_mismatch", (422,))

#: The import-time upload limit, for callers and tests that read it (requests read it live).
MAX_UPLOAD_MB = MAX_UPLOAD.get()

#: Where para_config.txt (the tool version and the components' licences) lives: the repo root.
_PARA_CONFIG_DIR = str(Path(__file__).resolve().parents[1])

#: File suffixes the converter reads, for /info and the 415 `accepted` member.
SUPPORTED_SUFFIXES: Tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".odt",
    ".ods",
    ".xlsx",
    ".rtf",
    ".doc",
    ".xls",
)

#: DigitalInputError reason -> (HTTP status, registered reason or None).
_REFUSALS: Dict[str, Tuple[int, Optional[str]]] = {
    "unsupported": (415, "unsupported_media_type"),
    "legacy_office_unsupported": (415, "unsupported_media_type"),
    "ocr_text_layer": (422, "ocr_text_layer"),
    "source_digest_mismatch": (422, "source_digest_mismatch"),
    "dependency_missing": (501, None),
    "encrypted": (422, None),
    "corrupt": (422, None),
    "zip_limits_exceeded": (422, None),
    "conversion_failed": (422, None),
}


# ── the typed contract (atrium-project#32 round 2) ──────────────────────────────────────────
# These models DOCUMENT the responses the handlers build; they do not filter them. A field the
# handlers always send has no default (required); one they send only sometimes defaults to None.
# No enums: known values are listed in the descriptions, so a new value never breaks a client
# generated from an older spec. Descriptions are published in service/openapi.json.


class ReaderInfo(BaseModel):
    """How the file was read."""

    model_config = ConfigDict(extra="allow")

    kind: str = Field(
        description="The input kind, by content: `pdf`, `docx`, `odt`, `ods`, `xlsx`, `rtf`, `doc`, `xls`."
    )
    media_type: str = Field(
        description="The original's media type (`source.media_type` unless a seed named one)."
    )
    origin: str = Field(
        description="`source.origin`: `digital-born-pdf`, `digital-born-docx`, `digital-born-odt`, ..."
    )
    engine: str = Field(
        description="The structural engine: `light` (the only one in the service image)."
    )
    pages: int = Field(description="Pages read.")
    conversion: Optional[Dict[str, Any]] = Field(
        description="DOC/XLS only: how LibreOffice converted the original (`tool`, `from`, `to`, `seconds`); else null."
    )
    notes: List[str] = Field(
        description="Reader notes worth knowing (e.g. a conversion); usually empty."
    )


class ReformatResponse(BaseModel):
    """`/reformat`: the document's record, and its Markdown when asked for."""

    model_config = ConfigDict(extra="allow")

    service: str = Field(description="`atrium-digital-convert`.")
    doc_id: str = Field(
        description="The record's `doc_id`: the seed's when one was sent, else derived from the file name."
    )
    document_json: AtriumDocument = Field(
        description=(
            "The ATRIUM document record: digital-convert's `source`, `pages`, `content`, `lines` and `tables` "
            "written, every other block of a sent record passed through."
        )
    )
    reader: ReaderInfo
    markdown: Optional[str] = Field(
        None,
        description="Only with `markdown=true`: the record rendered as annotated Markdown (json_to_md).",
    )
    document_json_schema_error: Optional[str] = Field(
        None,
        description="Only when the sent record did not validate and the result inherits that: the schema error.",
    )
    limits_applied: List[LimitNote] = Field(
        description="Every limit that shaped the result without refusing it."
    )
    paradata: CreateAction = Field(
        description=(
            "The call's provenance: its Process Run Crate `CreateAction` (atrium-project#71), whose `@id` is the "
            "`run_uuid` stamped into `document_json`."
        )
    )


class PageCategory(BaseModel):
    """page-classification's answer for one page."""

    model_config = ConfigDict(extra="allow")

    label: Optional[str] = Field(
        description="The top category (`TEXT_P`, `TEXT_HW`, `DRAW`, ...; page-category scheme)."
    )
    confidence: Optional[float] = Field(description="Its score, from 0 to 1.")
    top: List[Dict[str, Any]] = Field(description="The `topn` categories with scores, best first.")
    source: str = Field(description="`page-classification`.")


class PageQuality(BaseModel):
    """Whether a page's text reads: the common quality model, or the converter's decode check."""

    model_config = ConfigDict(extra="allow")

    source: str = Field(
        description="`ocr-postprocess` (the common line-quality model) or `digital-convert` (the decode check only)."
    )
    score: Optional[float] = Field(
        description="From 0 to 1. ocr-postprocess: the mean line-quality score. digital-convert: the "
        "page's decode sanity (the record's `pages[].quality_score` before any scoring), a measure of "
        "how the characters decode, not of how the text reads."
    )
    band: Optional[str] = Field(
        description="`Clear`, `Noisy` or `Trash`; null when no line was judged. ocr-postprocess: the "
        "plurality of its line verdicts. digital-convert: the decode-sanity band, so a garbled page can "
        "read `Clear` here; `text_layer` and `lines_by_category.Garbage` say whether it decodes."
    )
    lines_by_category: Dict[str, int] = Field(
        description="Lines per category: `Clear`/`Noisy`/`Trash`/`Non-text`/`Empty` (ocr-postprocess), or `Garbage`/`Inverted`/`decoded` (digital-convert)."
    )
    lang: Optional[str] = Field(
        description="The page's dominant language, when the model reports one."
    )
    lines_scored: int = Field(description="Lines the verdict is based on.")


class PageLayout(BaseModel):
    """What the converter found on the page."""

    model_config = ConfigDict(extra="allow")

    canvas: Optional[Dict[str, Any]] = Field(
        description="`width`, `height`, `unit` (PDF only); null without geometry."
    )
    lines: int
    blocks: int = Field(description="Distinct `group_id`s: paragraphs, table cells, a sheet.")
    tables: int
    headings: int
    regions: Dict[str, int] = Field(
        description="Lines per region: `page_header`, `page_footer`, `footnote`."
    )
    columns: int = Field(description="Reading-order columns of the body text (PDF).")
    images: int = Field(description="Images the page draws (PDF).")
    vector_paths: int = Field(
        description="Vector paths the page draws (PDF): rules, curves, text drawn as outlines."
    )


class PageReport(BaseModel):
    """One page of `/describe`."""

    model_config = ConfigDict(extra="allow")

    page: str = Field(description="The page label (`pages[].page` in the record).")
    page_index: int = Field(description="1-based physical position.")
    text_layer: str = Field(
        description="`digital` (decodes), `garbled` (exists, does not decode), `ocr` (a prior OCR run), `none` (no text layer), `blank` (an empty page without a page image)."
    )
    needs_ocr: bool
    needs_ocr_reason: Optional[str]
    category: Optional[PageCategory] = Field(
        description="Null when page-classification did not run for the page."
    )
    quality: Optional[PageQuality] = Field(description="Null for a page without lines.")
    route: str = Field(
        description="`nlp`, `ocr` (ATR), `htr` (handwriting) or `none` (nothing to read)."
    )
    route_reason: str
    layout: PageLayout
    text: Optional[str] = Field(
        None, description="The page's text in reading order (unless `include_text=false`)."
    )


class DescribeSummary(BaseModel):
    """The document at a glance."""

    model_config = ConfigDict(extra="allow")

    pages: int
    routes: Dict[str, int] = Field(description="Pages per route: `nlp`, `ocr`, `htr`, `none`.")
    needs_ocr_pages: List[int] = Field(description="`page_index` of the pages flagged `needs_ocr`.")
    reacquire_pages: List[int] = Field(
        description="`page_index` of the pages routed to `ocr` or `htr`."
    )
    document_route: str = Field(description="`nlp`, `ocr`, `htr`, `none`, or `mixed`.")


class StageOutcomeModel(BaseModel):
    """What one adjacent stage did."""

    model_config = ConfigDict(extra="allow")

    stage: str = Field(description="`page-classification` or `ocr-postprocess`.")
    status: str = Field(
        description="`ok`, `partial`, `not_configured`, `not_requested`, `skipped`, `unavailable`, `error` or `rejected`."
    )
    detail: str
    url: Optional[str] = Field(description="The stage's base URL; null when not configured.")
    http_status: Optional[int]
    reason: Optional[str] = Field(
        description="The stage's reason code, or the guard's (`page_key_mismatch`, ...)."
    )
    elapsed_s: Optional[float]
    service_version: Optional[str]
    pages: Optional[List[int]] = Field(description="`page_index` of the pages sent.")
    record_adopted: bool = Field(
        description="Whether the stage's record write was taken into `document_json`."
    )
    paradata: Optional[Dict[str, Any]] = Field(
        description="The stage's own `CreateAction`, when it returned one."
    )


class DescribeResponse(ReformatResponse):
    """`/describe`: the record, plus the per-page assessment and what each stage did."""

    pages: List[PageReport] = Field(description="One entry per page, in page order.")
    summary: DescribeSummary
    stages: List[StageOutcomeModel] = Field(
        description="One entry per stage, in the order they run."
    )


class DigitalInfo(InfoBase):
    """`/info` of atrium-digital-convert."""

    program: str = Field(
        description="The program id the record and paradata carry: `digital-convert`."
    )
    supported_inputs: List[str] = Field(
        description="The file suffixes the converter reads (by content, not by name)."
    )
    media_types: Dict[str, str] = Field(description="The media type of each input kind.")
    libreoffice: bool = Field(description="Whether DOC/XLS can be converted in this deployment.")
    stages: Dict[str, bool] = Field(
        description="Which adjacent stages `/describe` can call (their URLs are configured)."
    )


_RECORD_PART_HELP = (
    "Optional ATRIUM document record: the AMČR seed (`doc_id`, `source` with `sha512`, `filename`, "
    "`media_type`) or an earlier record of the same document. The seed's identity is kept; its `sha512` "
    "must be the uploaded file's (else 422 `source_digest_mismatch`). Every other tool's block passes "
    "through. A record that cannot be opened is refused (422 `invalid_record`); an empty part counts as none."
)
_FILE_HELP = "The born-digital document: PDF, DOCX, ODT, ODS, XLSX, RTF, or legacy DOC/XLS (decided by content)."
_BREAKS_HELP = (
    "DOCX pages: `auto` (explicit + Word's rendered breaks), `explicit`, or `none` (one page)."
)


# ── state ───────────────────────────────────────────────────────────────────────────────────

#: Readiness/draining/in-flight state for the §4.6 disposability contract (issue #55).
_state = ServiceState()


class _Slots:
    """At most MAX_CONCURRENT_JOBS conversions at once; the next one is refused, not queued."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = 0

    def acquire(self) -> bool:
        with self._lock:
            if self._busy >= MAX_CONCURRENT_JOBS.get():
                return False
            self._busy += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._busy = max(0, self._busy - 1)


_slots = _Slots()


def _deep_health() -> Optional[str]:
    """Deep readiness (§4.1): the converter's readers and the record schema are importable."""
    missing = []
    for module in ("pdfplumber", "pypdfium2", "docx", "lxml", "jsonschema"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        return f"converter dependencies missing: {', '.join(missing)}"
    try:
        from atrium_document import load_schema  # noqa: PLC0415

        load_schema()
    except Exception as exc:  # the schema gate would refuse every record
        return f"record schema unavailable: {exc}"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    problem = await asyncio.to_thread(_deep_health)
    if problem:
        logger.warning("digital-convert is not ready: %s", problem)
    _state.warm = problem is None
    async with serve_lifecycle(_state):
        yield


app = FastAPI(
    title="ATRIUM digital-convert API",
    version=read_tool_version(Path(__file__).resolve().parent),
    description=(
        "Born-digital documents (PDF, DOCX, ODT, ODS, XLSX, RTF, DOC, XLS) into the ATRIUM document record, "
        "and a per-page assessment of whether each page's text can be used as it is."
    ),
    lifespan=lifespan,
    responses=error_responses(422, 500),
    generate_unique_id_function=operation_id,
    root_path_in_servers=False,
)
attach_inflight_middleware(app, _state)
attach_error_handlers(app)
attach_openapi_contract(app, SERVICE, previous=PREVIOUS_SERVICE)
add_cors(app, methods=["GET", "POST"])
attach_health(app, deep_check=_deep_health, state=_state)


# ── helpers ─────────────────────────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r"[\x00-\x1f/\\\\]+")


def _safe_name(filename: Optional[str]) -> str:
    """The upload's own name, reduced to a plain file name (the record's `source.filename` and
    the derived `doc_id` come from it, in its original case — atrium-project#10 D2)."""
    name = Path(str(filename or "")).name
    name = _UNSAFE.sub("_", name).strip(". ")
    encoded = name.encode("utf-8")
    if len(encoded) > 200:
        stem, dot, suffix = name.rpartition(".")
        keep = 200 - len(suffix.encode("utf-8")) - 1 if dot else 200
        name = stem.encode("utf-8")[: max(1, keep)].decode("utf-8", "ignore") + (
            f".{suffix}" if dot else ""
        )
    return name or "upload"


def _doc_id(filename: Optional[str]) -> str:
    """The uploaded document's identity, derived the one canonical way (atrium-project#10, D3).

    `atrium_document.canonical_doc_id()` on the upload's own name — the same derivation the CLI
    and every other tool use, so a service upload and a batch run over the same file land on
    the same record. A seed's `doc_id` still wins (DocumentRecord inherits it).
    """
    return canonical_doc_id(_safe_name(filename)) or "upload"


def _refusal(exc: DigitalInputError) -> HTTPException:
    status, reason = _REFUSALS.get(exc.reason, (422, None))
    extra: Dict[str, Any] = {}
    if status == 415:
        extra["accepted"] = [*SUPPORTED_SUFFIXES, *sorted(set(d2j.MEDIA_TYPES.values()))]
    if reason != exc.reason:
        extra["cause"] = exc.reason
    return AtriumHTTPError(status, f"{exc.reason}: {exc}", reason=reason, **extra)


def _require_capacity() -> None:
    if _state.draining:
        raise HTTPException(
            503, "Service is shutting down; retry against a live replica."
        ) from None


def _reader_info(document: DigitalDocument, record: Dict[str, Any]) -> Dict[str, Any]:
    source = record.get("source") or {}
    return {
        "kind": document.kind,
        "media_type": source.get("media_type") or document.media_type,
        "origin": source.get("origin") or document.origin,
        "engine": document.engine,
        "pages": len(document.pages),
        "conversion": document.conversion,
        "notes": list(document.notes),
    }


def _convert(
    work: Path,
    upload: Path,
    baseline: Optional[Dict[str, Any]],
    docx_page_breaks: str,
    endpoint: str,
) -> Tuple[DigitalDocument, Dict[str, Any], Optional[str], ParadataLogger]:
    """Layers A–D for one upload (blocking): the IR, the gated record, a schema error, the run."""
    run = ParadataLogger(
        program=PROGRAM,
        config={"endpoint": endpoint, "engine": "light", "docx_page_breaks": docx_page_breaks},
        paradata_dir=None,
        output_types=["json"],
        config_dir=_PARA_CONFIG_DIR,
    )
    baseline_path: Optional[str] = None
    baseline_invalid = False
    if baseline is not None:
        baseline_path = str(work / "baseline.document.json")
        Path(baseline_path).write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
        try:
            validate_baseline(baseline)
        except Exception as exc:  # inherited, not ours: reported, not fatal (rule 6)
            baseline_invalid = True
            logger.warning("the sent record does not validate (%s); continuing", exc)
    try:
        document, record, page_rows, line_rows = d2j.prepare(
            str(upload),
            baseline=baseline_path,
            doc_id=_doc_id(upload.name),
            run_id=run.run_id,
            run_uuid=run.run_uuid,
            paradata_ref=run.paradata_ref,
            out_dir=str(work),
            docx_page_breaks=docx_page_breaks,
            logger=run,
        )
    except RuntimeError as exc:
        if "is required to convert this input" in str(exc):
            raise AtriumHTTPError(501, str(exc), cause="dependency_missing") from exc
        raise
    record.assert_fields_survived("lines", line_rows)
    record.assert_fields_survived("pages", page_rows)
    data = record.to_dict()
    schema_error: Optional[str] = None
    try:
        validate_document(data)
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc)
        if not baseline_invalid:
            raise HTTPException(
                500, f"Document record rejected by its own schema: {message}"
            ) from exc
        schema_error = message
    run.log_success("json")
    run.log_document_success()
    return document, data, schema_error, run


def _action(
    run: ParadataLogger,
    upload_name: str,
    upload_bytes: bytes,
    media_type: str,
    baseline_sent: bool,
    record: Dict[str, Any],
    extra_outputs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The call's CreateAction (atrium-project#71): what it read and what it wrote."""
    run.finalize()
    inputs = [atrium_rocrate.file_entity(upload_name, upload_bytes, media_type=media_type)]
    if baseline_sent:
        inputs.append(atrium_rocrate.record_entity(str(record.get("doc_id"))))
    outputs = atrium_rocrate.block_entities(atrium_rocrate.blocks_written(record, run.run_uuid))
    outputs.extend(extra_outputs or [])
    return atrium_rocrate.create_action(run.record, inputs=inputs, outputs=outputs)


async def _read_parts(
    file: UploadFile, document_json: Optional[UploadFile]
) -> Tuple[str, bytes, Optional[Dict[str, Any]]]:
    upload_mb = MAX_UPLOAD.get()
    name = _safe_name(file.filename)
    data = await read_upload_bounded(file, upload_mb, "File")
    if not data:
        raise AtriumHTTPError(422, "The uploaded file is empty.", cause="corrupt")
    baseline: Optional[Dict[str, Any]] = None
    if document_json is not None:
        raw = await read_upload_bounded(document_json, upload_mb, "document_json")
        baseline = parse_record_part(raw, "document_json")
    return name, data, baseline


async def _run_blocking(func, *args):
    """Run a blocking conversion in a worker thread, inside a concurrency slot."""
    _require_capacity()
    if not _slots.acquire():
        raise busy(
            f"All {MAX_CONCURRENT_JOBS.get()} conversion slots are taken (MAX_CONCURRENT_JOBS)."
        )
    try:
        return await asyncio.to_thread(func, *args)
    except DigitalInputError as exc:
        raise _refusal(exc) from exc
    except LimitExceeded:
        raise
    finally:
        _slots.release()


# ── routes ──────────────────────────────────────────────────────────────────────────────────


@app.get(
    "/info",
    response_model=None,
    responses={200: {"model": DigitalInfo, "description": "Identity, limits, capabilities."}},
)
async def info() -> Dict[str, Any]:
    """Service identity and capabilities (§4.1)."""
    from api_util.digital_legacy import libreoffice_binary  # noqa: PLC0415

    return build_info(
        app,
        service=SERVICE,
        limits=LIMITS,
        program=PROGRAM,
        supported_inputs=list(SUPPORTED_SUFFIXES),
        media_types=dict(d2j.MEDIA_TYPES),
        libreoffice=libreoffice_binary() is not None,
        stages=stages.configured(),
    )


@app.post(
    "/reformat",
    response_model=None,
    responses={
        200: {
            "model": ReformatResponse,
            "description": "The record, and its Markdown when asked for.",
        },
        **error_responses(413, 415, 429, 501, 503),
    },
)
async def reformat(
    file: UploadFile = File(..., description=_FILE_HELP),
    document_json: UploadFile = File(
        None,
        description=_RECORD_PART_HELP,
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    markdown: bool = Form(
        False, description="Also return the record rendered as annotated Markdown."
    ),
    docx_page_breaks: Literal["auto", "explicit", "none"] = Form("auto", description=_BREAKS_HELP),
):
    """Convert a born-digital document into its ATRIUM document record (§4.2).

    Calls no other service. Pages whose text layer does not decode, or that have none, carry
    `needs_ocr` with a reason; a PDF whose text layer is an earlier OCR run is refused (422
    `ocr_text_layer`) so the route can send it to OCR.
    """
    name, data, baseline = await _read_parts(file, document_json)

    def work() -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="digital_convert_") as tmp:
            workdir = Path(tmp)
            upload = workdir / "in" / name
            upload.parent.mkdir()
            upload.write_bytes(data)
            document, record, schema_error, run = _convert(
                workdir, upload, baseline, docx_page_breaks, "/reformat"
            )
            result: Dict[str, Any] = {
                "service": SERVICE,
                "doc_id": record["doc_id"],
                "document_json": record,
                "reader": _reader_info(document, record),
                "limits_applied": [],
            }
            extra = []
            if markdown:
                from api_util import json_to_md  # noqa: PLC0415

                try:
                    rendered = json_to_md.render_record(record, title=str(record["doc_id"]))
                except ValueError:
                    rendered = (
                        f"# {record['doc_id']}\n"  # nothing to render: a document without text
                    )
                result["markdown"] = rendered
                extra.append(
                    atrium_rocrate.file_entity(
                        f"{record['doc_id']}.md",
                        rendered.encode("utf-8"),
                        media_type="text/markdown",
                    )
                )
            if schema_error:
                result["document_json_schema_error"] = schema_error
            result["paradata"] = _action(
                run, name, data, document.media_type, baseline is not None, record, extra
            )
            return result

    return await _run_blocking(work)


@app.post(
    "/describe",
    response_model=None,
    responses={
        200: {
            "model": DescribeResponse,
            "description": "The record, the per-page assessment, and what each adjacent stage did.",
        },
        **error_responses(413, 415, 429, 501, 503),
    },
)
async def describe(
    file: UploadFile = File(..., description=_FILE_HELP),
    document_json: UploadFile = File(
        None,
        description=_RECORD_PART_HELP,
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    stages_requested: Optional[str] = Form(
        None,
        alias="stages",
        description=(
            "Which adjacent stages to call: a comma-separated subset of `page-classification`, `ocr-postprocess`; "
            "empty for every configured one; `none` for none (the assessment then rests on the converter alone)."
        ),
    ),
    classify_pages: Literal["needs_ocr", "all"] = Form(
        "needs_ocr",
        description="Pages page-classification is asked about: those without a usable text layer (default), or all.",
    ),
    include_text: bool = Form(True, description="Include each page's text in `pages[].text`."),
    docx_page_breaks: Literal["auto", "explicit", "none"] = Form("auto", description=_BREAKS_HELP),
):
    """Convert a born-digital document and assess every page (§4.2).

    For each page: whether its text layer can be used, what kind of page it is
    (page-classification), whether its text reads (ocr-postprocess), and where it should go
    next (`nlp`, `ocr`, `htr`, `none`). A stage that is not configured, not running or answers
    badly is reported in `stages[]`; the request still succeeds.
    """
    try:
        selected = stages.select_stages(stages_requested)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    name, data, baseline = await _read_parts(file, document_json)

    def work() -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="digital_describe_") as tmp:
            workdir = Path(tmp)
            upload = workdir / "in" / name
            upload.parent.mkdir()
            upload.write_bytes(data)
            document, record, schema_error, run = _convert(
                workdir, upload, baseline, docx_page_breaks, "/describe"
            )
            paradata = _action(run, name, data, document.media_type, baseline is not None, record)
            current, outcomes, categories, quality = _run_stages(
                document, record, data, name, selected, classify_pages
            )
            report = build_report(
                document,
                categories=categories,
                quality=quality,
                trash_share_limit=ROUTE_TRASH_SHARE.get(),
                include_text=include_text,
            )
            result: Dict[str, Any] = {
                "service": SERVICE,
                "doc_id": current["doc_id"],
                "document_json": current,
                "reader": _reader_info(document, current),
                "pages": report["pages"],
                "summary": report["summary"],
                "stages": [outcome.to_dict() for outcome in outcomes],
                "limits_applied": [],
                "paradata": paradata,
            }
            if schema_error:
                result["document_json_schema_error"] = schema_error
            return result

    return await _run_blocking(work)


def _validates(record: Dict[str, Any]) -> bool:
    try:
        validate_document(record)
    except Exception:
        return False
    return True


def _run_stages(
    document: DigitalDocument,
    record: Dict[str, Any],
    data: bytes,
    name: str,
    selected: List[str],
    classify_pages: str,
) -> Tuple[
    Dict[str, Any], List[stages.StageOutcome], Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]
]:
    """Call the selected, configured stages in order; adopt each guarded record write."""
    timeout = float(STAGE_TIMEOUT_S.get())
    current = record
    outcomes: List[stages.StageOutcome] = []
    categories: Dict[int, Dict[str, Any]] = {}
    quality: Dict[int, Dict[str, Any]] = {}
    for stage in stages.STAGES:
        base = stages.stage_url(stage)
        if stage not in selected:
            outcomes.append(
                stages.StageOutcome(stage=stage, status=stages.STATUS_NOT_REQUESTED, url=base)
            )
            continue
        if base is None:
            outcomes.append(
                stages.StageOutcome(
                    stage=stage,
                    status=stages.STATUS_NOT_CONFIGURED,
                    detail=f"{stages.URL_ENV[stage]} is not set",
                )
            )
            continue
        if stage == stages.PAGE_CLASSIFICATION:
            if document.kind != "pdf":
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail=f"page-classification reads the page images of a PDF; a {document.kind.upper()} has none",
                    )
                )
                continue
            wanted = [
                page.page_index
                for page in document.pages
                if classify_pages == "all" or page.needs_ocr
            ]
            if not wanted:
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail="every page has a usable text layer (send classify_pages=all to classify them anyway)",
                    )
                )
                continue
            result = stages.run_page_classification(base, data, name, current, wanted, timeout)
            categories = result.per_page
        else:
            lines = {
                page.page_index: (page.page, [line.text for line in page.lines])
                for page in document.pages
                if page.lines
            }
            if not lines:
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail="the document has no lines to score",
                    )
                )
                continue
            result = stages.run_ocr_postprocess(base, current, lines, timeout)
            quality = result.per_page
        adopted = stages.adopt(result, current)
        if adopted is not current and not _validates(adopted):
            result.outcome.status = stages.STATUS_REJECTED
            result.outcome.record_adopted = False
            result.outcome.reason = "schema"
            result.outcome.detail = "the returned record does not validate against the schema; the record was kept as it was"
            adopted = current
        current = adopted
        outcomes.append(result.outcome)
    return current, outcomes, categories, quality


if __name__ == "__main__":
    import os
    import sys

    import uvicorn

    # (12-factor XI) Logs are an event stream: emit to stdout and let the supervisor route
    # them. The format string is alto-postprocess's, verbatim, in every service (issue #61).
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # (12-factor VII) The port is configuration (issue #58); reload needs an import string,
    # everywhere else the app object is correct (see atrium-llm-enrich history for why).
    reload = os.getenv("RELOAD", "false").strip().lower() in ("true", "1", "yes", "on")
    _app_ref = f"{__spec__.name}:app" if reload and __spec__ is not None else app
    uvicorn.run(
        _app_ref,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=reload,
        # (12-factor IX) bounds uvicorn's wait for in-flight requests; serve_lifecycle() adds
        # its own drain on top (issue #55).
        timeout_graceful_shutdown=int(os.getenv("GRACEFUL_SHUTDOWN_S", "20")),
    )
