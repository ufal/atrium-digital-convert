# digital-convert API service

The HTTP service of the born-digital step (`api-digital`). It reads born-digital documents into docproc
(`atr/docproc-json`), the layout format the ATR, AIS CR's text-recognition service, writes for scanned pages;
ocr-postprocess builds the ATRIUM record from it. The version it reports is read from `para_config.txt`.

* **`POST /reformat`** — the production endpoint the AMČR route calls. File (and optionally the
  AMČR seed) in, docproc out; Markdown on request; run `paradata`. **Calls no other service** —
  in the AMČR deployment the pipeline's orchestrator (Temporal) chains the steps.
* **`POST /describe`** — parked during the AMČR pilot: a per-page assessment
  ([parked features](../docs/reference/parked.md)).

Inputs, by content (the extension only names the file), in two workflows: **visual**, PDF and the office
documents DOCX, DOC, ODT and RTF (native text on the pages of a headless LibreOffice rendering); **textual**, ODS,
XLSX, XLS (displayed by LibreOffice in cs-CZ, every cell as the sheet shows it), TXT, CSV, TSV and JSON, extracted
as they are and never sent to the ATR. Images are not inputs: they go to the ATR. No model, no network, no GPU.

## Start

**At a glance:**

| Image                                               | CPU / GPU        | Memory                                                                                                      | Volumes                                   | Calls out to                                                                                                        | Ready after                                                                         |
|-----------------------------------------------------|------------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| `ghcr.io/ufal/atrium-digital-convert-api:<version>` | CPU only; no GPU | budget about 1 GB plus `MAX_UPLOAD_MB` per `MAX_CONCURRENT_JOBS` slot; the largest peak measured was 783 MB | none (compose mounts `./data` as `/data`) | `/reformat`: nothing. The parked `/describe`: page-classification and ocr-postprocess, only when their URLs are set | about 3 s from a checkout (no model to load); the health check allows 30 s to start |

The published image (2.0.0-beta or later, the release without its leading `v`) is what a deployment runs, and it
needs no checkout. Start it in the background and wait until it is ready:

```bash
docker run -d --name atrium-digital-convert -p 8000:8000 ghcr.io/ufal/atrium-digital-convert-api:<version>
until curl -fsS http://localhost:8000/ready >/dev/null; do sleep 2; done
```

From a checkout, with compose (`docker compose --profile api up -d`) or in a virtual environment, for development:

```bash
pip install -r service/requirements.txt          # the converter stack + the web server
python -m service.api                            # binds $HOST:$PORT (0.0.0.0:8000); RELOAD=true for auto-reload
```

Then, from a second terminal:

```bash
curl -F file=@report.pdf http://localhost:8000/reformat
curl -F file=@report.pdf -F markdown=true http://localhost:8000/reformat
curl -F file=@report.pdf -F document_json=@seed.json http://localhost:8000/reformat
```

The README's [quick start](../README.md#quick-start) generates a sample PDF and shows the expected docproc. Stop the
container with `docker rm -f atrium-digital-convert`.

## Models and caches

Nothing is downloaded at start or per request: the converter has no model and needs no network.

| What                                                               | From where                                       | Revision                           | Into          | Without egress                                                                |
|--------------------------------------------------------------------|--------------------------------------------------|------------------------------------|---------------|-------------------------------------------------------------------------------|
| LibreOffice and its fonts (office documents, XLSX, XLS)            | in the image                                     | the image's package                | —             | nothing needed; a deployment without it answers 501 for DOCX/DOC/ODT/RTF/XLS  |
| page-classification, ocr-postprocess (the parked `/describe` only) | `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL` | their own releases                 | —             | the stage reads `not_configured` or `unavailable`; the request still succeeds |
| Docling (optional engine)                                          | a local build only, not the API image            | `requirements_digital_docling.txt` | its own cache | not part of the API image                                                     |

## Endpoints

