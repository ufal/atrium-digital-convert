# digital-convert API service 📄

The HTTP service of the born-digital stage (`api-digital`, [#2](https://github.com/ufal/atrium-digital-convert/issues/2)).
It reads born-digital documents into the ATRIUM document record (`atrium_document`) and, on a
second endpoint, assesses every page: can its text be used as it is, what kind of page is it,
does its text read, and where should it go next.

* **`POST /reformat`** — the production endpoint the AMČR route calls. File (and optionally the
  AMČR seed) in, record out; Markdown on request; run `paradata`. **Calls no other service** —
  in the AMČR deployment Temporal chains the stages.
* **`POST /describe`** — the same conversion, then the per-page assessment, asking
  page-classification and ocr-postprocess **when their URLs are configured**. A stage that is
  not configured or not running is reported, never a failed request.

Inputs, by content (the extension only names the file): PDF, DOCX, ODT, ODS, XLSX, RTF, and the
legacy DOC/XLS through headless LibreOffice. No model, no network, no GPU.

## Quick start

```bash
pip install -r service/requirements.txt          # the converter stack + the web server
python -m service.api                            # binds $HOST:$PORT (0.0.0.0:8000)
# or, for development with auto-reload:
RELOAD=true python -m service.api
# or:
uvicorn service.api:app --port 8000
```

Docker: `docker compose --profile api up` (image `ghcr.io/ufal/atrium-digital-convert-api`).
To try `/describe` with stand-ins for the two stages: `docker compose --profile stub up`.

```bash
curl -F file=@report.pdf http://localhost:8000/reformat
curl -F file=@report.pdf -F markdown=true http://localhost:8000/reformat
curl -F file=@report.pdf -F document_json=@seed.json http://localhost:8000/reformat
curl -F file=@report.pdf http://localhost:8000/describe
curl -F file=@report.pdf -F stages=none http://localhost:8000/describe
```

## Endpoints

| Method | Path        | Purpose                                                                    |
|--------|-------------|----------------------------------------------------------------------------|
| GET    | `/info`     | identity, version, endpoints, limits and settings, capabilities (§4.1)     |
| GET    | `/health`   | liveness; `?deep=true` checks the readers and the record schema            |
| GET    | `/ready`    | readiness: 503 before start-up checks pass and while draining (issue #55)  |
| POST   | `/reformat` | born-digital file → record (+ Markdown on request)                         |
| POST   | `/describe` | born-digital file → record + per-page assessment + what each stage did     |

### `POST /reformat` (multipart form)

| Field              | Required | Meaning                                                                                                                                     |
|--------------------|----------|---------------------------------------------------------------------------------------------------------------------------------------------|
| `file`             | yes      | the document                                                                                                                                |
| `document_json`    | no       | the AMČR seed (`doc_id`, `source.sha512`, `filename`, `media_type`) or an earlier record of the same document; an empty part counts as none |
| `markdown`         | no       | `true` to also get the record rendered as annotated Markdown (`json_to_md`)                                                                 |
| `docx_page_breaks` | no       | DOCX pages: `auto` (default), `explicit`, `none`                                                                                            |

The response:

| Field                        | Meaning                                                                                                                                           |
|------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| `service`                    | `atrium-digital-convert`                                                                                                                          |
| `doc_id`                     | the seed's `doc_id`, else derived from the file name (`atrium_document.canonical_doc_id`)                                                         |
| `document_json`              | the record: digital-convert's `source`, `pages`, `content`, `lines`, `tables`; every other block of a sent record passed through                  |
| `reader`                     | how the file was read: `kind`, `media_type`, `origin`, `engine`, `pages`, `conversion` (DOC/XLS), `notes`                                         |
| `markdown`                   | only with `markdown=true`                                                                                                                         |
| `document_json_schema_error` | only when the sent record did not validate and the result inherits that                                                                           |
| `limits_applied`             | every limit that shaped the result without refusing it (`[]` today: each limit refuses)                                                           |
| `paradata`                   | the call's Process Run Crate `CreateAction` (atrium-project#71); its `@id` is the `run_uuid` stamped into the record. No paradata file is written |

**The seed.** Its `doc_id`, `source.sha512`, `filename` and `media_type` are kept; the converter adds
`source.origin` and `page_count` (and no `sha256` beside the archive's `sha512`). The seed's `sha512`
is compared with the uploaded bytes **before anything is read** — for a DOC/XLS, before the
LibreOffice conversion — and a mismatch is refused (422 `source_digest_mismatch`), so a record
never describes one file under another file's identity.

**The text layer.** A page whose embedded text does not decode (CP1250 read as CP1252, U+FFFD and
control characters), or that has no text layer, carries `pages[].needs_ocr: true` with a
`needs_ocr_reason`. A PDF whose text-bearing pages are at least `OCR_LAYER_DOCUMENT_SHARE` (0.5)
prior-OCR layers (invisible text over a page image) is refused, 422 `ocr_text_layer`, so the route
step sends it to OCR; below that share the document is converted and those pages are flagged.

### `POST /describe` (multipart form)

The fields of `/reformat` (without `markdown`), plus:

| Field            | Meaning                                                                                                                         |
|------------------|---------------------------------------------------------------------------------------------------------------------------------|
| `stages`         | a comma-separated subset of `page-classification`, `ocr-postprocess`; empty → every configured one; `none` → none               |
| `classify_pages` | `needs_ocr` (default): page-classification is asked about the pages without a usable text layer; `all`: about every page        |
| `include_text`   | `false` to leave `pages[].text` out                                                                                             |

The response is `/reformat`'s plus:

* `pages[]` — one per page:
  * `page`, `page_index`;
  * `text_layer` — `digital` (decodes), `garbled` (a layer that does not decode), `ocr` (a prior OCR run), `none` (no text layer), `blank` (an empty page of a format without page images);
  * `needs_ocr`, `needs_ocr_reason`;
  * `category` — page-classification's answer (`label`, `confidence`, `top`), or null;
  * `quality` — `source: ocr-postprocess` (the common line-quality model: `score`, `band`, `lines_by_category`, `lang`) or `source: digital-convert` (the decode check only);
  * `route` + `route_reason`, below;
  * `layout` — `canvas`, `lines`, `blocks`, `tables`, `headings`, `regions`, `columns`, `images`, `vector_paths`;
  * `text`.
* `summary` — `pages`, `routes` (pages per route), `needs_ocr_pages`, `reacquire_pages`, `document_route` (`nlp`, `ocr`, `htr`, `none` or `mixed`).
* `stages[]` — per stage: `status`, `detail`, `url`, `http_status`, `reason`, `elapsed_s`, `service_version`, `pages` sent, `record_adopted`, and the stage's own `paradata`.

**Routes.** Deterministic (`api_util/digital_report.py`):

| The page                                                                                                         | Route                                                                                                       |
|------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| its text layer decodes                                                                                           | `nlp`, unless ocr-postprocess calls more than `ROUTE_TRASH_SHARE` (0.5) of its scored lines `Trash` → `ocr` |
| no usable text layer, category handwritten only (`TEXT_HW`, `LINE_HW`)                                           | `htr`                                                                                                       |
| no usable text layer, printed, typed or tabular text (`TEXT_P`, `TEXT_T`, `TEXT`, `LINE_*`, `DRAW_L`, `PHOTO_L`) | `ocr`                                                                                                       |
| no usable text layer, graphical only (`DRAW`, `PHOTO`)                                                           | `none`                                                                                                      |
| no usable text layer, category unknown (classifier not run)                                                      | `ocr`                                                                                                       |
| blank                                                                                                            | `none`                                                                                                      |

Categories are read through the page-category facets of `atrium_vocab.COLLECTIONS`
(handwritten / printed / typed / graphical / tabular), so a category added to the registry is routed
without a code change.

**Stage statuses.** `ok`, `partial` (some pages failed), `not_configured` (its URL is unset),
`not_requested`, `skipped` (nothing to do: not a PDF, no page needs classifying, no lines),
`unavailable` (connection error, timeout `STAGE_TIMEOUT_S`, 429/5xx), `error` (another 4xx, or a
payload that is not the expected JSON), `rejected` (the guard below refused its record).

**What the stages may write.** Each stage is sent the current record and writes only its own fields
through its own accretion: page-classification `page_categories` and `pages[].category/
category_confidence`; ocr-postprocess `lines[].categ/quality_score/lang` and `pages[].quality_score/
quality_band`, never over a line carrying the converter's decode verdict (`Garbage`/`Inverted`).
The returned record is adopted only when it is the same document with the converter's part intact
— the same `doc_id`, `source`, `content`, `tables`, page and line rows, and the converter's own
fields (`text`, `bbox`, `group_id`, `style`, `page_index`, `canvas`, `needs_ocr`, `needs_ocr_reason`)
unchanged — and when it validates. Otherwise the stage is `rejected` (e.g. `page_key_mismatch`:
an older page-classification keys pages by physical number on a PDF whose pages carry labels)
and the record stays as it was; the stage's answer still goes into `pages[]`.

**Older stage versions.** A page-classification without the `pages` subset classifies every page:
only the requested ones are used. An ocr-postprocess without `/score_record` is asked page by page
through `/process` (`task_type=text`): its verdict goes into `pages[]`, not into the record.

## Errors

Every error has the harmonised body `{status, reason, detail}` (§4.4); `reason` is a registered
code or `null`, and refusals of a born-digital input name the converter's finer cause in `cause`.

| Status | `reason`                 | When                                                                                                                          |
|--------|--------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| 413    | `limit_exceeded`         | a part over `MAX_UPLOAD_MB`                                                                                                   |
| 415    | `unsupported_media_type` | not a born-digital type the converter reads (`cause`: `unsupported`, `legacy_office_unsupported`; `accepted` lists the types) |
| 422    | `ocr_text_layer`         | a PDF whose text layer is an earlier OCR run (route it to OCR)                                                                |
| 422    | `source_digest_mismatch` | the seed's `source.sha512` is not the uploaded file's                                                                         |
| 422    | `invalid_record`         | `document_json` cannot be opened (not UTF-8 JSON, not an object, a newer schema major)                                        |
| 422    | `limit_exceeded`         | over `MAX_PAGES` or `LIBREOFFICE_TIMEOUT_S`                                                                                   |
| 422    | `null`                   | a broken file (`cause`: `corrupt`, `encrypted`, `zip_limits_exceeded`, `conversion_failed`); an unknown `stages` name         |
| 429    | `busy`                   | `MAX_CONCURRENT_JOBS` conversions already running; retry after `Retry-After`                                                  |
| 501    | `null`                   | a DOC/XLS with no LibreOffice in this deployment (`cause`: `dependency_missing`)                                              |
| 503    | `null`                   | the service is shutting down; retry against a live replica                                                                    |

`source_digest_mismatch` is registered by this service beside the shared registry until the hub's
canonical `atrium_service.py` carries it; it is published in the spec like every other code.

## Configuration (environment)

| Variable                  | Default   | Meaning                                                                                   |
|---------------------------|-----------|-------------------------------------------------------------------------------------------|
| `PORT`                    | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes (issues #55, #58) |
| `HOST`                    | `0.0.0.0` | bind address (issue #58). ⚠️ see the warning below                                        |
| `GRACEFUL_SHUTDOWN_S`     | `20`      | seconds uvicorn waits for in-flight requests (issue #55)                                  |
| `RELOAD`                  | `false`   | filesystem auto-reload — development only                                                 |
| `LOG_LEVEL`               | `INFO`    | root logger level for the `python -m service.api` start path (issue #61)                  |
| `ALLOWED_ORIGINS`         | `*`       | CSV of CORS origins                                                                       |
| `PAGE_CLASSIFICATION_URL` | —         | base URL of atrium-page-classification, for `/describe`; unset → `not_configured`         |
| `OCR_POSTPROCESS_URL`     | —         | base URL of atrium-ocr-postprocess, for `/describe`; unset → `not_configured`             |
| `LIBREOFFICE_BIN`         | —         | the LibreOffice binary for DOC/XLS; unset → `soffice` or `libreoffice` on `PATH`          |

Every limit and setting — `MAX_UPLOAD_MB`, `MAX_PAGES`, `OCR_LAYER_DOCUMENT_SHARE` and the rest — is
listed under [Limits](#limits); `.env.example` is the complete ledger.

> ⚠️ `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`.

## Limits

Every limit and setting is an environment variable (atrium-project#53, factor III), declared in
`tool_limits.py` and reported with its current value by `GET /info` (`limits`; `limits_meta` says
which variable sets it and whether the value came from the environment or the default). A
malformed value stops the service at startup, naming the variable. `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)              | Variable                            | Default | Unit    | Over the limit                                                                                                              |
|----------------------------|-------------------------------------|---------|---------|-----------------------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`            | `MAX_UPLOAD_MB`                     | 50      | MB      | 413 `limit_exceeded` — per part (the file, `document_json`)                                                                 |
| `max_pages`                | `MAX_PAGES`                         | 2000    | pages   | 422 `limit_exceeded` — split the document                                                                                   |
| `max_concurrent_jobs`      | `MAX_CONCURRENT_JOBS`               | 2       | jobs    | 429 `busy` with `Retry-After`                                                                                               |
| `libreoffice_timeout_s`    | `LIBREOFFICE_TIMEOUT_S`             | 120     | s       | 422 `limit_exceeded` — a DOC/XLS LibreOffice did not convert in time                                                        |
| `stage_timeout_s`          | `STAGE_TIMEOUT_S`                   | 120     | s       | `/describe`: the stage is reported `unavailable`; the request still succeeds                                                |
| `ocr_layer_document_share` | `OCR_LAYER_DOCUMENT_SHARE`          | 0.5     | share   | policy: at or above it a PDF with prior-OCR pages is refused (422 `ocr_text_layer`); below it those pages carry `needs_ocr` |
| `route_trash_share`        | `ROUTE_TRASH_SHARE`                 | 0.5     | share   | policy (`/describe`): a decoding page goes to `ocr` instead of `nlp` when the quality model calls more of its lines `Trash` |
| `odf_repeat_cap`           | `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP` | 100     | repeats | the shared reader expands a repeated ODF cell or row at most this often                                                     |
| `pdf_object_cap`           | `ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP` | 20000   | objects | the shared reader judges a PDF page's text layer on its first N objects                                                     |

## How it works

`/reformat` and `/describe` run the same converter as the command line
(`api_util/digital_to_json.py`), in a worker thread, inside one of `MAX_CONCURRENT_JOBS` slots:

1. **Seed check** — the seed's `source.sha512` against the original's bytes.
2. **Read** — PDF and DOCX with this repository's structural readers (`digital_pdf.py`: words,
   lines, columns, running headers and footers, headings, ruled tables, the per-page census;
   `digital_docx.py`), ODT/ODS/XLSX/RTF with the shared reader atrium-ocr-postprocess owns
   (`text_formats.py`, vendored byte-identical; `digital_text.py`), DOC/XLS after a headless
   LibreOffice conversion with a throw-away profile (`digital_legacy.py`).
3. **Check the text layer** — decode sanity per line, `needs_ocr` per page, the prior-OCR refusal.
4. **Build and gate the record** — only digital-convert's fields; the field-survival assertion
   and the schema; the licence union of the components that ran (`para_config.txt`).
5. `/describe` only: **the stages** (`service/stages.py`), then the **report**
   (`api_util/digital_report.py`).

## Shutdown behavior (issue #55)

On SIGTERM `/ready` turns 503 at once (the load balancer stops routing), new conversions are
refused with 503, and in-flight ones finish within `GRACEFUL_SHUTDOWN_S`; `/health` stays 200 so
a liveness probe does not kill a draining pod.

## OpenAPI (the typed contract)

`service/openapi.json` is committed and attached to every release (atrium-project#32); `/info`
reports its digest as `openapi_sha256`. The spec declares the service id `atrium-digital-convert`
and the rename from `atrium-llm-enrich` (`x-atrium-service-previous`), and publishes the reason
codes under `x-atrium-reason-codes`. Regenerate it after an API change:

```bash
python atrium_openapi.py export --app service.api:app --out service/openapi.json
python atrium_openapi.py check  --app service.api:app --spec service/openapi.json
```

## Tests

```bash
pytest tests/test_api_contract.py tests/test_openapi_contract.py   # the contract
pytest tests/test_describe.py tests/test_digital_report.py         # /describe, the stages, the routes
pytest tests/test_digital_formats.py tests/test_digital_to_json.py # the converter
```

`tests/test_describe.py` serves `tools/stage_stub.py` — a model-free stand-in for page-classification
and ocr-postprocess with their request and response shapes — on a local port, and drives the
failure modes (down, timeout, 500, older versions, a rewriting stage) through `requests`.
