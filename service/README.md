# digital-convert API service

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

## Start

The published image first: it is what a deployment runs, and it needs no checkout.

```bash
docker run --rm -p 8000:8000 ghcr.io/ufal/atrium-digital-convert-api:<version>   # e.g. 1.3.0-beta
curl -s http://localhost:8000/ready                                               # 200 once ready
# from a checkout:
docker compose --profile api up
docker compose --profile stub up      # /describe with stand-ins for the two stages
# or in a virtual environment (development):
pip install -r service/requirements.txt          # the converter stack + the web server
python -m service.api                            # binds $HOST:$PORT (0.0.0.0:8000)
RELOAD=true python -m service.api                # with auto-reload
```

```bash
curl -F file=@report.pdf http://localhost:8000/reformat
curl -F file=@report.pdf -F markdown=true http://localhost:8000/reformat
curl -F file=@report.pdf -F document_json=@seed.json http://localhost:8000/reformat
curl -F file=@report.pdf http://localhost:8000/describe
curl -F file=@report.pdf -F stages=none http://localhost:8000/describe
```

## Models and caches

Nothing is downloaded at start or per request: the converter has no model and needs no network.

| What                                                    | From where                                       | Revision                           | Into          | Without egress                                                                |
|---------------------------------------------------------|--------------------------------------------------|------------------------------------|---------------|-------------------------------------------------------------------------------|
| LibreOffice (DOC and XLS only)                          | in the image                                     | the image's package                | —             | nothing needed; a deployment without it answers 501 for DOC/XLS               |
| page-classification, ocr-postprocess (`/describe` only) | `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL` | their own releases                 | —             | the stage reads `not_configured` or `unavailable`; the request still succeeds |
| Docling (optional engine)                               | a local build only, not the API image            | `requirements_digital_docling.txt` | its own cache | not part of the API image                                                     |

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

