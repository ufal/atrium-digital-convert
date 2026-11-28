"""service/api.py — the HTTP service of atrium-digital-convert, the born-digital stage (#2, `api-digital`).

Two operations, one converter:

* ``POST /reformat`` — the production endpoint the AMČR route calls. A born-digital file and
  optionally the AMČR seed in; the document's layout out as docproc (``atr/docproc-json``: pages,
  blocks and lines with each page's text-layer verdict and ``needsAtr``; atrium-project#84), with
  Markdown only on request and the run's Process Run Crate ``CreateAction`` as ``paradata``. Two
  workflows (atrium-digital-convert#9): **visual** — a PDF on its pages; a DOCX, DOC, ODT or RTF
  as its native text on the pages of its LibreOffice rendering, a page with no text of its own
  that shows a picture routed to the ATR (atrium-project#84 decision 3), each picture a ``figure``
  block (decision 6), the rendering's PDF back as ``rendered_pdf`` when the ATR or
  page-classification reads it, named in the paradata and not stored (decision 5); **textual** — ODS, XLSX, XLS, TXT, CSV,
  TSV, JSON, extracted as they are, without page geometry, never the ATR. No record: ocr-postprocess builds it from the docproc. It calls NO
  other service: in the AMČR deployment Temporal chains the stages (atrium-digital-convert#1,
  30 September).
* ``POST /describe`` — parked (not part of the pilot): the same conversion, then a per-page
  assessment (``api_util/digital_report.py``) of what the converter read. It calls no other
  service either (since v2.0.0-beta).

The common contract (atrium-project#32/#53/#55/#71): ``/health``, ``/ready``, ``/info`` with every
setting of ``tool_limits.py``; typed responses documented in the committed
``service/openapi.json``; the §4.4 error body with registered reasons; a SIGTERM drain. Refusals:

* 415 ``unsupported_media_type`` — not a type this service reads, an image among them (images go to
  the ATR, H13) (``cause``: ``unsupported`` or ``legacy_office_unsupported``; ``accepted`` lists
  the types);
* 422 ``source_digest_mismatch`` — the seed's ``source.sha512`` is not the file's digest;
* 422 ``invalid_record`` — a ``document_json`` part that cannot be opened;
* 422 (``cause``: ``encrypted``, ``corrupt`` — a reading that would skip content among them —,
  ``zip_limits_exceeded``, ``conversion_failed`` — a failed LibreOffice conversion or rendering);
* 413/422 ``limit_exceeded`` — ``MAX_UPLOAD_MB``, ``MAX_PAGES``, ``LIBREOFFICE_TIMEOUT_S``, and the
  ODF repeat cap, which would cut content (D13);
* 429 ``busy`` — ``MAX_CONCURRENT_JOBS`` conversions already running;
* 501 (``cause``: ``dependency_missing``) — LibreOffice is not installed for an office document or an XLS;
* 503 — the service is draining.

Regenerate the spec after an API change::

    python atrium_openapi.py export --app service.api:app --out service/openapi.json
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

import atrium_rocrate
from api_util import digital_to_json as d2j
from api_util import docproc_writer
from api_util.digital_ir import DigitalDocument, DigitalInputError
from atrium_document import canonical_doc_id
from atrium_limits import LimitExceeded
from atrium_paradata import ParadataLogger
from tool_limits import (
    LIMITS,
    MAX_CONCURRENT_JOBS,
    MAX_UPLOAD,
)

# Shared ATRIUM meta-contract helpers (§4). Byte-identical across every service,
# enforced by para-drift.reusable.yml.
from .atrium_service import (
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
from .describe import describe_document, reader_info, render_markdown

logger = logging.getLogger(__name__)

#: The tool id (/info `service`, the spec's `x-atrium-service`): the repository name.
SERVICE = "atrium-digital-convert"
#: The id the previous release of this repository published (v1.0.0-beta, the llm-enrich
#: snapshot): declared so the release gate accepts the rename (atrium-project#72).
PREVIOUS_SERVICE = "atrium-llm-enrich"
#: The program id the record and the paradata carry (unchanged by the repository move).
PROGRAM = "digital-convert"

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
    ".txt",
    ".csv",
    ".tsv",
    ".json",
)

#: DigitalInputError reason -> (HTTP status, registered reason or None).
_REFUSALS: Dict[str, Tuple[int, Optional[str]]] = {
    "unsupported": (415, "unsupported_media_type"),
    "legacy_office_unsupported": (415, "unsupported_media_type"),
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
        description=(
            "The input kind, by content: `pdf`, `docx`, `odt`, `rtf`, `doc`; `ods`, `xlsx`, `xls`, `txt`, `csv`, "
            "`tsv`, `json`."
        )
    )
    workflow: Optional[str] = Field(
        None,
        description=(
            "`visual` (pages with a verdict each: a PDF, an office document on its rendered pages) or `textual` "
            "(extracted as it is, no pages to render, never the ATR) — atrium-digital-convert#9."
        ),
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
    rendering: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "DOCX/DOC/ODT/RTF only: how LibreOffice rendered the document for its pages (`tool`, `version`, `from`, "
            "`to`, `seconds`, `pages`, `sha512` of the PDF); else null."
        ),
    )
    notes: List[str] = Field(
        description="Reader notes worth knowing (e.g. a conversion); usually empty."
    )


class RenderedPdf(BaseModel):
    """An office document's rendered PDF (atrium-digital-convert#9)."""

    media_type: str = Field(description="`application/pdf`.")
    sha512: str = Field(
        description="Its SHA-512, as the paradata name it: `rendered-pdf`, not stored (atrium-project#84 decision 5)."
    )
    data: str = Field(description="The PDF, base64-encoded.")


