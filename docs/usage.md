# Using digital-convert

Setup, the command line, the HTTP service and the Docker images. Deploying and operating the service, with every
error and limit, is in [../service/README.md](../service/README.md).

## Setup

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
pip install -r requirements_flexiconv.txt        # flexiconv adapter (GPL-3.0; api_util/flexiconv_convert.py)
pip install -r requirements_docmd.txt            # deprecated direct converters (--legacy, --ocr)
```

## Command line (`api_util/digital_to_json.py`)

```bash
python3 api_util/digital_to_json.py report.pdf  --document-json-out report.document.json
python3 api_util/digital_to_json.py report.odt  --document-json seed.json --document-json-out report.document.json
python3 api_util/digital_to_json.py budget.xls  --out-dir records/
python3 api_util/digital_to_json.py report.pdf  --engine docling --paradata-dir paradata/
```

| Option                          | Meaning                                                                                            |
|---------------------------------|----------------------------------------------------------------------------------------------------|
| `--document-json-out` (`--out`) | where to write the record (default `<out-dir>/<doc_id>.document.json`)                             |
| `--document-json`               | the AMČR seed, or an earlier record of the same document, to accrete onto                          |
| `--doc-id`                      | override the doc_id derived from the file name (a seed's doc_id always wins)                       |
| `--engine`                      | `light` (default) or `docling` (PDF only, needs `requirements_digital_docling.txt` and its models) |
| `--docx-page-breaks`            | DOCX pages: `auto` (explicit, section and Word's last rendered breaks), `explicit`, `none`         |
| `--paradata-dir`                | also write the run's paradata record                                                               |
| `--strict`                      | raise instead of warning on a field-ownership violation                                            |

**Exit codes:**
* `0`: the record was written.
* `2`: a dependency is missing (a reader, Docling's models, LibreOffice for DOC/XLS), with advice.
* `3`: not an input this converter takes (unsupported type, an OCR-layer PDF, an old binary Office
  type it cannot convert), or a seed whose `source.sha512` does not match.
* `4`: corrupt, encrypted, over the ZIP limits, a failed LibreOffice conversion, or over a limit
  (`MAX_PAGES`, `LIBREOFFICE_TIMEOUT_S`).

## The service: `/reformat` and `/describe`

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

The fields, every response key, the error table (`ocr_text_layer`, `source_digest_mismatch`,
`unsupported_media_type`, `limit_exceeded`, `busy`, …), the limits and the shutdown behaviour are
documented in [service/README.md](../service/README.md). The typed contract is
[service/openapi.json](../service/openapi.json), attached to every release.

## Docker

| Target            | Image                                         | What it is                                                                                |
|-------------------|-----------------------------------------------|-------------------------------------------------------------------------------------------|
| `api`             | `ghcr.io/ufal/atrium-digital-convert-api`     | **the production image**: `/reformat`, `/describe` (light engine + LibreOffice + FastAPI) |
| `digital`         | `ghcr.io/ufal/atrium-digital-convert-digital` | the converter's command line (published, never pinned)                                    |
| `digital-docling` | built locally                                 | the heavy PDF engine, with Docling's models downloaded at build time                      |

The tag is the release **without** its leading `v` (`1.1.0-beta` for `v1.1.0-beta`); the target is
part of the image name.

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
> * Docker Desktop (macOS, Windows) needs neither. (atrium-project#69)
