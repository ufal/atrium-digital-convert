# Parked features

Two features of digital-convert are parked during the AMČR pilot. They are kept, tested and described here, but
they are not part of the pipeline: the per-page assessment (`POST /describe` and `--describe`), and the offline
tools that ask a decision model the document type of PDF pages. What the pipeline runs is in the
[README](../../README.md) and [usage](../usage.md).

## The per-page assessment: `/describe` and `--describe`

Parked during the AMČR pilot: not part of the pipeline. The service still answers `POST /describe`.

`/describe` runs the conversion of `/reformat` and then answers, page by page, from what the converter itself read:

* can the embedded text be used as it is?
* do its characters decode?
* where should the page go next?

It calls no other tool (since v2.0.0-beta). What kind of page it is, and whether its text reads, are answered by
page-classification and ocr-postprocess, which the pipeline runs as its own steps. Nothing the assessment finds
reaches the docproc.

### Asking for it

The service takes the fields of `/reformat` without `markdown` and `rendered`, plus `include_text` (`false` to
leave `pages[].text` out).

```bash
curl -F file=@report.pdf http://localhost:8000/describe
```

The command line gives the same report with `--describe` (`<doc_id>.describe.json`, beside the docproc):

| Option              | Meaning                                                                                             |
|---------------------|-----------------------------------------------------------------------------------------------------|
| `--describe [PATH]` | also write the per-page assessment: `reader`, `pages`, `summary` (default `<doc_id>.describe.json`) |
| `--no-text`         | with `--describe`: leave `pages[].text` out (`include_text=false`)                                  |

```bash
python3 api_util/digital_to_json.py report.pdf --out-dir out/ --describe
```

`--describe` runs the service's own code (`service/describe.py`), so a file gives the same assessment on both sides.

**Before v2.0.0-beta** `/describe` also asked page-classification and ocr-postprocess, when their URLs were set
(`stages`, `classify_pages`, `STAGE_TIMEOUT_S`, `ROUTE_TRASH_SHARE`, the answer's `stages[]` and the pages'
`category` and `quality`). That is gone: a deployment that still sets `PAGE_CLASSIFICATION_URL` or
`OCR_POSTPROCESS_URL` changes nothing, and a client that still sends `stages` gets the assessment without them.

### What it returns

An excerpt for the `garbled.pdf` test fixture (a PDF whose font encoding turns Czech diacritics into mojibake):

```json
{
  "pages": [{
    "page": "1", "page_index": 1,
    "text_layer": "garbled",
    "needs_atr": true,
    "needs_atr_reason": "embedded text layer does not decode: 3 of 3 lines carry CP1250 bytes read as CP1252 (mojibake diacritics), …",
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
              "needs_atr_pages": [1], "reacquire_pages": [1], "document_route": "ocr"}
}
```

The response is `/reformat`'s plus `document` (the converter's development dict) and:

* `pages[]`, one per page:
  * `page`, `page_index`;
  * `text_layer`: `digital` (decodes), `garbled` (a layer that does not decode: mojibake, or lines over 15 % glyphs
    without a Unicode mapping), `ocr` (a prior OCR run), `none` (no text layer: a scan, text drawn as curves, a page
    that draws nothing, or a page whose only text is its running header or footer over images covering at least
    `FURNITURE_ONLY_IMAGE_COVERAGE` of it), `blank` (an empty page: nothing on a page of a format without page
    images, or nothing rendered on a PDF page);
  * `needs_atr`, `needs_atr_reason` (docproc's `needsAtr`, `needsAtrReason`);
  * `decode`: the converter's decode check: `decode_score` (how the characters decode, not how the text reads),
    `lines_by_flag` (`decode:garbled`, `decode:inverted`), `lines_decoded`;
  * `route` and `route_reason`, below;
  * `layout`: `canvas`, `lines`, `blocks`, `tables`, `headings`, `regions`, `columns`, `images`, `vector_paths`,
    `words` (of the body text, outside the running header and footer), `damaged_words` (those of them that lost a
    letter to a glyph without a Unicode mapping, U+FFFD);
  * `text`, the page's text in reading order.
* `summary`: `pages`, `routes` (pages per route), `needs_atr_pages`, `reacquire_pages`, `document_route` (`nlp`,
  `ocr`, `htr`, `none` or `mixed`). `reacquire_pages` is the pages routed `ocr` or `htr`.

### Routes

Deterministic (`api_util/digital_report.py`), from the converter's own signals:

| The page                                        | Route  |
|-------------------------------------------------|--------|
| its text layer decodes                          | `nlp`  |
| no usable text layer (`garbled`, `ocr`, `none`) | `ocr`  |
| `none` and nothing drawn                        | `none` |
| blank                                           | `none` |

Words that lose a letter to a glyph without a Unicode mapping route nothing: they are counted, and their quality is
scoring's question.

`route_page` in the same module also holds the rules for a caller that has page-classification's and
ocr-postprocess's answers (`tools/samples_report.py` re-applies them to the runs of
[the samples](../evaluation/amcr-samples.md)): a page type sends a page without a usable text layer to `htr`
(handwritten only), `ocr` (printed, typed or tabular) or `none` (graphical only); a decoding page goes to `ocr` when
the quality model calls more than half of its prose lines `Trash` (at least 5 of them); and a decoding page whose only
text is its running header or footer goes where a text-bearing page type (`DRAW_L`, `PHOTO_L`, `TEXT_*`, `LINE_*`)
sends it. Categories are read through the page-category facets of `atrium_vocab.COLLECTIONS`, so a category added to
the registry is routed without a code change.

In the pipeline no route is taken from this report: digital-convert's docproc marks the pages that need the ATR
(`needsAtr`), and the pipeline's own steps classify the pages and score the lines.

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