class ReformatResponse(BaseModel):
    """`/reformat`: the document's layout as docproc, and its Markdown when asked for."""

    model_config = ConfigDict(extra="allow")

    service: str = Field(description="`atrium-digital-convert`.")
    doc_id: str = Field(
        description="The document's id: the seed's `doc_id` when one was sent, else derived from the file name."
    )
    docproc: Dict[str, Any] = Field(
        description=(
            "The document's layout as docproc (`atr/docproc-json`, atrium-project#84): pages with the text-layer "
            "verdict (`textLayer`) and, where the text cannot be used, `needsAtr` with its reason; blocks with their "
            "`lines[]`; each picture a page shows a `figure` block with its `bbox` and no `imageRef` (atrium-project#84 "
            "decision 6); `metadata.routing.pages[]` routing every page (`text-layer`, `atr`, `empty`). An office "
            "document's page with no line outside its running header and footer that shows a picture is routed `atr` "
            "(decision 3). The forced pages and the pages with `figure` blocks are the pages page-classification reads "
            "(decision 4). Valid against `schemas/docproc/document.atrium-proposal.schema.json` (the ATR's schema with "
            "asks 3, 5, 10 and 13 and answer 12). No record is written: ocr-postprocess builds it from this."
        )
    )
    reader: ReaderInfo
    markdown: Optional[str] = Field(
        None,
        description="Only with `markdown=true`: the document rendered as annotated Markdown (json_to_md).",
    )
    rendered_pdf: Optional[RenderedPdf] = Field(
        None,
        description=(
            "An office document's rendered PDF (DOCX, DOC, ODT, RTF): the pages the docproc's `pageNumber` and boxes "
            "refer to. Returned when it is read downstream: by the ATR when a page is routed `atr` (the whole "
            "document goes there as this PDF), by page-classification when a page holds a `figure` block. The "
            "paradata name it in `result` for every office document (`rendered-pdf`, `stored: false`; "
            "atrium-project#84 decision 5), returned or not. Absent for a PDF and the textual workflow. A later step "
            "uses these bytes, never a re-render: LibreOffice's output differs per run."
        ),
    )
    limits_applied: List[LimitNote] = Field(
        description=(
            "The splitting the result rests on (D13): lines of a text-only input or a spreadsheet wrapped at "
            "`MAX_LINE_CHARS`, a sheet continued on further pages. Nothing is sampled."
        )
    )
    paradata: CreateAction = Field(
        description=(
            "The call's provenance (paradata 3.0, atrium-project#84): its Process Run Crate `CreateAction`. `object` "
            "is the upload as `original` (and the seed, `atrium/document-json`), `result` the docproc "
            "(`atr/docproc-json`), each by SHA-512; the docproc is `stored: false` when a page needs the ATR (H14). "
            "An office document's rendered PDF is in `result` too, as `rendered-pdf` with `stored: false`."
        )
    )


