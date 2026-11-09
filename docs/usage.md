# Using digital-convert

How to set up a checkout, convert a file on the command line or through the HTTP service, and run the Docker
images. Deploying and operating the service, with every error and limit, is in
[service/README.md](../service/README.md).

**On this page:**

- [Set up a checkout](#set-up-a-checkout)
- [Convert a file on the command line](#convert-a-file-on-the-command-line): options and exit codes
- [Call the service](#call-the-service-reformat-and-describe): `/reformat` and `/describe`
- [Run the Docker images](#run-the-docker-images)
- [The command line and the service](#the-command-line-and-the-service): where the two differ, and why
- [Document types for decision models](#document-types-for-decision-models-offline): two offline research tools

## Set up a checkout

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                  # base: pydantic, requests, jsonschema, lxml, …
pip install -r requirements_digital.txt          # the converter: pdfplumber, pypdfium2, python-docx, lxml, jsonschema
pip install -r service/requirements.txt          # + the web server (FastAPI, uvicorn), for the service
```

Optional:

```bash
pip install -r requirements_digital_docling.txt  # the heavy PDF engine (--engine docling): Docling + torch
apt-get install libreoffice-writer-nogui libreoffice-calc-nogui   # DOC and XLS (or set LIBREOFFICE_BIN)
pip install -r requirements_flexiconv.txt        # flexiconv adapter (MIT; api_util/flexiconv_convert.py)
pip install -r requirements_docmd.txt            # deprecated direct converters (--legacy, --ocr)
```

The flexiconv adapter (MIT, `requirements_flexiconv.txt`) is an optional TEITOK adapter of the older Markdown converters
(`api_util/doc_to_visual_md.py`, `docx_to_md.py`, `pdf_to_md.py`); neither entry point loads it.

## Convert a file on the command line

```bash
python3 api_util/digital_to_json.py report.pdf  --document-json-out report.document.json
python3 api_util/digital_to_json.py report.odt  --document-json seed.json --document-json-out report.document.json
python3 api_util/digital_to_json.py budget.xls  --out-dir records/
python3 api_util/digital_to_json.py report.pdf  --engine docling --paradata-dir paradata/
python3 api_util/digital_to_json.py report.pdf  --out-dir out/ --markdown --describe
PAGE_CLASSIFICATION_URL=http://localhost:8001 OCR_POSTPROCESS_URL=http://localhost:8002 \
    python3 api_util/digital_to_json.py report.pdf --out-dir out/ --describe --classify-pages all
```

| Option                                               | Meaning                                                                                                                      |
|------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `--document-json-out` (`--out`)                      | where to write the record (default `<out-dir>/<doc_id>.document.json`)                                                       |
| `--document-json`                                    | the AMČR seed, or an earlier record of the same document, to accrete onto                                                    |
| `--doc-id`                                           | override the doc_id derived from the file name (a seed's doc_id always wins)                                                 |
| `--engine`                                           | `light` (default) or `docling` (PDF only, needs `requirements_digital_docling.txt` and its models)                           |
| `--docx-page-breaks`                                 | DOCX pages: `auto` (explicit, section and Word's last rendered breaks), `explicit`, `none`                                   |
| `--paradata-dir`                                     | also write the run's paradata record                                                                                         |
| `--strict`                                           | raise instead of warning on a field-ownership violation                                                                      |
| `--markdown [PATH]`                                  | also write the record as annotated Markdown, as `/reformat`'s `markdown=true` (default `<doc_id>.md` beside the record)      |
| `--describe [PATH]`                                  | also write the per-page assessment of `/describe`: `reader`, `pages`, `summary`, `stages` (default `<doc_id>.describe.json`) |
| `--stages`                                           | with `--describe`: `page-classification`, `ocr-postprocess`, both comma-separated, or `none`; unset = every configured one   |
| `--classify-pages`                                   | with `--describe`: `needs_ocr` (default) or `all`, the pages page-classification is asked about                              |
| `--no-text`                                          | with `--describe`: leave `pages[].text` out (`include_text=false`)                                                           |
| `--page-classification-url`, `--ocr-postprocess-url` | with `--describe`: the stages' base URLs (else `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`)                             |

`--markdown` and `--describe` run the service's own code (`service/describe.py`), so a file gives the same
Markdown and the same assessment on both sides. If a stage writes into the record during `--describe`, the same
guard as in the service decides whether the record adopts it, and the record file then holds the result. A stage
that is not configured, is down or answers badly is reported in `stages[]`. The run still exits 0, as the service
still answers 200.

### Exit codes

* `0`: the record was written (and the views asked for).
* `2` (from argparse): an unknown `--stages` value, as the service's 422.
* `2`: a dependency is missing (a reader, Docling's models, LibreOffice for DOC/XLS), with advice.
* `3`: an input this converter does not take. That means an unsupported type, an OCR-layer PDF, an old binary
  Office type it cannot convert, or a seed whose `source.sha512` does not match. It also means a
  `--document-json` record that cannot be opened (`invalid_record`: not UTF-8 JSON, not an object, a newer
  schema), or whose `pages` or `lines` is not a list of objects (`invalid_record` too, found when the conversion
  adds its rows).
* `4`: an empty, corrupt or encrypted file, a file over the ZIP limits, a failed LibreOffice conversion, or a
  file over a limit (`MAX_PAGES`, `LIBREOFFICE_TIMEOUT_S`).

A `--document-json` record that opens but does not validate is accreted onto, as in the service. The run warns,
writes the record and exits 0; the service answers 200 with `document_json_schema_error`.

## Call the service: `/reformat` and `/describe`

```bash
pip install -r service/requirements.txt
python -m service.api                                    # 0.0.0.0:8000
curl -F file=@report.pdf http://localhost:8000/reformat
curl -F file=@report.pdf -F document_json=@seed.json -F markdown=true http://localhost:8000/reformat
curl -F file=@report.pdf http://localhost:8000/describe
```

| Endpoint                         | Does                                                                                                 | Calls other services                                              |
|----------------------------------|------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| `POST /reformat`                 | file (+ seed) → `document_json` (the record), Markdown on request, `paradata`                        | **never**                                                         |
| `POST /describe`                 | the same conversion → `document_json`, `pages[]` (the assessment), `summary`, `stages[]`, `paradata` | page-classification and ocr-postprocess, **only when configured** |
| `GET /info`, `/health`, `/ready` | the ATRIUM service contract: identity, limits, readers, LibreOffice availability, stages configured  | —                                                                 |

The command line gives every result of the two endpoints: the record, `--markdown` and `--describe` (above).
Where the two differ, and why: [The command line and the service](#the-command-line-and-the-service).

The runbook, [service/README.md](../service/README.md), documents the fields, every response key, the error
table (`ocr_text_layer`, `source_digest_mismatch`, `unsupported_media_type`, `limit_exceeded`, `busy`, …), the
limits and the shutdown behaviour. The typed contract is [service/openapi.json](../service/openapi.json),
attached to every release.

## Run the Docker images

| Target            | Image                                         | What it is                                                                                |
|-------------------|-----------------------------------------------|-------------------------------------------------------------------------------------------|
| `api`             | `ghcr.io/ufal/atrium-digital-convert-api`     | **the production image**: `/reformat`, `/describe` (light engine + LibreOffice + FastAPI) |
| `digital`         | `ghcr.io/ufal/atrium-digital-convert-digital` | the converter's command line (published, never pinned)                                    |
| `digital-docling` | built locally                                 | the heavy PDF engine, with Docling's models downloaded at build time                      |

The tag is the release **without** its leading `v` (`1.4.7-beta` for `v1.4.7-beta`); the target is
part of the image name.

The batch image passes its arguments to `api_util/digital_to_json.py` (`docker run IMAGE /data/report.pdf
--document-json-out /data/report.document.json`). Since `v1.4.5-beta` its entry point is the hub-canonical
`container_entrypoint.py`, which names `api_util/digital_to_json.py` by its absolute path: a first argument that
names a shell or Python runs that program instead (`docker run IMAGE /bin/sh job.sh`), which is how Galaxy starts a
tool's container. Only those interpreters run: an input file that happens to be executable is still converted, as
`digital-docling` does too.

```bash
docker compose --profile api up                                     # the service on :8000
docker compose --profile stub up                                    # the service + the stage stand-ins
docker compose --profile digital run --rm digital-convert-digital \
    /data/report.pdf --document-json-out /data/report.document.json
docker build --target digital-docling -t atrium-digital-convert-digital-docling .
```

* **Image contents.** [`.github/production-image.json`](../.github/production-image.json) declares
  the first-party files of the `api` image. The hub's `tools/ci/image_closure.py` checks it on every
  push; research tools, tests and the stage stub stay out (`.dockerignore`).
* **Image size.** LibreOffice (writer and calc, `-nogui`) adds roughly 300 MB to `digital` and `api`.

> [!NOTE]
> **Docker on Linux: run as yourself.** `./data` belongs to you, while the images run as uid 10001
> by default.
> * With compose: `docker-compose.yaml` runs every service as `user: "${ATRIUM_UID:-10001}:0"`, so
>   put your uid in `.env` once (`echo "ATRIUM_UID=$(id -u)" >> .env`).
> * With `docker run -v "$PWD:/data"`: pass `--user "$(id -u):0"`.
> * Docker Desktop (macOS, Windows) needs neither.
## The command line and the service

Both run one converter (`api_util/digital_to_json.py`: `prepare`, the output gate `gate`, the paradata's
parameters `run_config`) and the views of `service/describe.py`. A scratch harness ran 240 combinations through
both on 2026-10-28. The record, the Markdown, the assessment, the refusals and the paradata's parameters and
counts were the same in every case. The combinations covered:

* **Files:** PDF, DOCX, ODT, RTF, DOC, XLSX, ODS and XLS made with LibreOffice, a name with diacritics and spaces,
  an empty file and a broken package.
* **DOCX page breaks:** `auto`, `explicit` and `none`.
* **Seeds:** no seed, a seed, a mismatched digest, an unreadable record, a newer schema and an invalid inherited
  record.
* **Views:** the record alone, the record with Markdown, and `/describe` with no stage, with page-classification
  only and with both; `classify_pages` `needs_ocr` and `all`; with and without the text. The stages were served by
  `tools/stage_stub.py`.

`tests/test_cli_service_parity.py` holds the two sides to the same record, Markdown, assessment and refusals.

| Input or setting                                           | Command line                                                                                     | Service                                                           | Same?                                                                  |
|------------------------------------------------------------|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|------------------------------------------------------------------------|
| The formats, recognised by content                         | PDF, DOCX, ODT, ODS, XLSX, RTF; DOC and XLS through LibreOffice                                  | the same                                                          | the same                                                               |
| An empty file                                              | exit 4, `corrupt`                                                                                | 422, `cause: corrupt`                                             | the same                                                               |
| A seed or an earlier record                                | `--document-json`; its `source.sha512` checked                                                   | the `document_json` part; checked the same way                    | the same                                                               |
| A record that cannot be opened                             | exit 3, `invalid_record`                                                                         | 422 `invalid_record`                                              | the same                                                               |
| A record whose `pages` or `lines` is not a list of objects | exit 3, `invalid_record`                                                                         | 422 `invalid_record`                                              | the same                                                               |
| An inherited record that does not validate                 | a warning; the record is written, exit 0                                                         | 200 with `document_json_schema_error`                             | the same                                                               |
| DOCX pages                                                 | `--docx-page-breaks`                                                                             | `docx_page_breaks`                                                | the same                                                               |
| The doc_id                                                 | from the file name; `--doc-id` overrides it; a seed's wins                                       | from the upload's name; a seed's wins                             | the same derivation; `--doc-id` is for a pipeline driver               |
| Markdown                                                   | `--markdown`, the `full` profile; others with `api_util/json_to_md.py --detail`                  | `markdown=true`, the `full` profile                               | the same; with `--describe` as well, rendered after the stages' writes |
| The assessment                                             | `--describe`, `--stages`, `--classify-pages`, `--no-text`                                        | `/describe`, `stages`, `classify_pages`, `include_text`           | the same                                                               |
| Which reader read it (`reader`)                            | in the `--describe` file                                                                         | in both answers                                                   | the same object                                                        |
| The stages' URLs                                           | `--page-classification-url`, `--ocr-postprocess-url`, or the variables                           | `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`                  | differs: set per deployment                                            |
| The engine                                                 | `light`, or `--engine docling` (a local build)                                                   | `light`                                                           | differs: Docling is not in the image                                   |
| Field ownership                                            | a warning; `--strict` makes it an error                                                          | a warning                                                         | differs: `--strict` is for testing a new block                         |
| The run's stamps                                           | its own, or `--run-id`, `--paradata-ref` from a pipeline driver                                  | its own                                                           | differs: a driver may stamp several stages with one run                |
| Limits                                                     | `MAX_PAGES`, `LIBREOFFICE_TIMEOUT_S`; `MAX_UPLOAD_MB` in the shared reader (ODT, ODS, XLSX, RTF) | all, and the upload itself, `MAX_CONCURRENT_JOBS`                 | differs: the service bounds what it accepts                            |
| Paradata                                                   | a file, with `--paradata-dir`                                                                    | the answer's `paradata`, a `CreateAction`; config adds `endpoint` | the same parameters and counts                                         |

## Document types for decision models (offline)

Two research tools of [#8](https://github.com/ufal/atrium-digital-convert/issues/8) ask a decision model which
AMČR document type (`dokument_typ`) a PDF page or a whole PDF is. They are offline experiments: neither is in an
image or in the service, so they have no counterpart in `/reformat` or `/describe`, and nothing they find reaches
the record. The list of types is a draft until AMČR agrees to it
([atrium-project#83](https://github.com/ufal/atrium-project/issues/83)).

**Images only.** A document type is the document's form: a report, a site photograph, a trench plan, a map. Under
the rule of atrium-project#83, form is read from images, by this repository and atrium-page-classification, and
never from text; the tools that read text (keyword-extract, ocr-postprocess) decide content and text quality. So a
request carries rendered pages and nothing else: the text layer is never sent.

### The list of types

`tools/build_document_types.py` generates `decisions/document_type.v3.json` from atrium-keyword-extract's
vocabulary build (`data_samples/vocab/amcr_flat.json`, AMČR's heslář, CC0), so the list is never written by hand.
Of the 41 `dokument_typ` terms, the 9 data-only ones (geodetic, geophysical and GNSS data, 3D models and the like)
are left out. Each of the other 32 keeps its AMČR URI and labels and gets an English id and one of 6 groups:
`text`, `photograph`, `aerial_photograph`, `plan_or_map`, `drawing` and `unspecified`. `unspecified`
(`neurčený dokument`) is the abstain option. The file records the vocabulary's commit and SHA-256, and holds one
question set per level, a view, each with its digest (`schema_digest`):

| View           | Level    | Input                               | Questions                                                                                              |
|----------------|----------|-------------------------------------|--------------------------------------------------------------------------------------------------------|
| `pdf_page`     | page     | one rendered page                   | `group` (6 options) and `image_type`: the 25 picture types and `unspecified`, also the answer for text |
| `pdf_document` | document | the first rendered pages, at most 4 | `group` and `document_type`: all 32 types, the 6 written genres among them                             |

A page is not asked its written genre (field report, annex, expert or client report, brief report, other text):
an inner page of a report rarely shows it, while a title page does. No other repository uses the list today;
atrium-page-classification's track 2 (its #58) is to take the picture types.

```bash
python3 tools/build_document_types.py --vocab ../atrium-keyword-extract/data_samples/vocab/amcr_flat.json
python3 tools/build_document_types.py --vocab <file> --check     # exit 1 if the committed list differs
```

`--out` writes elsewhere, and `--source-commit` names the vocabulary's commit when `--vocab` is not in a git
checkout. The script stops when the vocabulary and its type table disagree: when other than 9 terms are data-only,
or a term has no group. A type AMČR adds is therefore placed on purpose.

### Ask a decision model: `tools/decide_documents.py`

The tool renders each page of each PDF with pypdfium2 and sends it to a decision model served at
`POST /v1/systemone`, through `decision_client.py` (vendored from atrium-page-classification). It asks at two
levels, one request per item:

* **page:** one rendered page, the `pdf_page` questions;
* **document:** the first `--doc-pages` pages of the PDF, at most 4, the `pdf_document` questions.

The page answers are never merged into a document answer: which of the two is right is what the evaluation is to
find out. A request carries the images alone. It needs `requirements_digital.txt` (pypdfium2, and Pillow through
pdfplumber) and a server. Run it from the repository root:

```bash
export DECISION_MODEL_URL=...      # the server; never recorded
python3 tools/decide_documents.py --input <folder of PDFs> --out-dir <dir> --model clef-oQ8e --served-on cluster-job
python3 tools/decide_documents.py --input <pdf> --out-dir <dir> --level page
```

| Option                          | Meaning                                                                                                                      |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `--input`                       | a PDF, or a folder: every `.pdf` under it (required)                                                                         |
| `--out-dir`                     | where the tables, the summary and the paradata go (required)                                                                 |
| `--level`                       | `page`, `document` or `both` (default)                                                                                       |
| `--max-side`                    | the rendered page's long side, in pixels (default 1024)                                                                      |
| `--doc-pages`                   | the pages of a document request, from the first: 1 to 4 (default 4, a request's image limit)                                 |
| `--gold`                        | a CSV of `file,document_type` to score the documents; a type as its id, its HES number or its Czech label                    |
| `--page-gold`                   | a CSV of `file,page,document_type` to score the pages, the same way; a written genre scores the page's group only            |
| `--accept-at`, `--review-below` | the lane thresholds, both or neither (below)                                                                                 |
| `--resume`                      | continue the tables in `--out-dir`, skipping the pages and documents they hold; without it, an existing table stops the run  |
| `--url`, `--model`              | the server and the model (default `DECISION_MODEL_URL`, `DECISION_MODEL_NAME`)                                               |
| `--model-revision`              | the served build or Hugging Face revision, for the paradata                                                                  |
| `--served-on`                   | where the pages go: `cluster-job`, `ufal-endpoint`, `local` or `other` (default `local` for a loopback URL, else `other`)    |
| `--timeout`, `--retries`        | per request: seconds (default `DECISION_TIMEOUT_S`, else 120) and retries (default 2)                                        |
| `--image-style`                 | how the images travel: `data_uri` (default), `base64`, `url` or `image_url`; which one a server reads is the server's choice |
| `--no-proxy`                    | ignore the proxy variables for this server                                                                                   |

**The lanes.** With both thresholds, each row gets a `lane` from its type's confidence: `accept` at or above
`--accept-at`, `review` below `--review-below`, `escalate` in between. Each threshold is between 0 and 1, and
`--review-below` is not above `--accept-at`. The thresholds belong to one model revision and one question wording,
so set them on dev data.

A bad option ends the run before the first request, with exit code 2: a threshold out of range or without the
other, a `--doc-pages` outside 1 to 4, no PDF at `--input`, or no server or model.

It writes into `--out-dir`:

* `DECIDE_PAGES.csv`, a row per page, and `DECIDE_DOCS.csv`, a row per PDF (`pages_sent`: how many pages went).
  A row holds the chosen `group` and type (`image_type` on a page, `document_type` for a document) with every
  option's probability (`P_group_*`, `P_type_*`), `type_group` (the chosen type's group), `consistent` (whether
  the type falls in the chosen group; on a page, `unspecified` also agrees with `text`), `confidence`, `top3`,
  `lane`, the gold, and the request's `status`, `error`, `latency_ms`, `input_tokens`, `attempts` and
  `schema_digest`. Rows are written as the answers come. A PDF that pdfium cannot open gets a `rejected` row in
  `DECIDE_DOCS.csv`, and the run goes on.
* `decide_documents.summary.json`: per level, the statuses, the latency, the input tokens, the spread of groups
  and types, the `unspecified` share and the inconsistent rows; with gold, the group accuracy and the type
  accuracy (on pages, over the rows whose gold is a picture type, `type_rows`).
* `paradata/`, the run's paradata record: program `digital-convert`, components `decision_model` and `pypdfium2`,
  and the model, its revision, the list, the digest of each view asked, the level, `--max-side` and
  `--doc-pages`. The server is recorded by its kind (`--served-on`), never by its URL.
