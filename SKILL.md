---
name: atrium-digital-convert
description: Converts born-digital documents (PDF, DOCX, ODT, ODS, XLSX, RTF; DOC and XLS through LibreOffice) into the ATRIUM document record - pages, lines with their boxes (PDF), tables and the text-layer verdict of every page - and, on request, assesses every page and routes it to NLP, OCR, HTR or nothing, asking page-classification and ocr-postprocess when the server knows their URLs. Use this skill for born-digital archival documents before NLP enrichment, to find which pages need OCR instead, or to turn a document into annotated Markdown.
---

# ATRIUM digital-convert Skill 📄

This skill gives agents access to the **ATRIUM digital-convert** service: the born-digital stage of the ATRIUM
pipeline. It reads a document's own text layer instead of running OCR on it, and says, page by page, whether that
text can be used. It follows a **server-client** design: a FastAPI server (in `service/`) does the conversion, and
a zero-dependency client script (`scripts/atrium_convert.py`) is the only thing the agent calls directly.

## Operational Requirements ⚙️

- **Server**: a running instance is required. Default `http://localhost:8000`; override with `--base-url` or the
  `ATRIUM_DC_URL` environment variable. `ATRIUM_DC_URL` is *inbound*, where the client finds this service. It is
  unrelated to the server's *outbound* `PAGE_CLASSIFICATION_URL` and `OCR_POSTPROCESS_URL`, which say where
  `/describe` finds the two other stages.
- **Client dependencies**: none - Python 3 standard library only.
- **Server dependencies**: Docker (recommended, compose `api` profile; the image includes LibreOffice) or a
  Python venv with `service/requirements.txt` (which installs `requirements.txt` and `requirements_digital.txt`).
  A local server reads DOC and XLS only when LibreOffice (`soffice`) is on its PATH; without it they answer 501.
- **First launch**: no model and no download; the first Docker start pulls or builds the image. Ready in
  seconds after that.
- **Limits**: 200 MB per file (`MAX_UPLOAD_MB`), 2,000 pages (`MAX_PAGES`), 2 conversions at once
  (`MAX_CONCURRENT_JOBS`; HTTP 429 with `Retry-After` when busy). A large report takes up to a minute with
  `/reformat`, and up to about five minutes with `/describe` and both stages.
- **Readiness**: `GET /health` is liveness (stays 200 while draining); `GET /ready` is the readiness probe, 503
  before start-up checks pass and while shutting down. `GET /info` reports the version, limits, the accepted types,
  whether LibreOffice is present and which stages are configured.

## Text layers and routes 📄

Every page of the record carries `text_layer`, the converter's verdict on its text:

| `text_layer` | Meaning                                                                   | `needs_ocr` |
|--------------|---------------------------------------------------------------------------|-------------|
| `digital`    | the text decodes; use it                                                  | no          |
| `garbled`    | a text layer exists but does not decode (wrong encoding, unmapped glyphs) | yes         |
| `ocr`        | the text is an earlier OCR run's invisible layer over a page image        | yes         |
| `none`       | no text layer, or only a running header or footer over an image           | yes         |
| `blank`      | an empty page of a format without page images                             | no          |

A PDF most of whose text-bearing pages are an earlier OCR run is refused (422 `ocr_text_layer`): send it to the
OCR route instead.

With `--describe`, every page also gets a `route`: `nlp` (its text is good), `ocr` (printed or typed text to read
again), `htr` (handwriting) or `none` (nothing to read: a drawing, a photograph, a blank page). `summary.reacquire_pages`
lists the pages for OCR or HTR. That list can also include pages whose text decodes but lost letters to unmapped
glyphs, which `needs_ocr` alone misses.

## Workflows 🪄

### 1. Ensure the server is running

```bash
bash scripts/server.sh          # Docker Compose api profile (or the local fallback)
bash scripts/server.sh --local  # force a local server in venv-dc (no Docker)
```

Idempotent: exits at once if `GET /ready` already answers 200.

### 2. Convert

