# Using digital-convert

How to set up a checkout, convert a file on the command line or through the HTTP service, and run the Docker
images. Deploying and operating the service, with every error and limit, is in
[service/README.md](../service/README.md).

**On this page:**

- [Set up a checkout](#set-up-a-checkout)
- [Convert a file on the command line](#convert-a-file-on-the-command-line): options and exit codes
- [Call the service](#call-the-service-reformat): `/reformat`
- [Run the Docker images](#run-the-docker-images)
- [The command line and the service](#the-command-line-and-the-service): where the two differ, and why

The per-page assessment (`/describe`, `--describe`) and the document-type research tools are parked during the AMČR
pilot; they are described in [parked features](reference/parked.md).

## Set up a checkout

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                  # base: pydantic, requests, jsonschema, lxml, …
pip install -r requirements_digital.txt          # the converter: pdfplumber, pypdfium2, python-docx, lxml, jsonschema
pip install -r service/requirements.txt          # + the web server (FastAPI, uvicorn), for the service
```

Optional:

```bash
pip install -r requirements_digital_docling.txt  # the heavy PDF engine (--engine docling): Docling + torch
apt-get install libreoffice-writer-nogui libreoffice-calc-nogui   # every office document and XLSX/XLS (or set LIBREOFFICE_BIN)
pip install -r requirements_flexiconv.txt        # flexiconv adapter (MIT; api_util/flexiconv_convert.py)
pip install -r requirements_docmd.txt            # deprecated direct Markdown converters (--legacy, --ocr), outside the pipeline
```

Without LibreOffice a DOCX, DOC, ODT, RTF, XLSX or XLS fails with `dependency_missing`; PDFs, ODS files and the text
formats do not need it.

The flexiconv adapter (MIT, `requirements_flexiconv.txt`) is an optional TEITOK adapter of the older Markdown converters
(`api_util/doc_to_visual_md.py`, `docx_to_md.py`, `pdf_to_md.py`); neither entry point loads it.

## Convert a file on the command line

```bash
python3 api_util/digital_to_json.py report.pdf  --docproc-out report.docproc.json
python3 api_util/digital_to_json.py report.odt  --document-json seed.json --out-dir out/
python3 api_util/digital_to_json.py budget.xls  --out-dir out/
python3 api_util/digital_to_json.py report.docx --out-dir out/ --rendered-out out/report.rendered.pdf
python3 api_util/digital_to_json.py finds.csv   --out-dir out/
python3 api_util/digital_to_json.py report.pdf  --engine docling --paradata-dir out/paradata/
python3 api_util/digital_to_json.py report.pdf  --out-dir out/ --markdown
```

| Option                    | Meaning                                                                                                                                                              |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--docproc-out` (`--out`) | where to write the docproc (default `<out-dir>/<doc_id>.docproc.json`)                                                                                               |
| `--document-json`         | the AMČR seed, read only: its `doc_id` names the output, its `source.sha512` is checked                                                                              |
| `--doc-id`                | override the doc_id derived from the file name (a seed's doc_id always wins)                                                                                         |
| `--engine`                | `light` (default) or `docling` (PDF only, needs `requirements_digital_docling.txt` and its models)                                                                   |
| `--rendered-out`          | also write an office document's rendered PDF (DOCX, DOC, ODT, RTF) when the ATR or page-classification reads it; the paradata name it either way, not stored         |
| `--paradata-dir`          | also write the run's paradata record (the original read, the docproc written, by SHA-512)                                                                            |
| `--markdown [PATH]`       | also write the document as annotated Markdown, as `/reformat`'s `markdown=true` (default `<doc_id>.md` beside the docproc)                                           |

`--markdown` runs the service's own code (`service/describe.py`), so a file gives the same Markdown on both sides.
`--describe` and its options belong to the parked per-page assessment ([parked features](reference/parked.md)).

### Exit codes

* `0`: the docproc was written (and the views asked for).
* `2` (from argparse): an unknown option or option value.
* `2`: a dependency is missing (a reader, Docling's models, LibreOffice for an office document or an XLS), with advice.
* `3`: an input this converter does not take. That means an unsupported type (an image among them), an old
  binary Office type it cannot convert, or a seed whose `source.sha512` does not match. It also means a
  `--document-json` seed that cannot be opened (`invalid_record`: not UTF-8 JSON, not an object, a newer
  schema). No PDF is refused for its pages: a page with an earlier OCR layer is routed to the ATR.
* `4`: an empty, corrupt or encrypted file, a CSV or JSON that does not parse, a reading that would skip content,
  a file over the ZIP limits, a failed LibreOffice conversion or rendering, or a file over a limit (`MAX_PAGES`,
  `LIBREOFFICE_TIMEOUT_S`, the ODF repeat cap, which would cut content).

A failed conversion writes no docproc. The docproc is written before the rendered PDF (`--rendered-out`) and the
Markdown, so a failure in those leaves the docproc on disk; the paradata then say `action_status: failed`.

## Call the service: `/reformat`

```bash
pip install -r service/requirements.txt
python -m service.api                                    # 0.0.0.0:8000
curl -F file=@report.pdf http://localhost:8000/reformat
curl -F file=@report.pdf -F document_json=@seed.json -F markdown=true http://localhost:8000/reformat
```

| Endpoint                         | Does                                                                                                 | Calls other services                                              |
|----------------------------------|------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| `POST /reformat`                 | file (+ seed) → `docproc`, Markdown on request, `paradata`                                           | **never**                                                         |
| `POST /describe` (parked)        | the per-page assessment ([parked features](reference/parked.md))                                     | **never**                                                         |
| `GET /info`, `/health`, `/ready` | the ATRIUM service contract: identity, limits, readers, LibreOffice availability                     | —                                                                 |

The command line gives every result of `/reformat`: the docproc, the rendered PDF and `--markdown` (above). Where
the two differ, and why: [The command line and the service](#the-command-line-and-the-service).

The runbook, [service/README.md](../service/README.md), documents the fields, every response key, the error
table (`source_digest_mismatch`, `unsupported_media_type`, `limit_exceeded`, `busy`, …), the
limits and the shutdown behaviour. The typed contract is [service/openapi.json](../service/openapi.json),
attached to every release.

## Run the Docker images

| Target            | Image                                         | What it is                                                                                |
|-------------------|-----------------------------------------------|-------------------------------------------------------------------------------------------|
| `api`             | `ghcr.io/ufal/atrium-digital-convert-api`     | **the production image**: `/reformat` (light engine + LibreOffice + FastAPI)              |
| `digital`         | `ghcr.io/ufal/atrium-digital-convert-digital` | the converter's command line (published, never pinned)                                    |
| `digital-docling` | built locally                                 | the heavy PDF engine, with Docling's models downloaded at build time                      |

The tag is the release **without** its leading `v` (`2.0.0-beta` for `v2.0.0-beta`); the target is
part of the image name.

The batch image passes its arguments to `api_util/digital_to_json.py` (`docker run IMAGE /data/report.pdf
--docproc-out /data/report.docproc.json`). Its entry point, the hub-canonical `container_entrypoint.py`, names
`api_util/digital_to_json.py` by its absolute path: a first argument that names a shell or Python runs that program
instead (`docker run IMAGE /bin/sh job.sh`), so a job script can run in the image. Only those interpreters run: an
input file that happens to be executable is still converted, as `digital-docling` does too.

```bash
docker compose --profile api up                                     # the service on :8000
docker compose --profile digital run --rm digital-convert-digital \
    /data/report.pdf --docproc-out /data/report.docproc.json
docker build --target digital-docling -t atrium-digital-convert-digital-docling .
```

* **Image contents.** [`.github/production-image.json`](../.github/production-image.json) declares
  the first-party files of the `api` image. The hub's `tools/ci/image_closure.py` checks it on every
  pull request; research tools and tests stay out (`.dockerignore`).
* **Image size.** LibreOffice (writer and calc, `-nogui`) adds roughly 300 MB to `digital` and `api`.

> [!NOTE]
> **Docker on Linux: run as yourself.** `./data` belongs to you, while the images run as uid 10001
> by default.
> * With compose: `docker-compose.yaml` runs every service as `user: "${ATRIUM_UID:-10001}:0"`, so
>   put your uid in `.env` once (`echo "ATRIUM_UID=$(id -u)" >> .env`).
> * With `docker run -v "$PWD:/data"`: pass `--user "$(id -u):0"`.
> * Docker Desktop (macOS, Windows) needs neither.

## The command line and the service

Both run one converter (`api_util/digital_to_json.py`: `prepare`, the docproc writer `api_util/docproc_writer.py`,
the paradata `run_config` and `record_run`) and the views of `service/describe.py`.
`tests/test_cli_service_parity.py` holds the two sides to the same docproc, byte for byte, and to the same Markdown
and refusals, over PDF, DOCX, ODT, RTF, DOC, XLSX, ODS and XLS files, a name with diacritics and spaces, an empty file,
a broken package, and seeds that match, mismatch or cannot be opened.

| Input or setting                   | Command line                                                                                                   | Service                                                           | Same?                                                                  |
|------------------------------------|----------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|------------------------------------------------------------------------|
| The formats, recognised by content | visual: PDF, DOCX, DOC, ODT, RTF; textual: ODS, XLSX, XLS, TXT, CSV, TSV, JSON                                 | the same                                                          | the same                                                               |
| An office document's rendered PDF  | `--rendered-out PATH`, written when the ATR or page-classification reads it                                    | `rendered_pdf`, returned then                                     | the same bytes and SHA-512                                             |
| An empty file                      | exit 4, `corrupt`                                                                                              | 422, `cause: corrupt`                                             | the same                                                               |
| A seed                             | `--document-json`; its `source.sha512` checked; read only                                                      | the `document_json` part; checked the same way                    | the same                                                               |
| A seed that cannot be opened       | exit 3, `invalid_record`                                                                                       | 422 `invalid_record`                                              | the same                                                               |
| The doc_id                         | from the file name; `--doc-id` overrides it; a seed's wins                                                     | from the upload's name; a seed's wins                             | the same derivation; `--doc-id` is for a pipeline driver               |
| Markdown                           | `--markdown`, the `full` profile; others with `api_util/json_to_md.py --detail`                                | `markdown=true`, the `full` profile                               | the same                                                               |
| The engine                         | `light`, or `--engine docling` (a local build)                                                                 | `light`                                                           | differs: Docling is not in the image                                   |
| Limits                             | `MAX_PAGES`, `LIBREOFFICE_TIMEOUT_S`, `MAX_LINE_CHARS`; `MAX_UPLOAD_MB` in the readers                         | all, and the upload itself, `MAX_CONCURRENT_JOBS`                 | differs: the service bounds what it accepts                            |
| Paradata                           | a file, with `--paradata-dir`                                                                                  | the answer's `paradata`, a `CreateAction`; config adds `endpoint` | the same parameters and counts                                         |
