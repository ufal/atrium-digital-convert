"""service/api.py — FastAPI surface for atrium-llm-enrich (strategy §4.2).

Brings llm-enrich into API parity with the rest of the ATRIUM pipeline. It wraps the
existing **remote / lightweight-local** enrichment engine (``llm_client_shared`` +
``openrouter_client`` / ``ollama_client``) — deliberately the torch-free path, so the
service stays in the no-model fast lane and never needs the GPU stack.

Backend is selected with ``LLM_BACKEND`` (``openrouter`` default, or ``ollama``); the
engine is warmed once on startup. A misconfigured backend (missing API key / model) does
**not** crash the app: ``/info`` and ``/health`` stay up and report ``ready: false`` while
the extraction endpoints answer 503 until configured.

The typed contract (atrium-project#32 round 2). Every route declares its response model
and its error statuses, so the committed ``service/openapi.json`` — attached to every
release, and what the AMČR pipeline generates its clients from — types every field. The
models below DOCUMENT the responses (``response_model=None``): the bytes sent are what the
handlers build, and ``tests/test_api_contract.py`` validates real responses against the
published schema. Refusals carry registered reasons: a wrong file type is 415
``unsupported_media_type``, a record that cannot be opened is 422 ``invalid_record``. Every
success carries the call's Process Run Crate ``CreateAction`` as ``paradata``
(atrium-project#71); the service writes no paradata file.
Regenerate the spec after an API change::

    python atrium_openapi.py export --app service.api:app --out service/openapi.json
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import atrium_rocrate
import tool_limits
from atrium_document import FILE_SUFFIX, canonical_doc_id
from atrium_limits import LimitExceeded, LimitNotes
from atrium_paradata import ParadataLogger
from tool_limits import (
    LIMITS,
    LLM_CONTEXT_WINDOW,
    LLM_MAX_CONSECUTIVE_ERRORS,
    LLM_MAX_NEW_TOKENS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    MAX_UPLOAD,
)

# Shared ATRIUM meta-contract helpers (§4). Byte-identical across every service,
# enforced by para-drift.reusable.yml.
from .atrium_service import (
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
    check_body_size,
    error_responses,
    operation_id,
    parse_record_part,
    read_tool_version,
    read_upload_bounded,
    serve_lifecycle,
)

logger = logging.getLogger(__name__)

#: The tool id (/info `service`, the spec's `x-atrium-service`): the repository name.
SERVICE = "atrium-llm-enrich"

# Every limit of this service is declared in tool_limits.py (atrium-project#53, factor III)
# and read per request; /info reports them all. The upload limit's import-time value
# stays here for the callers and tests that read it. The context window and the reply cap
# used to be read here, AFTER a backend-independent default had already been picked
# (32000 for either backend), and the reserve for the reply was a second hand-kept copy
# of the clients' MAX_NEW_TOKENS.
MAX_UPLOAD_MB = MAX_UPLOAD.get()
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

_LINE_SUFFIXES = (".csv", ".teitok.xml")
_DOC_SUFFIXES = (".md", ".txt")
#: The media type of each accepted upload, by its last suffix: the CreateAction's `object`.
_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xml": "application/xml",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

#: Where para_config.txt (the tool version and licences the paradata records) lives: the repo root.
_PARA_CONFIG_DIR = str(Path(__file__).resolve().parents[1])


# ── the typed contract (atrium-project#32 round 2) ──────────────────────────────────────────
# These models document the responses the handlers build; they do not filter them. A field
# the handlers always send has no default (required); one they send only sometimes defaults
# to None. Descriptions are published in service/openapi.json, so they are written for the
# client.


class Enrichment(BaseModel):
    """What the model found for one line (line mode) or one passage (document mode)."""

    model_config = ConfigDict(extra="allow")

    extracted_keywords_cs: List[str] = Field(
        description="Czech archaeological keywords found in the text."
    )
    extracted_keywords_en: List[str] = Field(
        description="Their English translations, in the same order."
    )
    teater_category: str = Field(
        description="The TEATER thesaurus category the model chose, by its label."
    )
    confidence_score: float = Field(
        description="The model's confidence in `teater_category`, from 0 to 1."
    )


class KeywordResult(BaseModel):
    """One result: a qualifying line (line mode) or a passage the model located (document mode)."""

    model_config = ConfigDict(extra="allow")

    file_id: str = Field(description="The input's name as the engine saw it.")
    enrichment: Enrichment
    page: Union[int, str, None] = Field(
        None,
        description="The page: a number in line mode; the page label the model cited in document mode.",
    )
    line: Optional[int] = Field(None, description="Line mode: the line number within the page.")
    locator: Optional[str] = Field(
        None, description="Document mode: the passage the keywords come from."
    )
    categ: Optional[str] = Field(
        None, description="Line mode: the line's quality category from alto-postprocess."
    )
    quality_score: Optional[float] = Field(
        None, description="Line mode: the line's quality score, when given."
    )
    original_text: Optional[str] = Field(None, description="Line mode: the line's text.")


class ExtractStats(BaseModel):
    """What the run did: how many lines or passages were processed, skipped and attempted."""

    model_config = ConfigDict(extra="allow")

    processed: int = Field(
        description="Lines (line mode) or passages (document mode) with a result."
    )
    skipped_filter: int = Field(description="Lines the quality filter left out.")
    skipped_error: int = Field(description="Lines or calls that failed.")
    aborted: int = Field(
        description="1 when the document was given up (LLM_MAX_CONSECUTIVE_ERRORS), else 0."
    )
    attempted: int = Field(description="Model calls made: 0 means the model was never asked.")
    truncated: Optional[int] = Field(
        None, description="Line mode: replies cut at LLM_MAX_NEW_TOKENS."
    )
    unprocessed: Optional[int] = Field(
        None, description="Line mode, after an abort: the lines not sent."
    )
    repaired: Optional[int] = Field(None, description="Document mode: replies repaired before use.")
    dropped_items: Optional[int] = Field(
        None, description="Document mode: items dropped while repairing."
    )


class ExtractResponse(BaseModel):
    """The keywords of one document, and its record when one was sent."""

    model_config = ConfigDict(extra="allow")

    service: str = Field(description="`atrium-llm-enrich`.")
    doc_id: str = Field(
        description="The document's id: from the upload's name, or the sent record's `doc_id`."
    )
    backend: str = Field(description="The LLM backend: `openrouter` or `ollama`.")
    model: str = Field(description="The model id (`<model>@<host>` for Ollama).")
    mode: str = Field(
        description="`line` (.csv, .teitok.xml) or `document` (.md, .txt, inline text)."
    )
    results: List[KeywordResult] = Field(description="One entry per line or passage with a result.")
    stats: ExtractStats
    limits_applied: List[LimitNote] = Field(
        description="Every limit that shaped the result without refusing it."
    )
    document_json: Optional[AtriumDocument] = Field(
        None,
        description=(
            "Only when a record was sent and the run contributed: the record, with llm-enrich's `enrichment` "
            "block updated and every other block as sent."
        ),
    )
    document_json_schema_error: Optional[str] = Field(
        None,
        description="Only when the returned record does not validate: the schema error (the sent record's).",
    )
    paradata: Optional[CreateAction] = Field(
        description=(
            "The call's provenance: its Process Run Crate `CreateAction` (atrium-project#71), whose `@id` is the "
            "`run_uuid` stamped into `document_json`."
        ),
    )


class VocabularyInfo(BaseModel):
    """How much of the vocabulary reaches the model."""

    model_config = ConfigDict(extra="allow")

    terms: int = Field(description="Terms in the vocabulary (the excluded themes left out).")
    line_prompt_terms: int = Field(description="Terms that fit the line prompt.")
    document_prompt_terms: int = Field(description="Terms that fit the document prompt.")


class LlmInfo(InfoBase):
    """`/info` of atrium-llm-enrich."""

    backend: Optional[str] = Field(
        description="The configured backend; null until the engine is warm."
    )
    model: Optional[str] = Field(description="The model id; null until the engine is warm.")
    ready: bool = Field(
        description="Whether the extraction endpoints can answer (else they answer 503)."
    )
    vocabulary: Optional[VocabularyInfo] = Field(description="Null until the engine is warm.")
    supported_inputs: List[str] = Field(description="The file suffixes `/extract_keywords` reads.")
    languages: List[str] = Field(description="The keyword languages.")


class ExtractTextRequest(BaseModel):
    """The body of `/extract_keywords_text`."""

    text: str = Field(description="Raw text for document-level archaeological keyword extraction.")
    document_json: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Optional baseline ATRIUM Document JSON: a record, or an AMČR seed (`doc_id`, `source`). When given, "
            "the response's `document_json` carries the record back with only llm-enrich's `enrichment` block "
            "updated. A record that does not validate is still accepted, and the response then also carries "
            "`document_json_schema_error`; one that cannot be opened is refused (422 `invalid_record`)."
        ),
    )


#: Where the record parts' description says what they are, for both endpoints.
_RECORD_PART_HELP = (
    "Optional baseline ATRIUM Document JSON (accretion model, docs/document_schema.md / issue #13), or an AMČR "
    "seed (`doc_id`, `source`). When given, the response's `document_json` carries the record back with only "
    "llm-enrich's `enrichment` block updated — every other tool's block (pages, lines, entities, translations, "
    "...) passes through untouched. A baseline that does not validate against atrium_document.schema.json is "
    "still accepted (rule 6), but the response then also carries `document_json_schema_error`; one that is not "
    "a JSON object is refused (422 `invalid_record`)."
)

# Warmed engine state (or an "error" key when the backend is unavailable).
_engine: Dict[str, Any] = {}


def _load_engine() -> Dict[str, Any]:
    """Build the enrichment engine for the configured backend (blocking).

    Replicates the setup sequence of ``openrouter_client.main`` / ``ollama_client.main``:
    load config + vocabulary, build the archaeological schema and system prompt, and bind
    a ``chat_fn`` to the chosen remote/local backend. Heavy-ish imports are kept local so
    importing this module (for contract tests) never requires ``requests``/vocab data.
    """
    import os

    import requests

    from llm_client_shared import (
        approx_token_count,
        build_document_schema,
        build_document_system_prompt,
        build_schema,
        build_system_prompt,
        count_vocab_terms,
        excluded_prompt_themes,
        load_config,
    )
    from vocab_manager import VocabularyManager

    backend = os.getenv("LLM_BACKEND", "openrouter").lower()
    config_path = os.getenv("LLM_CONFIG", "llm_config.txt")
    config = load_config(config_path) if Path(config_path).exists() else {}

    vocab_path = os.getenv("VOCAB_PATH") or config.get(
        "VOCAB_PATH", "data_samples/vocab/union_nested.json"
    )
    # The limits (tool_limits.py): the window defaults per backend, as in each client's CLI.
    context_window = tool_limits.context_window()
    max_retries = LLM_MAX_RETRIES.get()
    timeout = LLM_TIMEOUT.get()
    max_input_tokens = tool_limits.vocab_prompt_budget_tokens()

    filter_params = {
        "include_non_text": config.get("INCLUDE_NON_TEXT", "true").lower() == "true",
        "min_char_count": int(config.get("MIN_CHAR_COUNT", "3")),
        "min_char_non_text": int(config.get("MIN_CHAR_NON_TEXT", "8")),
        "min_alpha_ratio_non_text": float(config.get("MIN_ALPHA_RATIO_NON_TEXT", "0.40")),
    }

    vocab_mgr = VocabularyManager(vocab_path=vocab_path)
    # auto_sync=False: never harvest inside a pipeline run. Besides the
    # multi-minute OAI-PMH round trip, the sync path can no longer build a
    # usable vocabulary — fetch_amcr_vocab() emits bare {"cs", "en"} pairs
    # with no "source"/"scheme", so assign_theme() drops every term into
    # "Other", which excluded_prompt_themes() withholds from the prompt. The
    # result is an enum holding only "Nerelevantní (meta-text)", which then
    # rejects every correct answer the model gives as a validation error.
    # A missing vocabulary is a configuration fault: say so and stop.
    vocab_data = vocab_mgr.load(auto_sync=False)
    # Which themes reach the model is a taxonomy_config decision (in_prompt), not a
    # literal in the prompt builder — see excluded_prompt_themes().
    excluded_themes = excluded_prompt_themes(vocab_mgr)
    line_prompt, line_terms = build_system_prompt(
        vocab_data, max_tokens=max_input_tokens, excluded_themes=excluded_themes
    )
    doc_prompt, doc_terms = build_document_system_prompt(
        vocab_data, max_tokens=max_input_tokens, excluded_themes=excluded_themes
    )
    line_model = build_schema(line_terms)
    doc_model = build_document_schema(doc_terms)
    session = requests.Session()

    # The vocabulary cut (atrium-project#53): terms that do not fit the prompt budget are
    # left out of the prompt -- and so out of reach of the model. It used to be a stdout
    # line; it is now a warning, /info `vocabulary`, and a standing `limits_applied` note.
    total_terms = count_vocab_terms(vocab_data, excluded_themes)
    vocab_notes = {}
    for mode, terms in (("line", line_terms), ("document", doc_terms)):
        cut = total_terms - len(terms)
        notes = LimitNotes()
        if cut > 0:
            logger.warning(
                "%s prompt: %d of %d vocabulary terms left out -- they do not fit the %d-token "
                "prompt budget (LLM_CONTEXT_WINDOW %d - LLM_MAX_NEW_TOKENS - 512).",
                mode,
                cut,
                total_terms,
                max_input_tokens,
                context_window,
            )
            notes.note(
                "vocab_prompt_budget_tokens",
                "trimmed",
                cut,
                f"{cut} of {total_terms} vocabulary terms were left out of the {mode} prompt: they "
                f"do not fit its {max_input_tokens}-token budget; raise LLM_CONTEXT_WINDOW to include them",
                value=max_input_tokens,
            )
        vocab_notes[mode] = notes
    doc_prompt_tokens = approx_token_count(doc_prompt)
    tool_limits.set_prompt_facts(document_prompt_tokens=doc_prompt_tokens)
    vocabulary = {
        "terms": total_terms,
        "line_prompt_terms": len(line_terms),
        "document_prompt_terms": len(doc_terms),
    }

    if backend == "openrouter":
        from openrouter_client import _build_headers, make_chat_fn

        api_key = os.getenv("OPENROUTER_API_KEY") or config.get("OPENROUTER_API_KEY")
        model = os.getenv("OPENROUTER_MODEL") or config.get("OPENROUTER_MODEL")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        if not model:
            raise RuntimeError("OPENROUTER_MODEL is not set")
        headers = _build_headers(
            api_key,
            os.getenv("OPENROUTER_SITE_URL"),
            os.getenv("OPENROUTER_APP_NAME", "atrium-llm-enrich"),
        )
        line_chat_fn = make_chat_fn(
            session, headers, model, line_model.model_json_schema(), max_retries, timeout, None
        )
        doc_chat_fn = make_chat_fn(
            session, headers, model, doc_model.model_json_schema(), max_retries, timeout, None
        )
        model_id = model
    elif backend == "ollama":
        from ollama_client import DEFAULT_OLLAMA_HOST, make_chat_fn

        host = os.getenv("OLLAMA_HOST") or config.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        model = os.getenv("OLLAMA_MODEL") or config.get("OLLAMA_MODEL")
        if not model:
            raise RuntimeError("OLLAMA_MODEL is not set")
        line_chat_fn = make_chat_fn(
            session,
            host,
            model,
            line_model.model_json_schema(),
            max_retries,
            timeout,
            num_ctx=context_window,
        )
        doc_chat_fn = make_chat_fn(
            session,
            host,
            model,
            doc_model.model_json_schema(),
            max_retries,
            timeout,
            num_ctx=context_window,
        )
        model_id = f"{model}@{host}"
    else:
        raise RuntimeError(f"Unknown LLM_BACKEND '{backend}' (expected 'openrouter' or 'ollama')")

    return {
        "backend": backend,
        "model": model_id,
        "line_prompt": line_prompt,
        "line_model": line_model,
        "line_chat_fn": line_chat_fn,
        "doc_prompt": doc_prompt,
        "doc_model": doc_model,
        "doc_chat_fn": doc_chat_fn,
        "filter_params": filter_params,
        "vocab_notes": vocab_notes,
        "vocabulary": vocabulary,
        "doc_prompt_tokens": doc_prompt_tokens,
        # Where the flat vocabulary artifacts sit, for entities[].pid resolution in
        # write_document_record(). Derived from the same VOCAB_PATH the prompt vocabulary
        # was loaded from, so the two can never point at different harvests.
        "vocab_dir": os.path.dirname(vocab_path) or ".",
    }


#: Readiness/draining/in-flight state for the §4.6 disposability contract (issue #55).
_state = ServiceState()


def _engine_is_serviceable() -> bool:
    """Whether the warmed engine can actually answer a request.

    Same expression /info's ``ready`` field reports, kept in one place. This — not
    merely "startup finished" — is what ``_state.warm`` is set from, because a
    misconfigured backend here is recorded rather than fatal (see ``lifespan``): the
    process deliberately stays up so ``/info`` and ``/docs`` still explain what is
    wrong. Marking such a pod *ready* would then route traffic to a service that
    503s on every request; leaving it un-ready keeps it alive but drains it from the
    load balancer, which is the behaviour a Kubernetes readinessProbe exists for.
    """
    return not _engine.get("error") and bool(_engine.get("line_chat_fn"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the backend once; a misconfigured backend is recorded, not fatal.
    loop = asyncio.get_event_loop()
    try:
        _engine.update(await loop.run_in_executor(None, _load_engine))
        logger.info("llm-enrich engine ready (backend=%s)", _engine.get("backend"))
    except Exception as exc:
        _engine.clear()
        _engine["error"] = str(exc)
        logger.warning("llm-enrich engine warmup failed: %s", exc)
    _state.warm = _engine_is_serviceable()
    # issue #55: composes with the warmup above rather than replacing it. Flips /ready
    # to 503 on SIGTERM and, on shutdown, waits for in-flight requests before the
    # `_engine.clear()` below tears the backend out from under them.
    async with serve_lifecycle(_state):
        yield
    _engine.clear()


app = FastAPI(
    title="ATRIUM llm-enrich API",
    version=read_tool_version(Path(__file__).resolve().parent),
    description="LLM-based archaeological keyword extraction over text lines / documents.",
    lifespan=lifespan,
    # The typed contract (atrium-project#32 round 2): every route documents the §4.4 error
    # body for 422 and 500 (and FastAPI's own 422 body, which is not what is sent, goes);
    # operationIds are the handler names; the spec never depends on a root_path.
    responses=error_responses(422, 500),
    generate_unique_id_function=operation_id,
    root_path_in_servers=False,
)
attach_inflight_middleware(app, _state)
# §4.4 error body {status, reason, detail} for every error (atrium-project#32 item 2, #53).
attach_error_handlers(app)
# The published spec: reason registry, record schema, service id (atrium-project#32 item 3).
attach_openapi_contract(app, SERVICE)

# CORS — standard §4.5 configuration (ALLOWED_ORIGINS CSV, default "*").
add_cors(app, methods=["GET", "POST"])

# Demo frontend (§9) — served at /frontend when the directory is present.
#
# Guarded by `.exists()` on purpose, so this block is a no-op wherever
# `service/frontend/` was not shipped. That is what makes it safe on every branch:
# the page currently lives only on `agent-skill`, and this same code changes nothing
# on a branch without it, so there is no fork to forward-merge later.
#
# Until 2026-09-09 the branch README advertised a frontend "mounted at `/frontend`"
# while nothing mounted anything — the page shipped unreachable. The skill-validate
# endpoint check could not catch it: step 2 skips `/`-rooted tokens, and step 4's
# `GET /x` pattern deliberately ignores a bare backticked `/frontend` precisely
# because that form also names slash-commands and static mounts.
_frontend_dir = Path(__file__).resolve().parent / "frontend"
if _frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


def _deep_health() -> str | None:
    """Deep readiness (§4.1): the LLM backend warmed up and a chat_fn is bound."""
    if _engine.get("error"):
        return f"backend not configured: {_engine['error']}"
    if not _engine.get("line_chat_fn"):
        return "engine not initialized"
    return None


attach_health(app, deep_check=_deep_health, state=_state)


def _require_engine() -> Dict[str, Any]:
    """Return the warmed engine, or 503 if the backend is not ready (§4.4 → client retries)."""
    if _state.draining:
        # issue #55: stop accepting NEW work the moment a shutdown signal arrives, so the
        # drain has a bounded set of requests to wait for. /ready has already flipped to
        # 503 by now, but a request already in the accept queue can still arrive here.
        raise HTTPException(
            503, "Service is shutting down; retry against a live replica."
        ) from None
    if _engine.get("error"):
        raise HTTPException(503, f"LLM backend not ready: {_engine['error']}") from None
    if not _engine.get("line_chat_fn"):
        raise HTTPException(503, "LLM backend not initialized.") from None
    return _engine


def _doc_id(filename: str) -> str:
    """The uploaded document's identity, derived the one canonical way.

    Delegates to ``atrium_document.canonical_doc_id()`` (atrium-project#10, D3): this was a
    THIRD independent suffix-stripper in this repo, alongside
    ``api_util/teitok_read.doc_id_from_path`` and the two batch clients' ``Path.stem``. The
    accretion contract is keyed on ``doc_id``, so a service that re-keys the record it was
    handed silently orphans the caller's baseline — which is exactly what D1/D2 did in the
    batch clients and in alto's ``/process``.
    """
    return canonical_doc_id(filename)


def _run_extraction(
    tmp_path: str,
    filename: str,
    engine: Dict[str, Any],
    doc_id: str,
    document_record_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Blocking enrichment call, dispatched by file extension (line vs document level).

    When ``document_record_dir`` is given, also folds the results into this document's
    paired ATRIUM record (accretion model, docs/document_schema.md / issue #13): a baseline
    at ``<document_record_dir>/<doc_id>.document.json``, if the caller placed one there, is
    read and written back with only llm-enrich's ``enrichment`` block updated — every other
    tool's block passes through untouched (rule 2). With no baseline present the record holds
    just llm-enrich's own part (rule 3), mirroring ``llm_run.py``/``openrouter_client.py``/
    ``ollama_client.py``'s ``write_document_record`` call. A run that CONSULTED the model
    and located nothing still writes the block, with an empty ``items`` list — "ran and
    found nothing" is a different fact from "never ran", and only the record can carry
    that difference (atrium-project#49). A run that never reached the model (every row
    dropped by the quality filter) or whose every call failed writes nothing, same as
    those batch entry points.

    The Layer D schema gate (atrium-project#10, D4) lives in ``write_document_record()`` —
    the repo's single write chokepoint — so an invalid record is never written here either.
    What this function adds is the gate on the record it hands BACK: see below.

    Every call has its paradata (atrium-project#71), with or without a record: a logger that
    writes no file, whose ``run_id`` / ``run_uuid`` stamp the record and whose run comes back
    as the result's ``paradata``, a ``CreateAction``. It used to exist only when a record was
    written, and wrote a file into the request's temporary directory that nobody read.
    """
    from llm_client_shared import (
        ReplyTruncated,
        contributes_document_record,
        run_document_level,
        run_line_level,
        schema_gate,
        write_document_record,
    )

    name = filename.lower()
    path = Path(tmp_path)
    mode = "line" if name.endswith(_LINE_SUFFIXES) else "document"
    para_logger = ParadataLogger(
        program="llm-enrich-api",
        config={"mode": mode, "backend": engine["backend"], "model": engine["model"]},
        paradata_dir=None,
        output_types=["json"],
        config_dir=_PARA_CONFIG_DIR,
    )
    notes = LimitNotes()
    if mode == "line":
        records, stats = run_line_level(
            path,
            engine["line_chat_fn"],
            engine["line_prompt"],
            engine["line_model"],
            **engine["filter_params"],
            max_consecutive_errors=LLM_MAX_CONSECUTIVE_ERRORS.get(),
        )
        _note_line_limits(stats, notes)
    else:  # validated to be a _DOC_SUFFIXES file by the caller
        _check_document_fits(path, engine)
        try:
            records, stats = run_document_level(
                path, engine["doc_chat_fn"], engine["doc_prompt"], engine["doc_model"], strict=True
            )
        except ReplyTruncated as exc:
            raise LLM_MAX_NEW_TOKENS.exceeded(
                None,
                value=exc.max_new_tokens,
                detail=(
                    f"The model's reply was cut at {exc.max_new_tokens} tokens "
                    "(LLM_MAX_NEW_TOKENS) and is not used: this document yields more than one reply "
                    "can hold. Split it, send it as lines (.csv), or raise LLM_MAX_NEW_TOKENS."
                ),
            ) from exc
    notes.extend((engine.get("vocab_notes") or {}).get(mode, ()))

    result: Dict[str, Any] = {
        "mode": mode,
        "results": records,
        "stats": stats,
        # Every limit that shaped this result without refusing it (atrium-project#53).
        "limits_applied": notes.as_list(),
    }

    para_logger.note_limits(notes)
    if document_record_dir is not None and contributes_document_record(records, stats):
        from atrium_document import load_document

        try:
            record_path = write_document_record(
                doc_id,
                records,
                document_record_dir,
                run_id=para_logger.run_id,
                run_uuid=para_logger.run_uuid,
                paradata_ref=para_logger.paradata_ref,  # the run_uuid: the service writes no file
                # The service never renders a record: the recipe is written only when the
                # record can be rendered (a .txt upload with no baseline gets none), and
                # its detail is `full`, what a caller's json_to_md render defaults to.
                used_markdown_input=(mode == "document"),
                license_detail=para_logger.get_license_block(),
                # `.get`, not `[...]`: the contract tests build engine dicts by hand,
                # and an absent key must fall back to resolve_pid's default rather
                # than KeyError inside the record write.
                vocab_dir=engine.get("vocab_dir"),
            )
        except RuntimeError as exc:
            # The Layer D refusal (D4). Translated here rather than left to
            # _extract_from_path's blanket `RuntimeError -> 502 LLM backend error`,
            # which would blame the upstream provider for a record WE built wrong —
            # and 502 invites a retry that would fail identically. A record llm-enrich
            # cannot emit is a defect on this side, so it is a 500, named as such.
            raise HTTPException(500, f"Document record rejected by its own schema: {exc}") from exc
        if record_path is not None:
            para_logger.log_success("json")

        if record_path is not None:
            record = load_document(str(record_path))
            # Layer D on the way OUT (atrium-project#10, D4). write_document_record() has
            # already refused to emit a record whose invalidity was ours, so anything caught
            # here is a record it deliberately let through because the caller's own uploaded
            # baseline did not validate. Re-raising would contradict that decision (and 500
            # on somebody else's bad data), and returning it in silence is what D4 is about —
            # so the response says so, in a field an automated caller can test instead of
            # grepping the service log.
            schema_error = schema_gate(record, f"{doc_id}{FILE_SUFFIX}")
            if schema_error:
                logger.warning(
                    "returned document record for %s does not validate against the ATRIUM "
                    "document schema: %s",
                    doc_id,
                    schema_error,
                )
                result["document_json_schema_error"] = schema_error
            result["document_json"] = record

    para_logger.log_document_success()
    para_logger.finalize()
    result["paradata"] = _run_action(
        para_logger, path, filename, result, document_record_dir is not None, doc_id
    )
    return result


def _run_action(
    run: ParadataLogger,
    upload_path: Path,
    upload_name: str,
    result: Dict[str, Any],
    baseline_sent: bool,
    doc_id: str,
) -> Dict[str, Any]:
    """The call's CreateAction (atrium-project#71): what it read and what it wrote.

    `object` is the upload and, when one was sent, the record; `result` is the record's blocks
    this call stamped and the keyword results it answers with.
    """
    record = result.get("document_json")
    upload = atrium_rocrate.file_entity(
        upload_name,
        upload_path.read_bytes(),
        media_type=_MEDIA_TYPES.get(Path(upload_name.lower()).suffix),
    )
    inputs = [upload]
    if baseline_sent:
        inputs.append(atrium_rocrate.record_entity(str((record or {}).get("doc_id") or doc_id)))
    outputs = atrium_rocrate.block_entities(atrium_rocrate.blocks_written(record, run.run_uuid))
    answer = json.dumps(result["results"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    outputs.append(
        atrium_rocrate.file_entity("results.json", answer, media_type="application/json")
    )
    return atrium_rocrate.create_action(run.record, inputs=inputs, outputs=outputs)


def _check_document_fits(path: Path, engine: Dict[str, Any]) -> None:
    """Refuse a document that cannot fit one call, before the call (atrium-project#53).

    The document prompt (with the vocabulary), the document and the reply
    (``LLM_MAX_NEW_TOKENS``) must fit ``LLM_CONTEXT_WINDOW``, estimated at 4 characters per
    token. A document over it used to be sent anyway and come back as 200 with no results.
    """
    from llm_client_shared import approx_token_count

    window = tool_limits.context_window()
    reply = LLM_MAX_NEW_TOKENS.get()
    prompt = engine.get("doc_prompt_tokens")
    if prompt is None:
        prompt = approx_token_count(engine.get("doc_prompt", ""))
    document = approx_token_count("DOCUMENT:\n" + path.read_text(encoding="utf-8"))
    needed = prompt + document + reply
    if needed > window:
        raise LLM_CONTEXT_WINDOW.exceeded(
            needed,
            value=window,
            detail=(
                f"Document too long for one call: about {document} tokens, with the "
                f"{prompt}-token prompt and {reply} tokens kept for the reply, is over the "
                f"{window}-token context window (LLM_CONTEXT_WINDOW; /info "
                "limits.document_input_budget_tokens says how much a document may have). "
                "Split it, or send it as lines (.csv)."
            ),
        )


def _note_line_limits(stats: Dict[str, int], notes: LimitNotes) -> None:
    """The line-mode limits that shaped a result, from ``run_line_level``'s counts."""
    if stats.get("truncated"):
        notes.note(
            LLM_MAX_NEW_TOKENS,
            "skipped",
            stats["truncated"],
            "line(s) whose reply was cut at LLM_MAX_NEW_TOKENS got no result",
        )
    if stats.get("aborted"):
        notes.note(
            LLM_MAX_CONSECUTIVE_ERRORS,
            "stopped",
            1,
            f"the document was given up after LLM_MAX_CONSECUTIVE_ERRORS failed lines in a row; "
            f"{stats.get('unprocessed', 0)} line(s) after them were not sent",
        )


def _inline_doc_id(document_json: Optional[Dict[str, Any]]) -> str:
    """The key for an ``/extract_keywords_text`` call: the sent record's ``doc_id``.

    Inline text has no filename to derive an id from, so without a record the call is keyed
    ``inline_text``. With one, the record's own id is the key (atrium-project#68): the
    response then names the document it enriched. The id also names the baseline's file in
    the request's temp dir, so only a plain file name short enough to be one (255 bytes, less
    the suffix) is taken; anything else falls back to ``inline_text``. The record itself keeps
    its id either way (DocumentRecord inherits it).
    """
    doc_id = (document_json or {}).get("doc_id")
    if (
        not isinstance(doc_id, str)
        or doc_id in ("", ".", "..")
        or not doc_id.isprintable()  # control characters, NUL, lone surrogates
        or "/" in doc_id
        or "\\" in doc_id
        or len(doc_id.encode("utf-8")) > 200
    ):
        return "inline_text"
    return doc_id


def _envelope(engine: Dict[str, Any], doc_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "doc_id": doc_id,
        "backend": engine["backend"],
        "model": engine["model"],
        **payload,
    }


async def _extract_from_path(
    tmp_path: str,
    filename: str,
    engine: Dict[str, Any],
    doc_id: str,
    document_record_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None, _run_extraction, tmp_path, filename, engine, doc_id, document_record_dir
        )
    except LimitExceeded:  # not a ValueError, so the 422 below cannot swallow it
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (csv.Error, ET.ParseError) as exc:
        # A malformed CSV or TEITOK upload is the caller's input, not our failure: it used to
        # fall through to the blanket 500 (atrium-project#32 round 2). ET.ParseError is a
        # SyntaxError, and csv.Error an Exception, so neither is caught as a ValueError above.
        raise HTTPException(422, f"The upload could not be read: {exc}") from exc
    except RuntimeError as exc:
        # chat_fn exhausted its retries against the upstream LLM, or the provider refused
        # the request (its reply is in the message) — the backend's error (§4.4).
        raise HTTPException(502, f"LLM backend error: {exc}") from exc


@app.get(
    "/info",
    response_model=None,
    responses={200: {"model": LlmInfo, "description": "Identity, limits, capabilities."}},
)
async def info() -> Dict[str, Any]:
    """Service identity and capabilities (§4.1)."""
    return build_info(
        app,
        service=SERVICE,
        limits=LIMITS,
        backend=_engine.get("backend"),
        model=_engine.get("model"),
        ready=_engine_is_serviceable(),
        # How much of the vocabulary reaches the model (atrium-project#53): `terms`, and how
        # many of them fit each prompt; null until the engine is warm.
        vocabulary=_engine.get("vocabulary"),
        supported_inputs=[*_LINE_SUFFIXES, *_DOC_SUFFIXES],
        languages=["cs", "en"],
    )


@app.post(
    "/extract_keywords",
    response_model=None,
    responses={
        200: {
            "model": ExtractResponse,
            "description": "The keywords, and the record when one was sent.",
        },
        **error_responses(413, 415, 502, 503),
    },
)
async def extract_keywords(
    file: UploadFile = File(  # noqa: B008
        ...,
        description="The document: .csv or .teitok.xml (line mode), .md or .txt (document mode).",
    ),
    document_json: UploadFile = File(  # noqa: B008
        None,
        description=_RECORD_PART_HELP,
        json_schema_extra={"contentMediaType": "application/json"},
    ),
):
    """Extract archaeological keywords from an uploaded document (§4.2).

    ``.csv`` / ``*.teitok.xml`` → line-level (one record per qualifying line);
    ``.md`` / ``.txt`` → document-level (one record set per document). Each record's
    ``enrichment`` carries ``extracted_keywords_cs`` / ``extracted_keywords_en``.
    """
    engine = _require_engine()
    if not file.filename:
        raise HTTPException(422, "Filename is missing from the upload.") from None
    name = file.filename.lower()
    if not name.endswith(_LINE_SUFFIXES + _DOC_SUFFIXES):
        # §4.4: a type this endpoint does not read is 415 `unsupported_media_type` (it was a
        # bare 422 before atrium-project#32 round 2), with the accepted suffixes in the body.
        raise AtriumHTTPError(
            415,
            f"Unsupported file type. Accepted: {', '.join(_LINE_SUFFIXES + _DOC_SUFFIXES)}.",
            reason="unsupported_media_type",
            accepted=[*_LINE_SUFFIXES, *_DOC_SUFFIXES],
        ) from None

    upload_mb = MAX_UPLOAD.get()
    data = await read_upload_bounded(file, upload_mb, "File")

    doc_id = _doc_id(file.filename)
    suffix = ".teitok.xml" if name.endswith(".teitok.xml") else Path(name).suffix

    with tempfile.TemporaryDirectory() as tmp_dir:
        work_dir = Path(tmp_dir)
        tmp_path = work_dir / f"input{suffix}"
        tmp_path.write_bytes(data)

        document_record_dir: Optional[Path] = None
        if document_json is not None:
            # Bounded like the file (atrium-project#53): it used to be read whole, unbounded.
            baseline_bytes = await read_upload_bounded(document_json, upload_mb, "document_json")
            # A record that cannot be opened is refused before any model call (422
            # `invalid_record`); an empty part counts as none. The bytes are written as sent.
            if parse_record_part(baseline_bytes, "document_json") is not None:
                (work_dir / f"{doc_id}{FILE_SUFFIX}").write_bytes(baseline_bytes)
                document_record_dir = work_dir

        result = await _extract_from_path(
            str(tmp_path), file.filename, engine, doc_id, document_record_dir
        )

    return _envelope(engine, doc_id, result)


@app.post(
    "/extract_keywords_text",
    response_model=None,
    responses={
        200: {
            "model": ExtractResponse,
            "description": "The keywords, and the record when one was sent.",
        },
        **error_responses(413, 502, 503),
    },
)
async def extract_keywords_text(request: Request, payload: ExtractTextRequest):
    """Extract archaeological keywords from inline text (§4.2).

    The body is ``{"text": ..., "document_json": ...}``, as before atrium-project#32 round 2:
    the two embedded ``Body`` parameters became one named request model, so a generated client
    gets a type for it; the wire shape did not change.
    """
    # The body is bounded like an upload (atrium-project#53): it had no size limit at all.
    await check_body_size(request, MAX_UPLOAD.get(), "Request body")
    engine = _require_engine()

    text = payload.text
    document_json = parse_record_part(payload.document_json, "document_json")
    doc_id = _inline_doc_id(document_json)

    with tempfile.TemporaryDirectory() as tmp_dir:
        work_dir = Path(tmp_dir)
        tmp_path = work_dir / "input.txt"
        tmp_path.write_text(text, encoding="utf-8")

        document_record_dir: Optional[Path] = None
        if document_json is not None:
            baseline_path = work_dir / f"{doc_id}{FILE_SUFFIX}"
            baseline_path.write_text(json.dumps(document_json), encoding="utf-8")
            document_record_dir = work_dir

        result = await _extract_from_path(
            str(tmp_path), "input.txt", engine, doc_id, document_record_dir
        )

    return _envelope(engine, doc_id, result)


if __name__ == "__main__":
    import logging
    import os
    import sys

    import uvicorn

    # (12-factor XI) Logs are an event stream: emit to stdout and let the supervisor
    # route them. The library modules only getLogger(); this is the one place allowed
    # to configure handlers. The format string is alto-postprocess's, verbatim, in all
    # five services — a partner tailing five logs wants one shape, and format drift is
    # never fixed later. (issue #61)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # (12-factor VII) The service exports itself by binding a port, and which port is
    # configuration. This was baked into an exec-form ENTRYPOINT array, where no shell
    # exists to expand a variable even if one is set — while the reference manifest we
    # hand ARÚP/ARÚB (atrium-project docs/templates/k8s/atrium-service.deployment.yaml)
    # declares `env: PORT` and service/healthcheck.py already reads it. Setting PORT
    # therefore moved the health PROBE and not the listener, so the container reported
    # unhealthy forever rather than simply ignoring the knob. (issue #58)
    reload = os.getenv("RELOAD", "false").strip().lower() in ("true", "1", "yes", "on")

    # uvicorn needs an IMPORT STRING to respawn workers on reload; everywhere else the
    # app OBJECT is correct and strictly better. Passing a string under the container
    # entrypoint (`python -m service.api`) re-imports this module under its real name
    # while it is already running as __main__: the whole body executes twice, and the
    # copy uvicorn serves is not the one __main__ built. __spec__ is None under a direct
    # `python api.py` from service/ (service/README.md's documented start), where no
    # import string resolves anyway — so reload degrades to a uvicorn warning there
    # instead of silently pretending to be on.
    _app_ref = f"{__spec__.name}:app" if reload and __spec__ is not None else app

    uvicorn.run(
        _app_ref,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=reload,
        # (12-factor IX) Disposability: this is the `--timeout-graceful-shutdown 20`
        # that moved off the ENTRYPOINT line when the port became configurable. It
        # bounds uvicorn's wait for in-flight requests; serve_lifecycle() adds its own
        # drain on top, and docs/k8s_deployment.md in the hub carries the full grace
        # budget the two have to fit inside. (issue #55)
        timeout_graceful_shutdown=int(os.getenv("GRACEFUL_SHUTDOWN_S", "20")),
    )
