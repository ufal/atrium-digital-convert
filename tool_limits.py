"""tool_limits.py — every limit and setting atrium-digital-convert has (atrium-project#53, factor III).

One declaration, read by the service (``service/api.py``), the converter
(``api_util/digital_to_json.py`` and its adapters) and the vendored reader
(``text_formats.py``, which imports ``ODF_REPEAT_CAP`` from here exactly as it does in
atrium-ocr-postprocess), and reported by ``GET /info`` (``limits`` and
``limits_meta``). Each one is an environment setting; a malformed value stops the process at
startup, naming the variable (``atrium_limits.LimitConfigError``). ``.env.example`` and
``service/README.md``'s ``## Limits`` table list the same set; ``tests/test_limits_contract.py``
checks that they agree.

What happens over each limit — refused (with the HTTP status), or a policy threshold — is said
beside it. Two entries are POLICY thresholds rather than size caps
(``FURNITURE_ONLY_IMAGE_COVERAGE``, ``BOLD_HEADING_MAX_SHARE``): they are declared here all the
same, because a value a deployment may change and a client must be able to read back is exactly
what ``/info`` ``limits`` is for. ``ROUTE_DAMAGED_WORD_SHARE`` (#5, 10-22) is gone: words that lose
letters route nothing since atrium-digital-convert#9. ``STAGE_TIMEOUT_S`` and ``ROUTE_TRASH_SHARE``
are gone since v2.0.0-beta: ``/describe`` calls no other tool, so it waits for no stage and has no
quality verdict to route by. ``RENDER_TEXT_COVERAGE`` is gone since v2.0.0-beta too: an office
page is not judged by how much of its rendering the text covers; it goes to the ATR when it has no
running text and shows an object (atrium-project#84 decision 3), which no threshold decides.

Standard library only (``atrium_limits`` is the hub's canonical module at the repo root): the
CLI converter imports this too.
"""

from __future__ import annotations

from atrium_limits import LimitSet, limit, upload_limit

#: §4.5 upload limit, per uploaded part (the file, `document_json`). Over it → 413
#: ``limit_exceeded``. Born-digital PDFs with embedded images run larger than OCR text: the AMČR
#: samples of atrium-digital-convert#5 reach 156 MB (250 since 2026-10-22, AMČR's operational
#: setting; 200 from the #5 run, 50 before). Larger originals (up to about 1.5 GB) are raised per
#: deployment and are not yet tested for memory, concurrency and timeouts. The upload is held in memory, and a conversion's own peak follows the document's text, not its size: the
#: server peaked at 783 MB on those 15 reports (a 282-page one; 560 MB for the 156 MB file) once
#: the light engine stopped holding every page's parse (1.9 GB before). Budget about 1 GB plus
#: the upload per ``MAX_CONCURRENT_JOBS`` slot.
MAX_UPLOAD = upload_limit(250)

#: Pages one document may have. Counted before the text is read (a PDF's page tree; a DOCX,
#: ODT or spreadsheet after reading). Over it → 422 ``limit_exceeded``: split the document.
MAX_PAGES = limit("MAX_PAGES", 2000, unit="pages", minimum=1, status=422)

#: Conversions the service runs at once. A request over it → 429 ``busy`` with ``Retry-After``
#: (the conversion is CPU-bound and runs in a worker thread; queuing it would only move the wait).
MAX_CONCURRENT_JOBS = limit("MAX_CONCURRENT_JOBS", 2, unit="jobs", minimum=1)

#: Seconds headless LibreOffice may take for one run: converting a legacy DOC or XLS file, or
#: rendering an office document (DOCX, DOC, ODT, RTF) to PDF for its pages
#: (atrium-digital-convert#9). Over it → 422 ``limit_exceeded`` (a per-input processing budget,
#: not an upstream service).
LIBREOFFICE_TIMEOUT_S = limit(
    "LIBREOFFICE_TIMEOUT_S", 120, unit="s", kind=float, minimum=1, status=422
)

#: POLICY. A PDF page whose only text is its running header or footer (a page number, a stamp)
#: is ``none`` — no text layer under the furniture, ``needs_ocr`` — when its images cover at
#: least this share of the page: a scan the PDF's producer numbered (atrium-digital-convert#5).
#: Below it the page stays ``digital``. Measured only on such pages (``api_util/pdf_coverage.py``).
FURNITURE_ONLY_IMAGE_COVERAGE = limit(
    "FURNITURE_ONLY_IMAGE_COVERAGE", 0.85, unit="share", kind=float, minimum=0, maximum=1
)

#: POLICY. A PDF line set in bold at the body size is a heading (one level below the size-based
#: ones) when it reads as a title: short, not ending in a comma, not a list item, at most two bold
#: lines in a row, not repeated on three pages. AMČR reports set their section titles that way, so
#: before this they reached `/reformat` as body text (atrium-digital-convert#5). The rule applies
#: only while bold text at the body size is at most this share of a document's body text: a
#: document that sets whole paragraphs in bold has no bold titles to find. 0 switches it off.
BOLD_HEADING_MAX_SHARE = limit(
    "BOLD_HEADING_MAX_SHARE", 0.25, unit="share", kind=float, minimum=0, maximum=1
)

#: Characters in one line of a text-only input (TXT, CSV, TSV, JSON) or a spreadsheet row, which
#: have no visual lines. A longer line is wrapped at a word boundary into lines of at most this
#: length, as atrium-ocr-postprocess's text path does (``text_formats.wrap_line``, the same
#: default), and each wrap is recorded in the paradata ``limits`` as a ``split`` (D13). 0 never wraps.
MAX_LINE_CHARS = limit("MAX_LINE_CHARS", 1000, unit="chars", minimum=0, status=422)

# ── the vendored reader's own caps (text_formats.py, from atrium-ocr-postprocess) ────────────
#: Same variable name, unit and default as atrium-ocr-postprocess's tool_limits.py: the reader
#: module is byte-identical there and here (tests/test_vendored_reader_parity.py), and imports
#: this name from whichever ``tool_limits`` sits beside it.
#: Most a repeated ODF cell/row (`table:number-columns-repeated`) is expanded; past it the
#: conversion fails (D13, atrium-project#84: ``api_util/digital_text.py``), as no content is cut.
ODF_REPEAT_CAP = limit(
    "ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP", 100, unit="repeats", key="odf_repeat_cap", minimum=1
)
#: (``ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP`` is gone: a PDF page's text layer is judged on all its objects,
#: never a sample of them, atrium-project#84 "never samples".)

LIMITS = LimitSet(
    MAX_UPLOAD,
    MAX_PAGES,
    MAX_CONCURRENT_JOBS,
    LIBREOFFICE_TIMEOUT_S,
    FURNITURE_ONLY_IMAGE_COVERAGE,
    BOLD_HEADING_MAX_SHARE,
    MAX_LINE_CHARS,
    ODF_REPEAT_CAP,
)