```bash
# One PDF: the record, and one row per page (text layer, lines, needs OCR or not)
python3 scripts/atrium_convert.py small_data_samples/enrichable.pdf

# A text page, a scanned page and a blank page: two flagged needs_ocr
python3 scripts/atrium_convert.py small_data_samples/image_only.pdf

# A DOCX, with the record and annotated Markdown written to a directory
python3 scripts/atrium_convert.py small_data_samples/minimal.docx --markdown --out-dir out/

# With the AMČR seed record, which names the document and its digest
python3 scripts/atrium_convert.py report.pdf --seed report.seed.json --out-dir out/

# DOCX page breaks: the document's own (explicit), none, or auto (default)
python3 scripts/atrium_convert.py report.docx --docx-page-breaks explicit

# Discover capabilities, limits and configured stages
python3 scripts/atrium_convert.py --info
```

### 3. Assess and route every page

```bash
# Per page: text layer, page type, text quality, route and reason
python3 scripts/atrium_convert.py small_data_samples/image_only.pdf --describe

# The converter's own evidence only, without calling the other stages
python3 scripts/atrium_convert.py report.pdf --describe --stages none

# Ask page-classification about every page, not only those without usable text
python3 scripts/atrium_convert.py report.pdf --describe --classify-pages all --no-text --format json
```

A stage the server has no URL for is reported as `not_configured`, and the request still succeeds. The route then
rests on the converter alone.

### 4. Interpret the output

- **Table and CSV** (`--format csv`): `/reformat` gives FILE, PAGE, TEXT_LAYER, LINES, NEEDS_OCR and REASON;
  `/describe` gives FILE, PAGE, TEXT_LAYER, CATEGORY (with its confidence), QUALITY (band and source), ROUTE and
  REASON. The table cuts the last column to 70 characters; CSV and JSON keep it whole.
- **JSON** (`--format json`): the server's whole answer per document, keyed by `doc_id`. It holds the record
  (`document_json`), the reader details (`reader`), the run's provenance (`paradata`), and with `--describe` the
  `pages`, `summary` and `stages`.
- **Files** (`--out-dir`): `<doc_id>.document.json`, and `<doc_id>.md` with `--markdown`.
- One summary line per document goes to stderr; stdout stays clean for CSV and JSON.

## Agent Guidelines 🤖

1. **Route by the verdict, not by the file type.** Send pages with `needs_ocr` (and, after `--describe`, every
   page in `summary.reacquire_pages`) to OCR. Use the text of `digital` pages as it is.
2. **Report what the server could not do.** A `/describe` stage that is `not_configured`, `unavailable`, `error`
   or `rejected` means the route rests on less evidence; say so rather than presenting the route as certain.
3. Prefer `--format json` when the result feeds further processing; pass the record on with `--out-dir`.
4. For full request and response schemas, fetch `GET /openapi.json` from the running server (Swagger UI at
   `/docs`); `service/README.md` is the runbook.
5. Exit code `2` (unreachable): start the server (`bash scripts/server.sh`) and retry once. Exit code `3` (server
   error): the client has already retried a 503 or 504, and a 502 that is not final, three times. Read the hint
   printed under the error, check `GET /health?deep=true` and the server log, and do not loop.
6. **Refusals name their fix.** 415: not a born-digital type (a scan goes to OCR). 422 `ocr_text_layer`: send the
   document to OCR. 422 `source_digest_mismatch`: the seed was made for another file. 501: no LibreOffice for DOC
   or XLS on this server. 429: wait for `Retry-After`.
7. **Size:** files over 200 MB are refused by default. Ask the operator to raise `MAX_UPLOAD_MB`, or split the
   document, and tell the user which you did.
8. Do not bypass the API by importing the converter directly. Every call returns its provenance as `paradata`, a
   Process Run Crate `CreateAction`; the service writes no paradata file.

## Acknowledgements & Citations 🙏

Developed within the [ATRIUM](https://atrium-research.eu/) project at ÚFAL, Charles University, for the
Archaeological Map of the Czech Republic (AMČR). If you use this service for research, cite the repository's
`CITATION.cff`.
