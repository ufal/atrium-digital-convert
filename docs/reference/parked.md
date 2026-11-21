# Parked features

Two features of digital-convert are parked during the AMČR pilot. They are kept, tested and described here, but
they are not part of the pipeline: the per-page assessment (`POST /describe` and `--describe`), and the offline
tools that ask a decision model the document type of PDF pages. What the pipeline runs is in the
[README](../../README.md) and [usage](../usage.md).

## The per-page assessment: `/describe` and `--describe`

Parked during the AMČR pilot: not part of the pipeline. The service still answers `POST /describe`.

`/describe` runs the conversion of `/reformat` and then answers, page by page:

* can the embedded text be used as it is?
* if not, what kind of page is it?
* does the text read?
* where should the page go next?

To answer the last three it asks page-classification and ocr-postprocess, when their URLs are configured. It sends
them the converter's development dict: record-shaped, never a record. Stages on record schema 2.0 may refuse it,
which is reported per stage. Nothing the assessment finds reaches the docproc.

### Asking for it

The service takes the fields of `/reformat` without `markdown` and `rendered`, plus:

| Field            | Meaning                                                                                                                                                                                                                                          |
|------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `stages`         | a comma-separated subset of `page-classification`, `ocr-postprocess`; empty → every configured one; `none` → none; an unknown name is a 422                                                                                                      |
| `classify_pages` | `needs_atr` (default): page-classification is asked about the pages without a usable text layer, and the pages whose only text is a running header or footer over an image; `all`: about every page; `needs_ocr`: deprecated name of `needs_atr` |
| `include_text`   | `false` to leave `pages[].text` out                                                                                                                                                                                                              |

```bash
curl -F file=@report.pdf http://localhost:8000/describe
curl -F file=@report.pdf -F stages=none http://localhost:8000/describe
```

The command line gives the same report with `--describe` (`<doc_id>.describe.json`, beside the docproc):

| Option                                               | Meaning                                                                                                                      |
|------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `--describe [PATH]`                                  | also write the per-page assessment: `reader`, `pages`, `summary`, `stages` (default `<doc_id>.describe.json`)                |
| `--stages`                                           | with `--describe`: `page-classification`, `ocr-postprocess`, both comma-separated, or `none`; unset = every configured one   |
| `--classify-pages`                                   | with `--describe`: `needs_ocr` (default; the service's `needs_atr`) or `all`, the pages page-classification is asked about   |
| `--no-text`                                          | with `--describe`: leave `pages[].text` out (`include_text=false`)                                                           |
| `--page-classification-url`, `--ocr-postprocess-url` | with `--describe`: the stages' base URLs (else `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`)                             |

```bash
PAGE_CLASSIFICATION_URL=http://localhost:8001 OCR_POSTPROCESS_URL=http://localhost:8002 \
    python3 api_util/digital_to_json.py report.pdf --out-dir out/ --describe --classify-pages all
```

`--describe` runs the service's own code (`service/describe.py`), so a file gives the same assessment on both sides.
A stage that is not configured, is down or answers badly is reported in `stages[]`; the run still exits 0, as the
service still answers 200. An unknown `--stages` value is exit status 2, as the service's 422.

**Trying it without the stages.** `tools/stage_stub.py` is a stand-in for both stages, never part of an image:
`docker compose --profile stub up`, or `python tools/stage_stub.py --port 8090` with
`PAGE_CLASSIFICATION_URL=http://127.0.0.1:8090 OCR_POSTPROCESS_URL=http://127.0.0.1:8090`.

### What it returns

An excerpt for the `garbled.pdf` test fixture (a PDF whose font encoding turns Czech diacritics into mojibake), with
no stage configured:

```json
{
  "pages": [{
    "page": "1", "page_index": 1,
    "text_layer": "garbled",
    "needs_atr": true,
    "needs_atr_reason": "embedded text layer does not decode: 3 of 3 lines carry CP1250 bytes read as CP1252 (mojibake diacritics), …",
    "category": null,
    "quality": null,
    "decode": {"decode_score": 0.9356, "lines_by_flag": {"decode:garbled": 3}, "lines_decoded": 0},
    "route": "ocr",
    "route_reason": "the text layer does not decode; page type unknown (page-classification not run): default to ATR",
    "layout": {"canvas": {"width": 612.0, "height": 792.0, "unit": "pt"}, "lines": 3, "blocks": 1,
               "tables": 0, "headings": 0, "columns": 1, "images": 0, "vector_paths": 0,
               "regions": {"page_header": 0, "page_footer": 0, "footnote": 0},
               "words": 16, "damaged_words": 0},
    "text": "Zpráva o sondì èíslo 3.\n…"
  }],
  "summary": {"pages": 1, "routes": {"nlp": 0, "ocr": 1, "htr": 0, "none": 0},
              "needs_atr_pages": [1], "reacquire_pages": [1], "document_route": "ocr"},
  "stages": [
    {"stage": "page-classification", "status": "not_configured", "detail": "PAGE_CLASSIFICATION_URL is not set", "record_adopted": false},
    {"stage": "ocr-postprocess",     "status": "not_configured", "detail": "OCR_POSTPROCESS_URL is not set",     "record_adopted": false}
  ]
}
```

The response is `/reformat`'s plus `document` (the converter's development dict, as the stages left it) and:

* `pages[]`, one per page:
  * `page`, `page_index`;
  * `text_layer`: `digital` (decodes), `garbled` (a layer that does not decode: mojibake, or lines over 15 % glyphs
    without a Unicode mapping), `ocr` (a prior OCR run), `none` (no text layer: a scan, text drawn as curves, a page
    that draws nothing, or a page whose only text is its running header or footer over images covering at least
    `FURNITURE_ONLY_IMAGE_COVERAGE` of it), `blank` (an empty page: nothing on a page of a format without page
    images, or nothing rendered on a PDF page);
  * `needs_atr`, `needs_atr_reason` (docproc's `needsAtr`, `needsAtrReason`);
  * `category`: page-classification's answer (`label`, `confidence`, `top`), or null. PDF only;
  * `quality`: `source: ocr-postprocess` (the common line-quality model: `score`, `band`, `lines_by_category`,
    `lang`, and `prose_lines_scored` / `prose_lines_by_category`, the lines its route rule counts; `score`, `lang`
    and `lines_scored` count only the lines it judged Clear, Noisy or Trash, not its pre-filter's `Non-text` and
    `Empty` placeholders); null when it did not score the page;
  * `decode`: the converter's decode check, apart from quality: `decode_score` (how the characters decode, not how
    the text reads), `lines_by_flag` (`decode:garbled`, `decode:inverted`), `lines_decoded`;
  * `route` and `route_reason`, below;
  * `layout`: `canvas`, `lines`, `blocks`, `tables`, `headings`, `regions`, `columns`, `images`, `vector_paths`,
    `words` (of the body text, outside the running header and footer), `damaged_words` (those of them that lost a
    letter to a glyph without a Unicode mapping, U+FFFD);
  * `text`, the page's text in reading order.
* `summary`: `pages`, `routes` (pages per route), `needs_atr_pages`, `reacquire_pages`, `document_route` (`nlp`,
  `ocr`, `htr`, `none` or `mixed`). `reacquire_pages` (routes `ocr` and `htr`) is the page list for text
  recognition or handwriting recognition: it adds the decoding pages the two rules below send to `ocr`, and leaves
  out the graphical pages, which `needs_atr_pages` (docproc's flags) keeps.
* `stages[]`, per stage: `status`, `detail`, `url`, `http_status`, `reason`, `elapsed_s`, `service_version`, `pages`
  sent, `record_adopted` (whether its write was adopted into the development dict), the stage's own `paradata`, and
  for ocr-postprocess `lines_unscored`.

### Routes

Deterministic (`api_util/digital_report.py`):

| The page                                                                                                         | Route                                                       |
|------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------|
| its text layer decodes                                                                                           | `nlp`, unless one of the two rules below sends it elsewhere |
| no usable text layer, category handwritten only (`TEXT_HW`, `LINE_HW`)                                           | `htr`                                                       |
| no usable text layer, printed, typed or tabular text (`TEXT_P`, `TEXT_T`, `TEXT`, `LINE_*`, `DRAW_L`, `PHOTO_L`) | `ocr`                                                       |
| no usable text layer, graphical only (`DRAW`, `PHOTO`)                                                           | `none`                                                      |
| no usable text layer, category unknown (classifier not run)                                                      | `ocr`                                                       |
| blank                                                                                                            | `none`                                                      |

Categories are read through the page-category facets of `atrium_vocab.COLLECTIONS` (handwritten, printed, typed,
graphical, tabular), so a category added to the registry is routed without a code change.

* **The quality model.** A page whose text layer decodes goes to `ocr` when ocr-postprocess calls more than
  `ROUTE_TRASH_SHARE` (0.5) of its *prose* lines `Trash`, and the page has at least 5 of them. A prose line is
  outside a table, at least 20 characters long, mostly letters and not a table-of-contents line. The model judges
  running text; on born-digital pages its other `Trash` lines are table cells, codes, coordinates and contents
  pages, whose text is exact. Counting every line would send 612 of the 1,981 born-digital AMČR pages of the samples
  to OCR; counting prose lines sends none ([the samples](../evaluation/amcr-samples.md)). Words that lose a letter to
  a glyph without a Unicode mapping route nothing: they are counted, and their quality is scoring's question.
* **Running header or footer only.** A decoding page whose only text is its running header or footer, over an image
  or a drawing, goes where its page type sends it when that type carries text (`DRAW_L`, `PHOTO_L`, `TEXT_*`,
  `LINE_*`): the image holds text the layer lacks, such as an excavation plan's labels and title block. With a
  graphical or unknown type it keeps its route. page-classification is asked about these pages by default.

In the pipeline no route is taken from this report: digital-convert's docproc marks the pages that need the ATR
(`needsAtr`), and a `Trash` line sends nothing back for another recognition pass.

### Stage statuses

* `ok`;
* `partial`: some pages failed, or some lines could not be scored;
* `not_configured`: its URL is unset;
* `not_requested`;
* `skipped`: nothing to do (not a PDF, no page needs classifying, no lines);
* `unavailable`: a connection error, a timeout (`STAGE_TIMEOUT_S`), or a 429 or 5xx answer;
* `error`: another 4xx, a payload that is not the expected JSON, or no line could be scored;
* `rejected`: the guard below refused its record.

A stage that is down or slow is reported, never a failed request.

**Lines ocr-postprocess could not score.** ocr-postprocess 2.0.0-beta and later refuse a record with a line that
fails perplexity even on its own (422 `limit_exceeded`), and the stage reads `error`. Earlier versions scored such a
line 99999 and noted it only in their `limits_applied`; the stage then read `partial` with `lines_unscored`, the
line kept the converter's values, and its page's score was recomputed from the lines that were scored.

**What the stages may write.** Each stage is sent the development dict as its `document_json` and writes only its
own fields: page-classification `page_categories` and `pages[].category/category_confidence`; ocr-postprocess
`lines[].categ/quality_score/lang` and `pages[].quality_score/quality_band` for every line, a flagged one too. The
converter's decode flags (`decode:garbled`, `decode:inverted`) and `decode_score` stay beside them. The returned dict
is adopted only when it is the same document with the converter's part intact: the same `doc_id`, `source`,
`content`, `tables`, page and line rows, and the converter's own fields unchanged (`text`, `bbox`, `group_id`,
`style`, `page_index`, `canvas`, `needs_ocr`, `needs_ocr_reason`, `text_layer`; a guard in `service/stages.py`).
Otherwise the stage is `rejected`, the dict stays as it was, and the stage's answer goes into `pages[]` only. An
example is `page_key_mismatch`: an older page-classification keys pages by physical number on a PDF whose pages
carry labels.

