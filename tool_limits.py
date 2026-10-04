"""tool_limits.py — every limit and setting atrium-digital-convert has (atrium-project#53, factor III).

One declaration, read by the service (``service/api.py``), the converter
(``api_util/digital_to_json.py`` and its adapters) and the vendored reader
(``text_formats.py``, which imports ``ODF_REPEAT_CAP`` and ``PDF_OBJECT_CAP`` from here exactly as
it does in atrium-ocr-postprocess), and reported by ``GET /info`` (``limits`` and
``limits_meta``). Each one is an environment setting; a malformed value stops the process at
startup, naming the variable (``atrium_limits.LimitConfigError``). ``.env.example`` and
``service/README.md``'s ``## Limits`` table list the same set; ``tests/test_limits_contract.py``
checks that they agree.

What happens over each limit — refused (with the HTTP status), or a policy threshold — is said
beside it. Two entries are POLICY thresholds rather than size caps (``OCR_LAYER_DOCUMENT_SHARE``,
``ROUTE_TRASH_SHARE``): they are declared here all the same, because a value a deployment may
change and a client must be able to read back is exactly what ``/info`` ``limits`` is for.

Standard library only (``atrium_limits`` is the hub's canonical module at the repo root): the
CLI converter imports this too.
"""

from __future__ import annotations

from atrium_limits import LimitSet, limit, upload_limit

#: §4.5 upload limit, per uploaded part (the file, `document_json`). Over it → 413
#: ``limit_exceeded``. Born-digital PDFs with embedded images run larger than OCR text.
MAX_UPLOAD = upload_limit(50)

#: Pages one document may have. Counted before the text is read (a PDF's page tree; a DOCX,
#: ODT or spreadsheet after reading). Over it → 422 ``limit_exceeded``: split the document.
MAX_PAGES = limit("MAX_PAGES", 2000, unit="pages", minimum=1, status=422)

#: Conversions the service runs at once. A request over it → 429 ``busy`` with ``Retry-After``
#: (the conversion is CPU-bound and runs in a worker thread; queuing it would only move the wait).
MAX_CONCURRENT_JOBS = limit("MAX_CONCURRENT_JOBS", 2, unit="jobs", minimum=1)

#: Seconds headless LibreOffice may take to convert one legacy DOC or XLS file. Over it → 422
#: ``limit_exceeded`` (a per-input processing budget, not an upstream service).
LIBREOFFICE_TIMEOUT_S = limit(
    "LIBREOFFICE_TIMEOUT_S", 120, unit="s", kind=float, minimum=1, status=422
)

#: Seconds `/describe` waits for one call to an adjacent stage (page-classification,
#: ocr-postprocess). Over it the stage is reported ``unavailable``; the request still succeeds.
STAGE_TIMEOUT_S = limit("STAGE_TIMEOUT_S", 120, unit="s", kind=float, minimum=1)

#: POLICY. Share of a PDF's text-bearing pages whose text layer is a prior OCR run's invisible
#: text at or above which the whole document is refused, 422 ``ocr_text_layer`` (the AMČR route
#: then sends it to OCR). Below it the document is converted and those pages carry
#: ``needs_ocr`` with a prior-OCR reason. Mirrors atrium-ocr-postprocess
#: ``default_source_origin``'s rule; 0.5 since llm-enrich 0.8.0.
OCR_LAYER_DOCUMENT_SHARE = limit(
    "OCR_LAYER_DOCUMENT_SHARE", 0.5, unit="share", kind=float, minimum=0, maximum=1, status=422
)

#: POLICY (`/describe` only). A page whose text layer decodes is routed to NLP — unless the
#: common quality model (ocr-postprocess) calls more than this share of its scored lines
#: ``Trash``; then the page is routed to OCR instead. Lines without a verdict do not count.
ROUTE_TRASH_SHARE = limit("ROUTE_TRASH_SHARE", 0.5, unit="share", kind=float, minimum=0, maximum=1)

# ── the vendored reader's own caps (text_formats.py, from atrium-ocr-postprocess) ────────────
#: Same variable names, units and defaults as atrium-ocr-postprocess's tool_limits.py: the reader
#: module is byte-identical there and here (tests/test_vendored_reader_parity.py), and imports
#: these two names from whichever ``tool_limits`` sits beside it.
#: Most a repeated ODF cell/row (`table:number-columns-repeated`) is expanded → ``trimmed`` note.
ODF_REPEAT_CAP = limit(
    "ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP", 100, unit="repeats", key="odf_repeat_cap", minimum=1
)
#: Most objects of one PDF page scanned to judge its text layer (the text itself is always
#: read in full); a page with more is judged on the first N → ``sampled`` note.
PDF_OBJECT_CAP = limit(
    "ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP", 20000, unit="objects", key="pdf_object_cap", minimum=1
)

LIMITS = LimitSet(
    MAX_UPLOAD,
    MAX_PAGES,
    MAX_CONCURRENT_JOBS,
    LIBREOFFICE_TIMEOUT_S,
    STAGE_TIMEOUT_S,
    OCR_LAYER_DOCUMENT_SHARE,
    ROUTE_TRASH_SHARE,
    ODF_REPEAT_CAP,
    PDF_OBJECT_CAP,
)