class PageDecode(BaseModel):
    """Whether a page's characters decode: the converter's own check, a system apart from text
    quality (D18 rules 2 and 3; atrium-project#84)."""

    model_config = ConfigDict(extra="allow")

    decode_score: Optional[float] = Field(
        description="From 0 to 1: the mean of the lines' decode scores, how their characters decode (not how "
        "the text reads)."
    )
    lines_by_flag: Dict[str, int] = Field(
        description="Lines per decode flag, as on docproc lines: `decode:garbled`, `decode:inverted`."
    )
    lines_decoded: int = Field(description="Lines without a decode flag.")


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
    words: int = Field(
        description="Whitespace-separated words of the page's body text (outside its running "
        "header and footer)."
    )
    damaged_words: int = Field(
        description="Of those, the words that lost a letter to a glyph without a Unicode mapping "
        "(U+FFFD). Counted for information: they route nothing (atrium-digital-convert#9); whether "
        "the text reads is ocr-postprocess's scoring, a step of the pipeline."
    )


class PageReport(BaseModel):
    """One page of `/describe`."""

    model_config = ConfigDict(extra="allow")

    page: str = Field(description="The page label (`pages[].page` in the record).")
    page_index: int = Field(description="1-based physical position.")
    text_layer: str = Field(
        description="`digital` (decodes), `garbled` (exists, does not decode), `ocr` (a prior OCR run), `none` (no text layer, or only a running header or footer over a full-page image), `blank` (an empty page without a page image): docproc's page `textLayer`."
    )
    needs_atr: bool = Field(description="The page goes to the ATR: docproc's page `needsAtr`.")
    needs_atr_reason: Optional[str] = Field(
        description="Why; docproc's `needsAtrReason`. Null when it does not."
    )
    decode: Optional[PageDecode] = Field(description="Null for a page without lines.")
    route: str = Field(
        description="`nlp`, `ocr` (ATR), `htr` (handwriting) or `none` (nothing to read), from the "
        "converter's own signals: a page that does not decode or has no text layer goes to `ocr`."
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
    needs_atr_pages: List[int] = Field(description="`page_index` of the pages flagged `needs_atr`.")
    reacquire_pages: List[int] = Field(
        description="`page_index` of the pages routed to `ocr` or `htr`."
    )
    document_route: str = Field(description="`nlp`, `ocr`, `htr`, `none`, or `mixed`.")


class DescribeResponse(ReformatResponse):
    """`/describe` (parked): the docproc, the development dict and the per-page assessment."""

    document: Dict[str, Any] = Field(
        description=(
            "The converter's development dict: record-shaped (`pages`, `lines` with `group_id`, `style` and "
            "decode `flags`, `content`, `tables`), never a record and never stored."
        )
    )

    pages: List[PageReport] = Field(description="One entry per page, in page order.")
    summary: DescribeSummary


class DigitalInfo(InfoBase):
    """`/info` of atrium-digital-convert."""

    program: str = Field(
        description="The program id the record and paradata carry: `digital-convert`."
    )
    supported_inputs: List[str] = Field(
        description="The file suffixes the converter reads (by content, not by name)."
    )
    media_types: Dict[str, str] = Field(description="The media type of each input kind.")
    workflows: Dict[str, List[str]] = Field(
        description=(
            "The input kinds of each workflow (atrium-digital-convert#9): `visual` (pages with a verdict each, the "
            "ATR for a page whose text cannot be used) and `textual` (extracted as it is, never the ATR)."
        )
    )
    libreoffice: bool = Field(
        description="Whether office documents can be rendered and DOC/XLS converted in this deployment."
    )


_RECORD_PART_HELP = (
    "Optional: the AMČR seed of the document (`doc_id`, `source` with `sha512`, `filename`, `media_type`), "
    "read only. Its `doc_id` names the result; its `sha512` must be the uploaded file's (else 422 "
    "`source_digest_mismatch`). Nothing is written into it: since atrium-project#84 the record is built by "
    "ocr-postprocess from the docproc. A part that cannot be opened is refused (422 `invalid_record`); an "
    "empty part counts as none."
)
_FILE_HELP = (
    "The document, decided by content: PDF, DOCX, DOC, ODT, RTF (visual); ODS, XLSX, XLS, TXT, CSV, TSV, JSON "
    "(textual). Not an image: images go to the ATR (H13)."
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
    else:
        # A clean start must log too: the image smoke test looks for one record in the agreed
        # shape at the default LOG_LEVEL (atrium-project#61), and nothing else here logs at INFO.
        from api_util.digital_legacy import libreoffice_binary  # noqa: PLC0415

        logger.info(
            "digital-convert %s ready: LibreOffice %s",
            app.version,
            "available" if libreoffice_binary() else "not found (DOC/XLS are refused)",
        )
    _state.warm = problem is None
    async with serve_lifecycle(_state):
        yield


app = FastAPI(
    title="ATRIUM digital-convert API",
    version=read_tool_version(Path(__file__).resolve().parent),
    description=(
        "Born-digital documents (PDF, DOCX, DOC, ODT, RTF, ODS, XLSX, XLS, TXT, CSV, TSV, JSON) into docproc "
        "(atr/docproc-json), the layout ocr-postprocess builds the record from, and a per-page assessment of "
        "whether each page's text can be used as it is."
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
    the same output. A seed's `doc_id` still wins (`d2j.prepare`).
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


def _convert(
    work: Path,
    upload: Path,
    baseline: Optional[Dict[str, Any]],
    endpoint: str,
    markdown: bool = False,
    describe: Optional[Dict[str, Any]] = None,
) -> Tuple[DigitalDocument, Dict[str, Any], Dict[str, Any], bytes, ParadataLogger]:
    """Layers A–D for one upload (blocking): the IR, the development dict, the validated docproc
    and its bytes, and the run.

    The run's configuration and the docproc gate are the command line's (`d2j.run_config`,
    `docproc_writer.validate`, atrium-project#72)."""
    run = ParadataLogger(
        program=PROGRAM,
        config={
            "endpoint": endpoint,
            **d2j.run_config(upload.name, "light", markdown=markdown, describe=describe),
        },
        paradata_dir=None,
        config_dir=_PARA_CONFIG_DIR,
    )
    baseline_path: Optional[str] = None
    if baseline is not None:
        baseline_path = str(work / "seed.document.json")
        Path(baseline_path).write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    try:
        document, dev = d2j.prepare(
            str(upload),
            baseline=baseline_path,
            doc_id=_doc_id(upload.name),
        )
    except RuntimeError as exc:
        if "is required to convert this input" in str(exc):
            raise AtriumHTTPError(501, str(exc), cause="dependency_missing") from exc
        raise
    try:
        docproc = docproc_writer.build_docproc(document)
    except docproc_writer.DocprocError as exc:
        raise HTTPException(500, f"docproc rejected by its own schema: {exc}") from exc
    run.log_success("docproc_json")
    run.log_document_success()
    return document, dev, docproc, d2j.docproc_bytes(docproc), run


def _action(
    run: ParadataLogger,
    upload_bytes: bytes,
    document: DigitalDocument,
    docproc: Dict[str, Any],
    docproc_data: bytes,
    seed_bytes: Optional[bytes],
) -> Dict[str, Any]:
    """The call's CreateAction (paradata 3.0, atrium-project#84): the original read and the
    docproc written, by distribution name and SHA-512 (`d2j.record_run`, as the command line).
    The seed, when one was sent, is an input as the bytes of the part (`atrium/document-json`).
    An office document's rendered PDF is an output, `stored: false`, whether or not it was handed
    over (decision 5).
    The Markdown is a development output and is not named."""
    seed = None
    if seed_bytes is not None:
        seed = {"data": seed_bytes, "media_type": "application/json"}
    d2j.record_run(
        run,
        document,
        docproc,
        {"data": upload_bytes, "media_type": document.media_type},
        docproc_data,
        seed,
    )
    run.finalize()
    return atrium_rocrate.action_from_paradata(run.record)


async def _read_parts(
    file: UploadFile, document_json: Optional[UploadFile]
) -> Tuple[str, bytes, Optional[Dict[str, Any]], Optional[bytes]]:
    """The upload's name and bytes, and the seed part opened and as sent (None, None without one)."""
    upload_mb = MAX_UPLOAD.get()
    name = _safe_name(file.filename)
    data = await read_upload_bounded(file, upload_mb, "File")
    if not data:
        raise AtriumHTTPError(422, "The uploaded file is empty.", cause="corrupt")
    baseline: Optional[Dict[str, Any]] = None
    raw: Optional[bytes] = None
    if document_json is not None:
        raw = await read_upload_bounded(document_json, upload_mb, "document_json")
        baseline = parse_record_part(raw, "document_json")
    return name, data, baseline, raw if baseline is not None else None


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
        workflows={
            workflow: [kind for kind in d2j.KINDS if d2j.WORKFLOWS[kind] == workflow]
            for workflow in (d2j.WORKFLOW_VISUAL, d2j.WORKFLOW_TEXTUAL)
        },
        libreoffice=libreoffice_binary() is not None,
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
        False,
        description="Also return the document rendered as annotated Markdown (a development view).",
    ),
):
    """Convert a born-digital document into docproc (§4.2; atrium-project#84).

    Calls no other service. A PDF page whose text layer cannot be used (it does not decode, it is
    a prior OCR run, there is none) carries `needsAtr` with its reason and is routed `atr`; no
    document is refused for its pages. An office document keeps its own text, but a page of it
    with no line outside its running header and footer that shows a picture is routed `atr` too
    (atrium-project#84 decision 3). Each picture is a `figure` block (decision 6); with the forced
    pages, the pages holding one are those page-classification reads (decision 4). An office
    document's rendered PDF comes with it when the ATR or page-classification reads it, and the
    paradata name it, not stored, either way (decision 5). A textual input is never routed `atr`.
    No record is written: ocr-postprocess builds it from the docproc.
    """
    name, data, baseline, seed_bytes = await _read_parts(file, document_json)

    def work() -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="digital_convert_") as tmp:
            workdir = Path(tmp)
            upload = workdir / "in" / name
            upload.parent.mkdir()
            upload.write_bytes(data)
            document, dev, docproc, docproc_data, run = _convert(
                workdir, upload, baseline, "/reformat", markdown=markdown
            )
            result: Dict[str, Any] = {
                "service": SERVICE,
                "doc_id": document.doc_id,
                "docproc": docproc,
                "reader": reader_info(document, dev),
            }
            if d2j.rendered_pdf_read(document, docproc):
                result["rendered_pdf"] = {
                    "media_type": "application/pdf",
                    "sha512": document.rendering["sha512"],
                    "data": base64.b64encode(document.rendered_pdf).decode("ascii"),
                }
            if markdown:
                result["markdown"] = render_markdown(dev)
                run.log_success("markdown")
            result["paradata"] = _action(
                run,
                data,
                document,
                docproc,
                docproc_data,
                seed_bytes,
            )
            result["limits_applied"] = run.limits
            return result

    return await _run_blocking(work)


@app.post(
    "/describe",
    response_model=None,
    responses={
        200: {
            "model": DescribeResponse,
            "description": "The docproc, the development dict and the per-page assessment.",
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
    include_text: bool = Form(True, description="Include each page's text in `pages[].text`."),
):
    """Convert a born-digital document and assess every page (§4.2). Parked: not part of the
    pilot (atrium-project#84, H11).

    For each page, from what the converter itself read: whether its text layer can be used,
    whether its characters decode, its layout, and where it should go next (`nlp`, `ocr`,
    `htr`, `none`). It calls no other service: a page's type and the quality of its text are
    the pipeline's own steps (page-classification, ocr-postprocess).
    """
    name, data, baseline, seed_bytes = await _read_parts(file, document_json)

    def work() -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="digital_describe_") as tmp:
            workdir = Path(tmp)
            upload = workdir / "in" / name
            upload.parent.mkdir()
            upload.write_bytes(data)
            document, dev, docproc, docproc_data, run = _convert(
                workdir,
                upload,
                baseline,
                "/describe",
                describe={"include_text": include_text},
            )
            assessed = describe_document(document, dev, include_text)
            # The run ends with the assessment, as the command line's --describe run does.
            run.log_success("describe_json")
            current = assessed["document"]
            return {
                "service": SERVICE,
                "doc_id": document.doc_id,
                "docproc": docproc,
                "document": current,
                "reader": reader_info(document, current),
                "pages": assessed["pages"],
                "summary": assessed["summary"],
                "paradata": _action(run, data, document, docproc, docproc_data, seed_bytes),
                "limits_applied": run.limits,
            }

    return await _run_blocking(work)


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
        # LOG_LEVEL governs uvicorn's own records too, its start-up lines and its access log: the level
        # basicConfig() set above, so `WARN` and the other spellings logging accepts work here too.
        # Without it uvicorn logged INFO under LOG_LEVEL=WARNING (atrium-project#61).
        log_level=logging.getLogger().getEffectiveLevel(),
    )