**Older stage versions.** A page-classification without the `pages` subset classifies every page: only the
requested ones are used. An ocr-postprocess without `/score_record` is asked page by page through `/process`
(`task_type=text`): its verdict goes into `pages[]`, not into the development dict.

### Large documents with `/describe`

Measured on 15 AMČR excavation reports, up to 156 MB and 529 pages ([what they showed](../evaluation/amcr-samples.md)):

* **Time.** `/describe` with both stages took up to 276 s (a 529-page report). page-classification took 135 s for its
  149 flagged pages and 202 s for all of them (`classify_pages=all`); ocr-postprocess took up to 76 s (88 s at 32
  lines a batch). `STAGE_TIMEOUT_S` is 360 s by default. A client, a proxy and `GRACEFUL_SHUTDOWN_S` must allow about
  5 minutes for one such request.
* **The stages' GPU.** This service needs none. page-classification takes 4.0 GB with its five models.
  ocr-postprocess's line-quality model peaked at 9.5 GB scoring 32 lines at a time, so the two fit one 24 GB card.
  At 128 lines the model peaked at 23.3 GB, and on a 24 GB card next to page-classification some batches ran out of
  memory.
* **The stages' own limits decide first.** The development dict `/describe` sends a stage reached 14 MB compact.
  page-classification also receives the whole PDF, and takes 10 MB per part and 50 pages per call by default: these
  samples need its `MAX_UPLOAD_MB` ≥ 160 and `MAX_PDF_PAGES` ≥ 157 (the most flagged pages in one document), or ≥ 529
  with `classify_pages=all`. ocr-postprocess takes a 25 MB record. A stage that refuses a part (413) is that
  document's stage `error`; the request still answers 200. Set the stages' limits together with this service's.

## Document types for decision models (offline)

Parked during the AMČR pilot: not part of the pipeline or the service.

Two research tools ask a decision model which AMČR document type (`dokument_typ`) a PDF page or a whole PDF is.
Neither is in an image or in the service, and nothing they find reaches the docproc or the record. The list of types
is a draft until AMČR agrees to it ([atrium-project#83](https://github.com/ufal/atrium-project/issues/83)).

**Images only.** A document type is the document's form: a report, a site photograph, a trench plan, a map. Form
is read from images, by this repository and atrium-page-classification, and never from text; the tools that read text (keyword-extract, ocr-postprocess) decide content and text quality. So a
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
atrium-page-classification is to take the picture types
([atrium-page-classification#58](https://github.com/ufal/atrium-page-classification/issues/58)).

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