| Method | Path        | Purpose                                                                                                    |
|--------|-------------|------------------------------------------------------------------------------------------------------------|
| GET    | `/info`     | identity, version, endpoints, limits and settings, capabilities                                            |
| GET    | `/health`   | liveness; `?deep=true` checks the readers and the record schema                                            |
| GET    | `/ready`    | readiness: 503 before start-up checks pass and while draining                                              |
| POST   | `/reformat` | born-digital file → docproc (+ Markdown on request)                                                        |
| POST   | `/describe` | parked: born-digital file → docproc + per-page assessment ([parked features](../docs/reference/parked.md)) |

### `POST /reformat` (multipart form)

| Field              | Required | Meaning                                                                                                      |
|--------------------|----------|--------------------------------------------------------------------------------------------------------------|
| `file`             | yes      | the document                                                                                                 |
| `document_json`    | no       | the AMČR seed (`doc_id`, `source.sha512`, `filename`, `media_type`), read only; an empty part counts as none |
| `markdown`         | no       | `true` to also get the document rendered as annotated Markdown (`json_to_md`; a development view)            |
| `rendered`         | no       | `true` to get an office document's rendered PDF even when no page needs the ATR                              |

The response:

| Field            | Meaning                                                                                                                                                                                                                                                                 |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `service`        | `atrium-digital-convert`                                                                                                                                                                                                                                                |
| `doc_id`         | the seed's `doc_id`, else derived from the file name (`atrium_document.canonical_doc_id`)                                                                                                                                                                               |
| `docproc`        | the docproc: pages, blocks with their `lines[]`, `metadata.routing` ([schemas/docproc](../schemas/docproc/README.md))                                                                                                                                                   |
| `reader`         | how the file was read: `kind`, `workflow` (`visual`, `textual`), `media_type`, `origin`, `engine`, `pages`, `conversion` (DOC/XLS/XLSX; a spreadsheet's with its `locale`), `rendering` (office documents: `tool`, `version`, `from`, `to`, `pages`, `sha512`), `notes` |
| `rendered_pdf`   | an office document's rendered PDF (`media_type`, `sha512`, base64 `data`): when a page is routed `atr`, or with `rendered=true`. The docproc's pages and boxes are its pages; page-classification and the ATR read it                                                   |
| `markdown`       | only with `markdown=true`                                                                                                                                                                                                                                               |
| `limits_applied` | the splitting the result rests on: lines of a textual input wrapped at `MAX_LINE_CHARS` (`split`). Nothing is sampled; a limit that would cut content fails the call                                                                                                    |
| `paradata`       | the call's Process Run Crate `CreateAction` (paradata 3.0, below). No paradata file is written                                                                                                                                                                          |

**The seed.** Read, never written: its `doc_id` names the result, and its `sha512` is compared with the
uploaded bytes **before anything is read** (for a DOC/XLS, before the LibreOffice conversion). A mismatch is
refused (422 `source_digest_mismatch`), so nothing is produced for the wrong file.

**`paradata`** (paradata schema 3.0): `object` names the upload as `original` and the seed as
`atrium/document-json`, `result` the docproc as `atr/docproc-json`, each by SHA-512. When a page needs the ATR
the docproc is an intermediate, `stored: false`: the ATR's docproc is the stored one. An office document's
rendered PDF is in `result` too, always `stored: false`, with the pinned export options and
`RENDER_TEXT_COVERAGE` among the `parameters`. `paradataRecord` lists the components used, each with its own terms
and LibreOffice with its version; no licence of the output is computed.

**The text layer.** Every page in `pages[]` carries the converter's verdict in `textLayer`:

* `digital`: the layer decodes;
* `garbled`: the layer does not decode (CP1250 read as CP1252, U+FFFD and control characters);
* `ocr`: a prior OCR run;
* `none`: no text layer.

The `garbled`, `ocr` and `none` pages also carry `needsAtr: true` with a `needsAtrReason`, and are routed `atr` in
`metadata.routing.pages[]`; an empty page is routed `empty` and is not in `pages[]`. No document is refused for
its pages.

### `POST /describe` (parked)

Parked during the AMČR pilot: not part of the pipeline. The service still answers it: the conversion of `/reformat`,
then a per-page assessment that asks page-classification and ocr-postprocess when their URLs are configured. Its
fields, routes and stage statuses are in [parked features](../docs/reference/parked.md).

## Errors

Every error has one JSON body, `{status, reason, detail}`, the same in every ATRIUM service (`ErrorBody` in
[`openapi.json`](openapi.json)).
`reason` is a registered code or `null`, and refusals of a born-digital input name the converter's finer
cause in `cause`. Each row gives the status, the cause and what to do.

| Status | `reason`                 | Meaning                                                                                                                                                                                                                                                | What to do                                                                         |
|--------|--------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| 413    | `limit_exceeded`         | a part over `MAX_UPLOAD_MB`                                                                                                                                                                                                                            | raise `MAX_UPLOAD_MB` for large originals (see Large documents), or split the file |
| 415    | `unsupported_media_type` | not a type the converter reads, an image among them (`cause`: `unsupported`, `legacy_office_unsupported`; `accepted` lists the types)                                                                                                                  | send a type in `accepted`; an image or a scanned document goes to the ATR          |
| 422    | `source_digest_mismatch` | the seed's `source.sha512` is not the uploaded file's                                                                                                                                                                                                  | send the file the seed was made for, or a new seed                                 |
| 422    | `invalid_record`         | `document_json` cannot be opened (not UTF-8 JSON, not an object, a newer schema major)                                                                                                                                                                 | send the seed AMČR made; update this service if the record is newer                |
| 422    | `limit_exceeded`         | over `MAX_PAGES` or `LIBREOFFICE_TIMEOUT_S`; a repeated ODF cell or row past the repeat cap (`limit.effect` `trimmed`: no content is cut)                                                                                                              | raise the setting `limit.env` names, or split the document                         |
| 422    | `null`                   | an empty or broken file (`cause`: `corrupt` — a CSV/JSON that does not parse, a reading that would skip content —, `encrypted`, `zip_limits_exceeded`, `conversion_failed` — a failed conversion or rendering); an unknown `stages` name (`/describe`) | repair or decrypt the file `cause` names; fix the `stages` value                   |
| 429    | `busy`                   | `MAX_CONCURRENT_JOBS` conversions already running; retry after `Retry-After`                                                                                                                                                                           | retry after `Retry-After`; raise `MAX_CONCURRENT_JOBS` if memory allows            |
| 501    | `null`                   | an office document (DOCX, DOC, ODT, RTF), an XLSX or an XLS with no LibreOffice in this deployment (`cause`: `dependency_missing`)                                                                                                                     | use the image (it has LibreOffice)                                                 |
| 503    | `null`                   | the service is shutting down; retry against a live replica                                                                                                                                                                                             | retry on another replica                                                           |

`source_digest_mismatch` is in the shared registry (`service/atrium_service.py`, vendored from the hub),
so every ATRIUM service's spec publishes it.

## Limits

Every limit and setting is an environment variable, declared in
`tool_limits.py`. `GET /info` reports its current value (`limits`), and `limits_meta` says which variable
sets it and whether the value came from the environment or the default. A
malformed value stops the service at startup, naming the variable. `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)                   | Variable                            | Default | Unit    | Over the limit                                                                                                                            |
|---------------------------------|-------------------------------------|---------|---------|-------------------------------------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`                 | `MAX_UPLOAD_MB`                     | 250     | MB      | 413 `limit_exceeded` — per part (the file, `document_json`)                                                                               |
| `max_pages`                     | `MAX_PAGES`                         | 2000    | pages   | 422 `limit_exceeded` — split the document                                                                                                 |
| `max_concurrent_jobs`           | `MAX_CONCURRENT_JOBS`               | 2       | jobs    | 429 `busy` with `Retry-After`                                                                                                             |
| `libreoffice_timeout_s`         | `LIBREOFFICE_TIMEOUT_S`             | 120     | s       | 422 `limit_exceeded` — LibreOffice did not convert a DOC/XLS/XLSX, or render an office document, in time                                  |
| `stage_timeout_s`               | `STAGE_TIMEOUT_S`                   | 360     | s       | `/describe` (parked): the stage is reported `unavailable`; the request still succeeds                                                     |
| `route_trash_share`             | `ROUTE_TRASH_SHARE`                 | 0.5     | share   | policy (`/describe`, parked): a decoding page goes to `ocr` instead of `nlp` when the quality model calls more of its prose lines `Trash` |
| `furniture_only_image_coverage` | `FURNITURE_ONLY_IMAGE_COVERAGE`     | 0.85    | share   | policy: a PDF page whose only text is its running header or footer is `none` (`needsAtr`) when its images cover this share of it          |
| `bold_heading_max_share`        | `BOLD_HEADING_MAX_SHARE`            | 0.25    | share   | policy: a bold body-size line that reads as a title is a heading while bold body-size text is at most this share; 0 = off                 |
| `render_text_coverage`          | `RENDER_TEXT_COVERAGE`              | 0.8     | share   | policy: a rendered office page whose body words the document's text covers less (3+ words not covered) needs the ATR                      |
| `max_line_chars`                | `MAX_LINE_CHARS`                    | 1000    | chars   | a longer line of a text-only input or a spreadsheet row is wrapped; each wrap is a `split` in the paradata `limits`; 0 = never            |
| `odf_repeat_cap`                | `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP` | 100     | repeats | the shared reader expands a repeated ODF cell or row at most this often                                                                   |

**Large documents** (measured on 15 AMČR excavation reports, up to 156 MB and 529 pages;
[what they showed](../docs/evaluation/amcr-samples.md)):

* **Upload.** The upload is held in memory, so the 250 MB default (AMČR's operational setting)
  accepts every sample. AMČR's originals reach about 1.5 GB. A deployment can raise `MAX_UPLOAD_MB`
  for them, but above 250 MB memory, concurrency and timeouts are not yet tested (measured up to
  156 MB). The orchestration can instead split the file by pages. The 413 names the setting.
* **Memory follows the text, not the file size.** The server's peak reached 783 MB, for a 282-page
  report, and 560 MB for the 156 MB one. Budget about 1 GB plus `MAX_UPLOAD_MB` per
  `MAX_CONCURRENT_JOBS` slot.
* **An office document's rendered PDF travels in the answer.** `rendered_pdf` is base64, about 4/3 of the
  PDF's size, so a DOCX full of photographs gives a large response when a page needs the ATR (or with
  `rendered=true`); the rendering keeps the images' resolution for the ATR.
* **Time.** `/reformat` took 3–58 s per document. What `/describe` takes with its stages is in
  [parked features](../docs/reference/parked.md#large-documents-with-describe).

## Partial results

A limit that would cut content fails the call: nothing partial is returned. Read these before using the result:

- `needsAtr` pages have no usable text layer and are routed `atr`; the ATR's docproc is then the stored one, and
  this docproc is an intermediate (`stored: false` in the paradata).
- `limits_applied` lists the splitting the result rests on: lines of a textual input wrapped at `MAX_LINE_CHARS`
  (`split`), each in full. It is `[]` when nothing was split.

## Known limits

- Headings come from font size and bold body-size titles, not from a layout model, so a complex layout
  gets fewer headings than a layout model finds; `BOLD_HEADING_MAX_SHARE=0` switches the bold-title rule
  off.
- PDFs and office documents give line boxes; spreadsheets and text files give pages and lines without positions.
- A PDF page whose fonts do not map to Unicode for some letters keeps its text layer, the lost letters shown as
  U+FFFD: that is left to ocr-postprocess's scoring, not a reason for the ATR. A layer that does not decode at all
  is `garbled` and goes to the ATR (`needsAtr`).
- Unruled tables are read by column, and letter-spaced captions and map labels split into pieces.
- The upload is held in memory: the default 250 MB accepts most reports, and larger originals need
  `MAX_UPLOAD_MB` raised per deployment; above 250 MB that is not yet tested (see Large documents).

## Configuration (environment)

| Variable                  | Default   | Meaning                                                                                                  |
|---------------------------|-----------|----------------------------------------------------------------------------------------------------------|
| `PORT`                    | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes                                  |
| `HOST`                    | `0.0.0.0` | bind address; see the note below                                                                         |
| `GRACEFUL_SHUTDOWN_S`     | `20`      | seconds uvicorn waits for in-flight requests                                                             |
| `RELOAD`                  | `false`   | filesystem auto-reload — development only                                                                |
| `LOG_LEVEL`               | `INFO`    | root logger level for the `python -m service.api` start path                                             |
| `ALLOWED_ORIGINS`         | `*`       | CSV of CORS origins                                                                                      |
| `PAGE_CLASSIFICATION_URL` | —         | base URL of atrium-page-classification, for the parked `/describe`; unset → `not_configured`             |
| `OCR_POSTPROCESS_URL`     | —         | base URL of atrium-ocr-postprocess, for the parked `/describe`; unset → `not_configured`                 |
| `LIBREOFFICE_BIN`         | —         | the LibreOffice binary for DOC/XLS/XLSX and the renderings; unset → `soffice` or `libreoffice` on `PATH` |

Every limit and setting — `MAX_UPLOAD_MB`, `MAX_PAGES`, `ROUTE_TRASH_SHARE` and the rest — is
listed under [Limits](#limits); `.env.example` is the complete ledger.

> **Note:** `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`.

## How it works

`/reformat` runs the same converter as the command line
(`api_util/digital_to_json.py`), in a worker thread, inside one of `MAX_CONCURRENT_JOBS` slots:

1. **Input check** — an empty file (`corrupt`), a seed that cannot be opened (`invalid_record`), then the
   seed's `source.sha512` against the original's bytes.
2. **Read** — PDF and DOCX with this repository's structural readers (`digital_pdf.py`: words,
   lines, columns, running headers and footers, headings, ruled tables, the per-page census;
   `digital_docx.py`), ODT/ODS/RTF with the shared reader atrium-ocr-postprocess owns
   (`text_formats.py`, vendored byte-identical; `digital_text.py`), DOC after a headless
   LibreOffice conversion with a throw-away profile (`digital_legacy.py`), and an XLSX or XLS as
   the ODS LibreOffice makes of it, displayed in cs-CZ (every cell as the sheet shows it).
3. **Check the text layer** — decode sanity per line, `needsAtr` per page; no document is refused for its pages.
4. **Build and gate the docproc** — `api_util/docproc_writer.py`, validated against its schema
   (`schemas/docproc/`); a docproc that fails is never returned. The paradata name the original and the docproc by SHA-512 and list the
   components that ran, each with its own terms (`para_config.txt`).
5. The parked `/describe` only: **the stages** (`service/stages.py`), then the **report**
   (`api_util/digital_report.py`), both run from `service/describe.py`.

The command line runs the same steps, the same output gate and the same Markdown view (`--markdown` is
`markdown=true`). It refuses the same inputs (an empty file exit 4, an unreadable seed exit 3) and records the same
paradata parameters. What differs, and why: [docs/usage.md](../docs/usage.md#the-command-line-and-the-service).
`tests/test_cli_service_parity.py` holds the two sides to the same docproc (byte for byte), Markdown and refusals.

## Shutdown behaviour

On SIGTERM `/ready` turns 503 at once, so the load balancer stops routing. New conversions are
refused with 503, and in-flight ones finish within `GRACEFUL_SHUTDOWN_S`. `/health` stays 200, so
a liveness probe does not kill a draining pod.

## OpenAPI (the typed contract)

`service/openapi.json` is committed and attached to every release; `/info` reports its digest as
`openapi_sha256`. The spec declares the service id `atrium-digital-convert` and its former id
`atrium-llm-enrich` (`x-atrium-service-previous`), and publishes the reason codes under
`x-atrium-reason-codes`. Regenerate it after an API change:

```bash
python atrium_openapi.py export --app service.api:app --out service/openapi.json
python atrium_openapi.py check  --app service.api:app --spec service/openapi.json
```

## Tests

```bash
pytest tests/test_api_contract.py tests/test_openapi_contract.py   # the contract
pytest tests/test_describe.py tests/test_digital_report.py         # the parked /describe, its stages and routes
pytest tests/test_digital_formats.py tests/test_digital_to_json.py # the converter
pytest tests/test_cli_service_parity.py                            # the CLI and the service agree
```

`tests/test_describe.py` serves `tools/stage_stub.py` — a model-free stand-in for page-classification
and ocr-postprocess with their request and response shapes — on a local port, and drives the
failure modes (down, timeout, 500, older versions, a rewriting stage) through `requests`.