**The text layer.** Every page carries the converter's verdict in `pages[].text_layer`: `digital`
(decodes), `garbled` (a layer that does not decode: CP1250 read as CP1252, U+FFFD and control
characters), `ocr` (a prior OCR run), `none` (no text layer) or `blank` (an empty page of a format
without page images). The `garbled`, `ocr` and `none` pages also carry `pages[].needs_ocr: true`
with a `needs_ocr_reason`. A PDF whose text-bearing pages are at least `OCR_LAYER_DOCUMENT_SHARE`
(0.5) prior-OCR layers (invisible text over a page image) is refused, 422 `ocr_text_layer`, so the
route step sends it to OCR; below that share the document is converted and those pages are flagged.
atrium-ocr-postprocess (`v1.9.1-beta`) then merges the ATR ALTO of a flagged page back into the same
record, that page only (atrium-digital-convert#4 W4).

### `POST /describe` (multipart form)

The fields of `/reformat` (without `markdown`), plus:

| Field            | Meaning                                                                                                                                                                                             |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `stages`         | a comma-separated subset of `page-classification`, `ocr-postprocess`; empty → every configured one; `none` → none                                                                                   |
| `classify_pages` | `needs_ocr` (default): page-classification is asked about the pages without a usable text layer, and the pages whose only text is a running header or footer over an image; `all`: about every page |
| `include_text`   | `false` to leave `pages[].text` out                                                                                                                                                                 |

The response is `/reformat`'s plus:

* `pages[]` — one per page:
  * `page`, `page_index`;
  * `text_layer` — `digital` (decodes), `garbled` (a layer that does not decode), `ocr` (a prior OCR run), `none` (no text layer), `blank` (an empty page of a format without page images);
  * `needs_ocr`, `needs_ocr_reason`;
  * `category` — page-classification's answer (`label`, `confidence`, `top`), or null;
  * `quality` — `source: ocr-postprocess` (the common line-quality model: `score`, `band`, `lines_by_category`, `lang`, and `prose_lines_scored` / `prose_lines_by_category`: the lines its route rule counts; `score`, `lang` and `lines_scored` count only the lines it judged Clear, Noisy or Trash, not its pre-filter's `Non-text`/`Empty` placeholders) or `source: digital-convert` (the decode check only);
  * `route` + `route_reason`, below;
  * `layout` — `canvas`, `lines`, `blocks`, `tables`, `headings`, `regions`, `columns`, `images`, `vector_paths`, `words` (of the body text, outside the running header and footer), `damaged_words` (those of them that lost a letter to a glyph without a Unicode mapping, U+FFFD);
  * `text`.
* `summary` — `pages`, `routes` (pages per route), `needs_ocr_pages`, `reacquire_pages`, `document_route` (`nlp`, `ocr`, `htr`, `none` or `mixed`). `reacquire_pages` (routes `ocr` and `htr`) is the page list to hand to ATR/HTR: it adds the decoding pages the two rules below send to OCR and leaves out the graphical ones, which `needs_ocr_pages` (the record's flags) does not.
* `stages[]` — per stage: `status`, `detail`, `url`, `http_status`, `reason`, `elapsed_s`, `service_version`, `pages` sent, `record_adopted`, the stage's own `paradata`, and for ocr-postprocess `lines_unscored` (below).

**Routes.** Deterministic (`api_util/digital_report.py`):

| The page                                                                                                         | Route                                                                                                       |
|------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| its text layer decodes                                                                                           | `nlp`, unless one of the two rules below sends it to `ocr`                                                  |
| no usable text layer, category handwritten only (`TEXT_HW`, `LINE_HW`)                                           | `htr`                                                                                                       |
| no usable text layer, printed, typed or tabular text (`TEXT_P`, `TEXT_T`, `TEXT`, `LINE_*`, `DRAW_L`, `PHOTO_L`) | `ocr`                                                                                                       |
| no usable text layer, graphical only (`DRAW`, `PHOTO`)                                                           | `none`                                                                                                      |
| no usable text layer, category unknown (classifier not run)                                                      | `ocr`                                                                                                       |
| blank                                                                                                            | `none`                                                                                                      |

Categories are read through the page-category facets of `atrium_vocab.COLLECTIONS`
(handwritten / printed / typed / graphical / tabular), so a category added to the registry is routed
without a code change.

A page whose text layer decodes goes to `ocr` instead of `nlp` when either holds
([#5](https://github.com/ufal/atrium-digital-convert/issues/5)):

* **Lost letters.** At least `ROUTE_DAMAGED_WORD_SHARE` (0.05) of the words of its body text (outside
  the running header and footer) lose a letter to a glyph without a Unicode mapping (U+FFFD; a font
  without /ToUnicode). This is the converter's own evidence, so it holds with `stages=none` too. Since
  v1.2.2-beta the record flags the page itself: `needs_ocr: true`, `text_layer: garbled` and a reason
  naming the damaged words, so it reaches the OCR hand-off; `/describe` routes it to `ocr` whatever its
  page type.
* **The quality model.** ocr-postprocess calls more than `ROUTE_TRASH_SHARE` (0.5) of its *prose*
  lines `Trash`, and the page has at least 5 of them. A prose line is outside a table, at least 20
  characters long, mostly letters and not a table-of-contents line. The model judges running text;
  on born-digital pages its other `Trash` lines are table cells, codes, coordinates and contents
  pages, whose text is exact. Counted over every line, the rule sent 612 of 1,981 born-digital AMČR
  pages to OCR; counted over prose, none.

A decoding page whose only text is its running header or footer, over an image or a drawing, goes
where its page type sends it when that type carries text (`DRAW_L`, `PHOTO_L`, `TEXT_*`, `LINE_*`):
the image holds text the layer lacks, such as an excavation plan's labels and title block. With a
graphical or unknown type it keeps its route. page-classification is asked about these pages by
default.

**Stage statuses.** `ok`, `partial` (some pages failed, or some lines could not be scored),
`not_configured` (its URL is unset), `not_requested`, `skipped` (nothing to do: not a PDF, no page
needs classifying, no lines), `unavailable` (connection error, timeout `STAGE_TIMEOUT_S`, 429/5xx),
`error` (another 4xx, a payload that is not the expected JSON, or no line could be scored),
`rejected` (the guard below refused its record).

**Lines ocr-postprocess could not score.** From its v1.12.0-beta a perplexity batch that runs out
of GPU memory is split and retried; a line that fails even on its own is scored 99999 (usually
`Trash`) and noted only in its answer's `limits_applied` (`ppl_batch_lines`, `skipped`). Such a
line keeps the converter's values in the record, its page's `quality_score`/`quality_band` are
recomputed from the lines that were scored (the page keeps the converter's values when none was),
it is left out of the page's `quality`, and the stage reads `partial` with `lines_unscored`, the
count. When no line could be scored the stage is `error` and the record is not written.

**What the stages may write.** Each stage is sent the current record and writes only its own fields
through its own accretion: page-classification `page_categories` and `pages[].category/
category_confidence`; ocr-postprocess `lines[].categ/quality_score/lang` and `pages[].quality_score/
quality_band`, never over a line carrying the converter's decode verdict (`Garbage`/`Inverted`).
The returned record is adopted only when it is the same document with the converter's part intact
— the same `doc_id`, `source`, `content`, `tables`, page and line rows, and the converter's own
fields (`text`, `bbox`, `group_id`, `style`, `page_index`, `canvas`, `needs_ocr`, `needs_ocr_reason`,
`text_layer`) unchanged — and when it validates. Otherwise the stage is `rejected` (e.g. `page_key_mismatch`:
an older page-classification keys pages by physical number on a PDF whose pages carry labels)
and the record stays as it was; the stage's answer still goes into `pages[]`.

**Older stage versions.** A page-classification without the `pages` subset classifies every page:
only the requested ones are used. An ocr-postprocess without `/score_record` is asked page by page
through `/process` (`task_type=text`): its verdict goes into `pages[]`, not into the record.

## Errors

Every error has the harmonised body `{status, reason, detail}` (§4.4); `reason` is a registered
code or `null`, and refusals of a born-digital input name the converter's finer cause in `cause`.

| Status | `reason`                 | When                                                                                                                          | What to do                                                                         |
|--------|--------------------------|-------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| 413    | `limit_exceeded`         | a part over `MAX_UPLOAD_MB`                                                                                                   | raise `MAX_UPLOAD_MB` for large originals (see Large documents), or split the file |
| 415    | `unsupported_media_type` | not a born-digital type the converter reads (`cause`: `unsupported`, `legacy_office_unsupported`; `accepted` lists the types) | send a type in `accepted`; a scanned document goes to OCR                          |
| 422    | `ocr_text_layer`         | a PDF whose text layer is an earlier OCR run (route it to OCR)                                                                | send the document to the OCR route                                                 |
| 422    | `source_digest_mismatch` | the seed's `source.sha512` is not the uploaded file's                                                                         | send the file the seed was made for, or a new seed                                 |
| 422    | `invalid_record`         | `document_json` cannot be opened (not UTF-8 JSON, not an object, a newer schema major)                                        | send the seed AMČR made; update this service if the record is newer                |
| 422    | `limit_exceeded`         | over `MAX_PAGES` or `LIBREOFFICE_TIMEOUT_S`                                                                                   | raise the setting `limit.env` names, or split the document                         |
| 422    | `null`                   | a broken file (`cause`: `corrupt`, `encrypted`, `zip_limits_exceeded`, `conversion_failed`); an unknown `stages` name         | repair or decrypt the file `cause` names; fix the `stages` value                   |
| 429    | `busy`                   | `MAX_CONCURRENT_JOBS` conversions already running; retry after `Retry-After`                                                  | retry after `Retry-After`; raise `MAX_CONCURRENT_JOBS` if memory allows            |
| 501    | `null`                   | a DOC/XLS with no LibreOffice in this deployment (`cause`: `dependency_missing`)                                              | use the image (it has LibreOffice), or convert DOC/XLS to DOCX/XLSX first          |
| 503    | `null`                   | the service is shutting down; retry against a live replica                                                                    | retry on another replica                                                           |

`source_digest_mismatch` is in the shared registry (`service/atrium_service.py`, vendored from the hub)
since v1.1.1-beta, so every ATRIUM service's spec publishes it; v1.1.0-beta registered it here.

## Partial results

A 200 can carry less than was asked for. Read these before using the result:

- `needs_ocr` pages (and `summary.needs_ocr_pages`) have no usable text layer; with `/describe`,
  `summary.reacquire_pages` lists every page OCR should read again, including pages whose letters do
  not decode.
- A `/describe` stage other than `ok` (see [Stage statuses](#post-describe-multipart-form)): `partial`
  (some pages failed, or `lines_unscored` lines kept the converter's values), `not_configured`,
  `unavailable`, `skipped`, `error` or `rejected`. The converter's record is still returned.
- `limits_applied` lists each limit that shaped the result without refusing it (none today: each limit
  refuses).

## Known limits

- Headings come from font size and bold body-size titles, not from a layout model, so a complex layout
  gets fewer headings than a layout model finds; `BOLD_HEADING_MAX_SHARE=0` switches the bold-title rule
  off.
- Only a PDF gives line boxes; the other formats give pages and lines without positions.
- A PDF page whose fonts do not map to Unicode loses its letters: from `ROUTE_DAMAGED_WORD_SHARE` of its
  body words on, the record flags it `needs_ocr` (`garbled`), and OCR reads it again.
- Unruled tables are read by column, and letter-spaced captions and map labels split into pieces.
- The upload is held in memory: the default 250 MB accepts most reports, and larger originals need
  `MAX_UPLOAD_MB` raised per deployment; above 250 MB that is not yet tested (see Large documents).

## Configuration (environment)

| Variable                  | Default   | Meaning                                                                                   |
|---------------------------|-----------|-------------------------------------------------------------------------------------------|
| `PORT`                    | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes (issues #55, #58) |
| `HOST`                    | `0.0.0.0` | bind address (issue #58); see the warning below                                           |
| `GRACEFUL_SHUTDOWN_S`     | `20`      | seconds uvicorn waits for in-flight requests (issue #55)                                  |
| `RELOAD`                  | `false`   | filesystem auto-reload — development only                                                 |
| `LOG_LEVEL`               | `INFO`    | root logger level for the `python -m service.api` start path (issue #61)                  |
| `ALLOWED_ORIGINS`         | `*`       | CSV of CORS origins                                                                       |
| `PAGE_CLASSIFICATION_URL` | —         | base URL of atrium-page-classification, for `/describe`; unset → `not_configured`         |
| `OCR_POSTPROCESS_URL`     | —         | base URL of atrium-ocr-postprocess, for `/describe`; unset → `not_configured`             |
| `LIBREOFFICE_BIN`         | —         | the LibreOffice binary for DOC/XLS; unset → `soffice` or `libreoffice` on `PATH`          |

Every limit and setting — `MAX_UPLOAD_MB`, `MAX_PAGES`, `OCR_LAYER_DOCUMENT_SHARE` and the rest — is
listed under [Limits](#limits); `.env.example` is the complete ledger.

> **Note:** `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`.

## Limits

Every limit and setting is an environment variable (atrium-project#53, factor III), declared in
`tool_limits.py` and reported with its current value by `GET /info` (`limits`; `limits_meta` says
which variable sets it and whether the value came from the environment or the default). A
malformed value stops the service at startup, naming the variable. `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)                   | Variable                            | Default | Unit    | Over the limit                                                                                                                    |
|---------------------------------|-------------------------------------|---------|---------|-----------------------------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`                 | `MAX_UPLOAD_MB`                     | 250     | MB      | 413 `limit_exceeded` — per part (the file, `document_json`)                                                                       |
| `max_pages`                     | `MAX_PAGES`                         | 2000    | pages   | 422 `limit_exceeded` — split the document                                                                                         |
| `max_concurrent_jobs`           | `MAX_CONCURRENT_JOBS`               | 2       | jobs    | 429 `busy` with `Retry-After`                                                                                                     |
| `libreoffice_timeout_s`         | `LIBREOFFICE_TIMEOUT_S`             | 120     | s       | 422 `limit_exceeded` — a DOC/XLS LibreOffice did not convert in time                                                              |
| `stage_timeout_s`               | `STAGE_TIMEOUT_S`                   | 360     | s       | `/describe`: the stage is reported `unavailable`; the request still succeeds                                                      |
| `ocr_layer_document_share`      | `OCR_LAYER_DOCUMENT_SHARE`          | 0.5     | share   | policy: at or above it a PDF with prior-OCR pages is refused (422 `ocr_text_layer`); below it those pages carry `needs_ocr`       |
| `route_trash_share`             | `ROUTE_TRASH_SHARE`                 | 0.5     | share   | policy (`/describe`): a decoding page goes to `ocr` instead of `nlp` when the quality model calls more of its prose lines `Trash` |
| `route_damaged_word_share`      | `ROUTE_DAMAGED_WORD_SHARE`          | 0.05    | share   | policy (`/describe`): a decoding page goes to `ocr` instead of `nlp` when this share of its body's words lose a letter (U+FFFD)   |
| `furniture_only_image_coverage` | `FURNITURE_ONLY_IMAGE_COVERAGE`     | 0.85    | share   | policy: a PDF page whose only text is its running header or footer is `none` (`needs_ocr`) when its images cover this share of it |
| `bold_heading_max_share`        | `BOLD_HEADING_MAX_SHARE`            | 0.25    | share   | policy: a bold body-size line that reads as a title is a heading while bold body-size text is at most this share; 0 = off         |
| `odf_repeat_cap`                | `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP` | 100     | repeats | the shared reader expands a repeated ODF cell or row at most this often                                                           |
| `pdf_object_cap`                | `ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP` | 20000   | objects | the shared reader judges a PDF page's text layer on its first N objects                                                           |

**Large documents** (measured on the 15 AMČR samples of
[#5](https://github.com/ufal/atrium-digital-convert/issues/5): up to 156 MB and 529 pages):

* **Upload.** The upload is held in memory, so the 250 MB default (AMČR's operational setting)
  accepts every sample. AMČR's originals reach about 1.5 GB: a deployment can raise `MAX_UPLOAD_MB`
  for them, but above 250 MB memory, concurrency and timeouts are not yet tested (measured up to
  156 MB), or the orchestration splits the file by pages; the 413 names the setting.
* **Memory follows the text, not the file size.** The server's peak reached 783 MB, for a 282-page
  report, and 560 MB for the 156 MB one (10-14, after the light engine stopped holding every page's
  parse; 1.9 GB before). Budget about 1 GB plus `MAX_UPLOAD_MB` per `MAX_CONCURRENT_JOBS` slot.
* **The stages' GPU.** This service needs none. Page-classification takes 4.0 GB with its five
  models. ocr-postprocess's line-quality model peaked at 9.5 GB scoring 32 lines at a time
  (`PPL_BATCH_LINES`: its default from v1.12.0-beta, which also splits and retries a batch that runs out of memory; set it on older ones), so the two fit one
  24 GB card. At 128 lines it peaked at 23.3 GB, and on a 24 GB card next to page-classification
  some batches ran out of memory (1,280 lines in 6 documents). Their lines were scored worst while
  the stage read `ok`; since v1.2.0-beta they keep the converter's values and the stage reads
  `partial` (above).
* **Time.** `/reformat` took 3–58 s per document. `/describe` with both stages took up to 276 s
  (a 529-page report): page-classification 135 s for its 149 flagged pages, 202 s for all of them
  (`classify_pages=all`), ocr-postprocess up to 76 s (88 s at 32 lines a batch). `STAGE_TIMEOUT_S`
  is 360 s since then; a client, a proxy and `GRACEFUL_SHUTDOWN_S` must allow about 5 minutes for
  one such request.
* **The stages' own limits decide `/describe` first.** The record part `/describe` sends a stage
  reached 14 MB compact. Page-classification also receives the whole PDF, and takes 10 MB per
  part and 50 pages per call by default: these samples need `MAX_UPLOAD_MB` ≥ 160 and
  `MAX_PDF_PAGES` ≥ 157 (the most flagged pages in one document), or ≥ 529 with
  `classify_pages=all`. ocr-postprocess takes a 25 MB record. A stage that refuses a part (413) is
  that document's stage `error`; the request still answers 200. Set the stages' limits together
  with this service's.

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
   (`api_util/digital_report.py`), both run from `service/describe.py`.

The command line runs the same steps and the same two views: `--markdown` is `markdown=true`, `--describe`
is `/describe`, with `--stages`, `--classify-pages` and `--no-text` for its fields and the same
`PAGE_CLASSIFICATION_URL` / `OCR_POSTPROCESS_URL` ([docs/usage.md](../docs/usage.md#command-line-api_utildigital_to_jsonpy)).
`tests/test_cli_service_parity.py` holds the two sides to the same record, Markdown and assessment.

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
pytest tests/test_cli_service_parity.py                            # the CLI and the service agree
```

`tests/test_describe.py` serves `tools/stage_stub.py` — a model-free stand-in for page-classification
and ocr-postprocess with their request and response shapes — on a local port, and drives the
failure modes (down, timeout, 500, older versions, a rewriting stage) through `requests`.
