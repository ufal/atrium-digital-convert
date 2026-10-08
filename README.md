<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" title="Python Version"></a>
  <a href="https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md"><img src="https://img.shields.io/badge/record-atrium__document%201.0-2E8B57.svg" title="Document record schema"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-digital-convert" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# 📄 ATRIUM digital-convert — born-digital documents into `atrium_document` records

This is the **born-digital stage** of the ATRIUM pipeline. A document that was created on a
computer already holds its text. digital-convert reads that text, with its page structure,
reading order, headings, tables and (for PDF) exact geometry, and writes it as an
**`atrium_document` record**. The record is the JSON every other ATRIUM stage accretes onto.
No OCR, no model, no network.

* **Inputs**, recognised by content: **PDF, DOCX, ODT, ODS, XLSX, RTF**, and the legacy **DOC and
  XLS** through headless LibreOffice.
* **Output**: the record (`source`, `pages`, `lines`, `content`, `tables`, `provenance`), validated
  against the frozen schema. On request it is also rendered as annotated Markdown.
* **Two ways to run it**: the command line (`api_util/digital_to_json.py`) and the HTTP service
  `api-digital` (`service/api.py`), which has two endpoints:
  * **`POST /reformat`**: file in, record out. This is the endpoint the AMČR route calls. It calls
    no other service; in the AMČR deployment Temporal chains the stages.
  * **`POST /describe`**: the same conversion, plus a **per-page assessment**. For every page it
    reports:
    * whether the embedded text is usable;
    * if it is not, what kind of page it is (page-classification);
    * whether the text reads (ocr-postprocess);
    * where the page goes next (NLP, OCR or HTR);
    * its layout and its text.
    It calls the two stages only when their URLs are configured; until they are deployed, it
    reports them as not configured and answers from the converter alone.

> [!NOTE]
> **This repository was `atrium-llm-enrich` until v1.0.0-beta.**
> * The keyword stage it used to hold (the LLM over the AMČR/TEATER vocabularies, `/extract_keywords`)
>   moved to [atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract), see
>   [#1](https://github.com/ufal/atrium-digital-convert/issues/1). The removed files can be
>   recovered from tag `v1.0.0-beta`; the transfer manifest is in
>   [`agent_dev_logs/digests/1.digest.md`](agent_dev_logs/digests/1.digest.md).
> * Since **v1.1.0-beta** the service id is `atrium-digital-convert` (the spec declares the rename,
>   `x-atrium-service-previous: atrium-llm-enrich`), the images are
>   `ghcr.io/ufal/atrium-digital-convert-{api,digital}`, and the record program id stays
>   `digital-convert`.

## Table of contents

- [Where it sits in the pipeline](#where-it-sits-in-the-pipeline)
- [⚙️ Setup](#-setup)
- [Command line (`api_util/digital_to_json.py`)](#command-line-api_utildigital_to_jsonpy)
- [Formats](#formats)
- [What the record holds](#what-the-record-holds)
- [The service: `/reformat` and `/describe`](#the-service-reformat-and-describe)
- [The per-page assessment](#the-per-page-assessment)
- [Record → annotated Markdown](#record--annotated-markdown)
- [Configuration](#configuration)
- [🐳 Docker](#-docker)
- [Paradata and provenance](#paradata-and-provenance)
- [📐 Document Understanding benchmark (research tools)](#-document-understanding-benchmark-research-tools)
- [Development](#development)
- [Acknowledgements](#acknowledgements-)

## Where it sits in the pipeline

```
AMČR upload ──► seed record (doc_id, source.sha512, filename, media_type)
                   │
                   ▼
   born-digital? ──yes──► digital-convert  /reformat ──► record ──► nlp-enrich, keyword-extract, …
                   │                          │
                   no                         └─ pages flagged needs_ocr ──► the OCR/HTR route
                   ▼                                (ocr-postprocess merges the re-OCR'd pages)
            OCR / HTR route
```

* **The record's originator.** For a `digital-born-*` record, digital-convert alone writes the
  positional plane (`pages`, `lines`, `content`, `tables`). `atrium_document.ORIGIN_ORIGINATORS`
  enforces that rule. Every later stage adds its own blocks to the same record.
* **The AMČR seed.** With a seed (`document_json`), the record keeps the seed's `doc_id` and source
  facts. The seed's `source.sha512` is checked against the uploaded bytes before anything is
  parsed: a mismatch is `source_digest_mismatch` (HTTP 422, CLI exit 3), so a record is never
  attached to the wrong file.
* **Pages the converter cannot vouch for** are flagged `pages[].needs_ocr`, with a reason, and every
  page says why in `pages[].text_layer` (`digital`, `garbled`, `ocr`, `none`, `blank`):
  * no text layer (a scan, text drawn as curves): `none`;
  * a text layer that does not decode (broken font encodings, replacement characters): `garbled`;
  * a few pages that are a prior OCR run: `ocr`.
  The record is still written, and those pages go to the OCR/HTR route. atrium-ocr-postprocess
  merges the ATR ALTO of each such page back into the same record, that page only. A PDF that is mostly a
  prior OCR run (at least `OCR_LAYER_DOCUMENT_SHARE` of its pages, default 0.5) is not born-digital.
  It is refused as `ocr_text_layer` and belongs to the OCR route as a whole.
* **The legacy DOC and XLS** were accepted by AMČR on
  [#4](https://github.com/ufal/atrium-digital-convert/issues/4) (2026-09-26). LibreOffice
  converts the file to DOCX/XLSX first and is not counted in the record's licence; see
  [Paradata and provenance](#paradata-and-provenance).

## ⚙️ Setup

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

## Formats

The type is decided by the file's content. The extension only names the file, so a mislabelled
upload is read as what it is.

| Input | Recognised by                        | Reader                                                                      | `source.origin`     | Pages                                                  | Geometry                             |
|-------|--------------------------------------|-----------------------------------------------------------------------------|---------------------|--------------------------------------------------------|--------------------------------------|
| PDF   | `%PDF-`                              | `digital_pdf.py` (pdfplumber, pypdfium2), or Docling (`--engine docling`)   | `digital-born-pdf`  | PDF pages, named by `/PageLabels` (`i`, `ii`, `A-1`)   | exact boxes, points, top-left origin |
| DOCX  | ZIP with `word/document.xml`         | `digital_docx.py` (python-docx, lxml)                                       | `digital-born-docx` | explicit, section and Word's last rendered page breaks | none                                 |
| ODT   | ZIP, ODF `mimetype`                  | `text_formats.py`, the shared reader (vendored from atrium-ocr-postprocess) | `digital-born-odt`  | explicit and soft page breaks                          | none                                 |
| ODS   | ZIP, ODF `mimetype`                  | `text_formats.py`                                                           | `digital-born-ods`  | one per sheet, labelled by the sheet name              | none                                 |
| XLSX  | ZIP with `xl/workbook.xml`           | `text_formats.py`                                                           | `digital-born-xlsx` | one per sheet, labelled by the sheet name              | none                                 |
| RTF   | `{\rtf`                              | `text_formats.py`                                                           | `digital-born-rtf`  | `\page` breaks                                         | none                                 |
| DOC   | OLE2 with a `WordDocument` stream    | headless LibreOffice → DOCX → `digital_docx.py`                             | `digital-born-doc`  | as DOCX                                                | none                                 |
| XLS   | OLE2 with a `Workbook`/`Book` stream | headless LibreOffice → XLSX → `text_formats.py`                             | `digital-born-xls`  | one per sheet                                          | none                                 |

* **Refused with a reason:**
  * PPTX, ODP, EPUB, plain text and any other type: `unsupported`, HTTP 415, which lists the
    accepted types;
  * an encrypted file: `encrypted`;
  * a broken one: `corrupt`;
  * a ZIP bomb: `zip_limits_exceeded`.
* **Only PDF has geometry.** The other formats have no page coordinates without rendering them, so
  their records carry no `bbox` and no `canvas`. That follows the schema's rule against fabricated
  boxes.
* **Spreadsheets.** A row's text cells become one line, joined by a tab. Numbers, dates and
  formulas are not text and are dropped by the reader. Each sheet is one `group_id`, so a consumer
  that builds paragraphs from groups keeps a sheet together. Spreadsheet `tables[]` (cell grids)
  are a follow-up.
* **The shared reader.** ODT, ODS, XLSX and RTF go through atrium-ocr-postprocess's reader (the hub's
  "one document reader", atrium-project#72), not a second fork.
  `tests/test_vendored_reader_parity.py` pins the copy by its SHA-256. The para-drift workflow
  compares it with ocr-postprocess's `test` head, and the hub's `scripts/revendor_shared.sh`
  refreshes it.
* **DOC/XLS conversion:**
  * LibreOffice runs headless, with a private, throw-away profile and a time limit
    (`LIBREOFFICE_TIMEOUT_S`). `LIBREOFFICE_BIN` names the binary (default: `soffice` or
    `libreoffice` on `PATH`).
  * The record keeps the original file's name, media type and digest, so it describes the DOC/XLS
    that was uploaded, not the converted file.
  * Without LibreOffice a DOC/XLS fails with `dependency_missing` (HTTP 501, CLI exit 2); every
    other format still works.

## What the record holds

| Layout cue                | PDF (light engine)                                         | DOCX                                                     | ODT / RTF             | ODS / XLSX            | Record field                                   |
|---------------------------|------------------------------------------------------------|----------------------------------------------------------|-----------------------|-----------------------|------------------------------------------------|
| Pages                     | PDF pages, named by `/PageLabels`                          | explicit, section and Word's last rendered page breaks   | page breaks           | sheets                | `pages[]` (`page`, `page_index`)               |
| Text blocks / paragraphs  | vertical gaps, per column                                  | one per paragraph, one per table cell                    | paragraph, table cell | row                   | `lines[]`, `lines[].group_id`                  |
| Reading order             | words, columns (column-major), headers first               | document order; text boxes after their anchor            | document order        | row order             | `lines[]` order                                |
| Headings                  | font size against the body size                            | outline level, then style name (`Heading N`, `Nadpis N`) | —                     | —                     | `lines[].style.heading_level`                  |
| Bold / italic             | font name                                                  | run → character style → paragraph style                  | —                     | —                     | `lines[].style.bold` / `.italic`               |
| Running header / footer   | margin lines repeated across pages, page numbers           | each section's header and footer                         | —                     | —                     | `lines[].style.region`                         |
| Footnotes                 | — (Docling engine: yes)                                    | footnotes and endnotes                                   | —                     | —                     | `lines[].style.region = footnote`              |
| Tables                    | ruled tables (Docling engine: any)                         | tables, merged cells                                     | cells as lines        | — (follow-up)         | `tables[]` + `cells[].group_id`                |
| Bounding boxes, page size | exact, points, top-left origin                             | none                                                     | none                  | none                  | `lines[].bbox`, `pages[].canvas`               |
| Untrustworthy text        | mojibake, replacement characters, no text layer, prior OCR | the same decode check                                    | the same decode check | the same decode check | `lines[].categ = Garbage`, `pages[].needs_ocr` |

* **Engines.**
  * `--engine light` (default) uses `requirements_digital.txt`: permissive licences, no models, no
    network.
  * `--engine docling` is opt-in and PDF only (`requirements_digital_docling.txt`). Docling's layout
    and table models decide reading order, headings, furniture, footnotes and tables on complex
    pages, and the light engine's lines keep their exact geometry.
  * Docling needs its model weights: `docling-tools models download layout tableformer -o DIR` and
    `DOCLING_ARTIFACTS_PATH=DIR`. The Docker target `digital-docling` does this at build time; it is
    built locally, never published. OCR stays off in both engines.
* **Output gate.** A record is never written if it fails any of these:
  * the field-ownership round trip (only digital-convert's own fields);
  * the JSON Schema;
  * the seed digest check.

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
documented in [service/README.md](service/README.md) 📎. The typed contract is
[service/openapi.json](service/openapi.json) 📎, attached to every release.

## The per-page assessment

`/describe` answers, page by page:
* can the embedded text be used as it is?
* if not, what kind of page is it?
* does the text read?
* where should the page go next?

An excerpt for the `garbled.pdf` test fixture (a PDF whose font encoding turns Czech diacritics
into mojibake), with no stage configured:

```json
{
  "pages": [{
    "page": "1", "page_index": 1,
    "text_layer": "garbled",
    "needs_ocr": true,
    "needs_ocr_reason": "embedded text layer does not decode: 3 of 3 lines carry CP1250 bytes read as CP1252 (mojibake diacritics), …",
    "category": null,
    "quality": {"source": "digital-convert", "score": 0.9356, "band": "Clear",
                "lines_by_category": {"Garbage": 3, "decoded": 0}, "lang": null, "lines_scored": 3},
    "route": "ocr",
    "route_reason": "the text layer does not decode; page type unknown (page-classification not run): default to ATR",
    "layout": {"canvas": {"width": 612.0, "height": 792.0, "unit": "pt"}, "lines": 3, "blocks": 1,
               "tables": 0, "headings": 0, "columns": 1, "images": 0, "vector_paths": 0,
               "regions": {"page_header": 0, "page_footer": 0, "footnote": 0}},
    "text": "Zpráva o sondì èíslo 3.\n…"
  }],
  "summary": {"pages": 1, "routes": {"nlp": 0, "ocr": 1, "htr": 0, "none": 0},
              "needs_ocr_pages": [1], "reacquire_pages": [1], "document_route": "ocr"},
  "stages": [
    {"stage": "page-classification", "status": "not_configured", "detail": "PAGE_CLASSIFICATION_URL is not set", "record_adopted": false},
    {"stage": "ocr-postprocess",     "status": "not_configured", "detail": "OCR_POSTPROCESS_URL is not set",     "record_adopted": false}
  ]
}
```

* **Text layer** (`text_layer`), from the converter; the record carries the same value in
  `pages[].text_layer`:
  * `digital`: the layer decodes;
  * `garbled`: a layer that does not decode;
  * `ocr`: a prior OCR run;
  * `none`: no text layer;
  * `blank`: an empty page of a format without page images.
* **Page type** (`category`): page-classification's label, confidence and top-N. It is asked about
  the `needs_ocr` pages by default, or about every page with `classify_pages=all`. PDF only.
* **Readability** (`quality`):
  * with ocr-postprocess configured: its line-quality model (Clear / Noisy / Trash per line, the
    page band, the language);
  * without it: the converter's decode check only. Its `score`/`band` measure how the characters
    decode, not how the text reads.
* **Route** (`route`), deterministic, with `route_reason`:

  | The page                                           | Route                                                                                                |
  |----------------------------------------------------|------------------------------------------------------------------------------------------------------|
  | its text layer decodes                             | `nlp`, unless ocr-postprocess calls more than `ROUTE_TRASH_SHARE` (0.5) of its lines `Trash` → `ocr` |
  | no usable text, handwritten (`TEXT_HW`, `LINE_HW`) | `htr`                                                                                                |
  | no usable text, printed, typed or tabular          | `ocr`                                                                                                |
  | no usable text, graphical only (`DRAW`, `PHOTO`)   | `none`                                                                                               |
  | no usable text, page type unknown                  | `ocr`                                                                                                |
  | blank                                              | `none`                                                                                               |

  The categories are read through the page-category facets of `atrium_vocab.COLLECTIONS`, so a
  category added to the vocabulary registry is routed without a code change.
* **Layout and text** (`layout`, `text`): the converter's counts per page, and the page's text in
  reading order (`include_text=false` leaves the text out).
* **Stages** (`stages[]`), one entry per stage:
  * the `status`: `ok`, `partial`, `not_configured`, `not_requested`, `skipped`, `unavailable`,
    `error` or `rejected`;
  * how long the call took, and the stage's version;
  * whether its record was adopted.

  A stage that is down or slow is reported, never a failed request.
* **The stages' record writes.** Each stage may write only its own fields:
  * page-classification: the page categories;
  * ocr-postprocess: the line and page quality.

  digital-convert adopts the returned record only when the converter's own part is intact (a guard
  in `service/stages.py`); otherwise the stage is `rejected` and its answer goes into `pages[]`
  only.
* **Trying it before the stages are deployed.** `tools/stage_stub.py` is a stand-in for both
  stages: `docker compose --profile stub up`, or
  `python tools/stage_stub.py --port 8090` with `PAGE_CLASSIFICATION_URL=OCR_POSTPROCESS_URL=http://127.0.0.1:8090`.
  It is never part of an image.

## Record → annotated Markdown

`/reformat` with `markdown=true` returns the record rendered as **annotated Markdown**. The same
renderer is available on the command line, and the TEITOK/ALTO renderer sits beside it:

```bash
python3 api_util/json_to_md.py CTX000000001.document.json --detail standard
python3 api_util/xml_to_md.py  CTX000000001.teitok.xml --format layout --detail minimal
python3 api_util/doc_to_visual_md.py report.pdf --output report.md         # convert + render in one step
```

* **The format.** The Markdown is page-sectioned (`## Page N`). The visual-layout cues ride in HTML
  comments (`DOC_META`, `BBOX`, `PAGE_BREAK`, `NEEDS_OCR`, `HEADER_*`/`FOOTER_*`, headings, GFM
  tables, footnotes). The full taxonomy is `CUE_SCHEMA` in [`api_util/layout_md.py`](api_util/layout_md.py) 📎.
* **The one model-facing representation.** Annotated Markdown is the single general input a language
  model is given (digital-convert#3). PDF, DOCX, TEITOK, PAGE XML and ALTO stay upstream, as sources;
  nothing downstream takes raw HTML or XML as a prompt. The record route and the TEITOK/ALTO route emit
  the same cue vocabulary and the same page labels. What the model said (`enrichment`) is never read back
  into the Markdown, and entities, keywords and enrichment are checked on the record, not in the Markdown.
  [`tests/test_md_stress.py`](tests/test_md_stress.py) holds both routes to this at every detail profile:
  each page and line once and in order, the cues each profile promises, the grouped structure, no
  `enrichment`, and the same output on every run and hash seed.
* **Who uses it.** The whole-document runs of the keyword clients read `.md` files
  ([atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract)'s `openrouter_client.py` and
  `ollama_client.py`); its service works line by line on the record and does not need it. How the renderer
  is shared with that repository (vendored with a pin) is still to be settled (atrium-project#72 B).
* **One source.** The rendering is a pure function of the record, so the Markdown cannot differ
  from the JSON.

### Detail profiles (`--detail`)

Three cue profiles, the values of the record's `regenerable.markdown.detail`
(hub [#70](https://github.com/ufal/atrium-project/issues/70), item 1):
* the **text lines are the same in all three**; a lighter profile only drops cues;
* the cue sets nest (minimal ⊂ standard ⊂ full);
* `full` is the default.

| Cue                                                                                                                        | `full`   | `standard`                                                                    | `minimal` |
|----------------------------------------------------------------------------------------------------------------------------|----------|-------------------------------------------------------------------------------|-----------|
| `# doc`, `## Page`, `PAGE_BREAK`, `NEEDS_OCR`, headings, footnotes, GFM tables, `HEADER_*`/`FOOTER_*`, figure placeholders | ✓        | ✓                                                                             | ✓         |
| `OCR` provenance, `DOC_META`, whole-line `**bold**`/`*italic*`                                                             | ✓        | ✓                                                                             | –         |
| `BBOX`                                                                                                                     | per line | one per block (a `group_id` run; a table keeps its own; ungrouped lines none) | –         |
| `LAYOUT_MARGIN` (canvas minus the body-line union; record route, pages with a canvas and boxes)                            | ✓        | –                                                                             | –         |

What each profile costs, from `python3 scripts/detail_budget.py` (characters; ≈tokens = chars / 4):

| Input                                                      | full            | standard       | minimal        |
|------------------------------------------------------------|-----------------|----------------|----------------|
| hub E2E scan `CTX192100040.alto.xml` (xml_to_md layout)    | 32 234 (≈8 058) | 21 102 (−35 %) | 15 535 (−52 %) |
| hub E2E scan `CTX192601143.alto.xml` (xml_to_md layout)    | 26 614 (≈6 653) | 12 555 (−53 %) | 9 536 (−64 %)  |
| `CTX000000002.teitok.xml` writer sample (xml_to_md layout) | 785             | 529 (−33 %)    | 385 (−51 %)    |
| `two_column.pdf` fixture (JSON route)                      | 1 293           | 907 (−30 %)    | 625 (−52 %)    |
| `enrichable.pdf` fixture (JSON route)                      | 596             | 354 (−41 %)    | 282 (−53 %)    |
| `rich.docx` fixture (JSON route; DOCX has no boxes)        | 632             | 632 (0 %)      | 628 (−1 %)     |

* **The recipe.** The record's `regenerable.markdown` recipe (`json_to_md@1.1`) is written only when
  the record can actually be rendered.
* **Unemitted cues.** Cues in the catalogue that no profile emits (`INDENT`, `LAYOUT_COLUMN`,
  `FONT`, `STYLE`, `WATERMARK`, strike, underline, alignment) are listed, each with its reason, in
  [`layout_md.RESERVED`](api_util/layout_md.py) 📎.
* **The deprecated direct converters.** `api_util/pdf_to_md.py` and `docx_to_md.py` are reached
  only through `doc_to_visual_md.py --legacy` / `--ocr`, for A/B checks, and render `full` only.

**TEITOK input.** `xml_to_md.py` reads a TEITOK (`*.teitok.xml`) or raw ALTO document.
* The TEITOK files come from atrium-nlp-enrich (format 2, `teitok-2`) or from flexiconv.
* The line-level reader [`api_util/teitok_read.py`](api_util/teitok_read.py) 📎 is vendored
  byte-identical from atrium-nlp-enrich, together with `bbox_scale.py` and `flexiconv_convert.py`,
  and pinned by `tests/test_vendored_teitok_parity.py`.
* The format itself is described in nlp-enrich's README, section
  [TEITOK XML — Unified Output Format](https://github.com/ufal/atrium-nlp-enrich#teitok-xml--unified-output-format).

## Configuration

Every setting is an environment variable. [`.env.example`](.env.example) 📎 is the complete ledger
(`tests/test_env_contract.py` keeps it complete).

| Variable                                                  | Default                 | Meaning                                                                       |
|-----------------------------------------------------------|-------------------------|-------------------------------------------------------------------------------|
| `MAX_UPLOAD_MB`, `MAX_PAGES`, `MAX_CONCURRENT_JOBS`       | 50, 2000, 2             | service limits                                                                |
| `OCR_LAYER_DOCUMENT_SHARE`                                | 0.5                     | at or above this share of prior-OCR pages a PDF is refused (`ocr_text_layer`) |
| `ROUTE_TRASH_SHARE`                                       | 0.5                     | `/describe`: a decoding page with more `Trash` lines than this goes to `ocr`  |
| `LIBREOFFICE_BIN`, `LIBREOFFICE_TIMEOUT_S`                | `soffice`, 120 s        | DOC/XLS conversion                                                            |
| `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`          | unset                   | `/describe`'s stages; unset → `not_configured`                                |
| `STAGE_TIMEOUT_S`                                         | 120 s                   | per stage call; over it the stage is `unavailable`                            |
| `PAGE_CLASSIFICATION_VERSION`, `PAGE_CLASSIFICATION_TOPN` | `all` (the ensemble), 3 | the model version and top-N `/describe` asks page-classification for          |
| `DOCLING_ARTIFACTS_PATH`                                  | unset                   | Docling's model directory (`--engine docling`)                                |

The full table of limits, with what happens over each one, is in
[service/README.md § Limits](service/README.md#limits). `GET /info` reports the value in force.

## 🐳 Docker

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

* **Image contents.** [`.github/production-image.json`](.github/production-image.json) 📎 declares
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

## Paradata and provenance

* **The service.** Every `/reformat` and `/describe` response carries `paradata`: the run's RO-Crate
  `CreateAction` (`atrium_rocrate`), program `digital-convert`. It names the run id, what the call
  read (the file, with its digest, and the seed record when one was sent) and the record blocks it
  wrote.
* **Stage paradata.** `/describe` also returns each stage's own paradata in `stages[]`.
* **The command line.** `--paradata-dir DIR` writes `YYMMDD-HHmmss_digital-convert.json`
  ([`atrium_paradata.py`](atrium_paradata.py) 📎).

**Licence of a record.** `provenance.license` is the most restrictive licence among the components
the run actually used, as declared in [`para_config.txt`](para_config.txt) 📎:
* **The light engine and the shared reader** are MIT, BSD-3-Clause or Apache-2.0, so a record
  says **MIT**.
* **The Docling engine** adds TableFormer's CDLA-Permissive-2.0, which the shared licence table
  ranks with MIT, so a Docling record also says MIT.
* **LibreOffice** (MPL-2.0) is declared but **never logged**. It converts the container format of
  a text it does not author, and AMČR accepted it on exactly that condition (#4). The conversion is
  reported beside the record instead (`reader.conversion` in the response).
* **PyMuPDF** (AGPL-3.0) is deliberately not used; see `requirements_digital.txt`.

### Document record schema 📑

The record follows [atrium_document.schema.json](atrium_document.schema.json) 📎, schema version
**`1.0`**, frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`).
* [tests/test_schema_freeze.py](tests/test_schema_freeze.py) 📎 checks this repository's schema
  against the frozen copy beside it,
  [atrium_document.schema.doc-schema-v1.json](atrium_document.schema.doc-schema-v1.json) 📎.
* The schema files, `atrium_document.py` and the other shared modules (`atrium_service.py`,
  `atrium_openapi.py`, `atrium_limits.py`, `atrium_vocab.py`, `atrium_rocrate.py`, …) are vendored
  from the hub. The `para-drift` workflow keeps them byte-identical, so they are never edited
  here; the hub's `scripts/revendor_shared.sh` refreshes them.

What may change after the freeze is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).

## 📐 Document Understanding benchmark (research tools)

The evaluation harness of hub issue [#22](https://github.com/ufal/atrium-project/issues/22) (see
[#3](https://github.com/ufal/atrium-digital-convert/issues/3)) compares out-of-the-box VLM/OCR models
with the legacy ABBYY/ALTO pipeline, scored per quality tier on an in-domain gold set.
* **Scripts.** `sample_stratify.py`, `bench_compare.py` and `eval_metrics.py` are torch-free.
* **Where they run.** They are research tools, run from a checkout and left out of every image
  (`.dockerignore`, atrium-project#72).

```bash
python sample_stratify.py --page-stats samples_page_stats.csv --n 200 --output docu_sample_manifest.csv
python bench_compare.py --manifest docu_sample_manifest.csv --gold data/gold \
    --pred alto=../atrium-ocr-postprocess/data_samples/PAGE_TXT \
           layoutreader=../atrium-ocr-postprocess/data_samples/PAGE_TXT_LR \
    --split test --output-dir bench_results
```

* **Sampling.** `sample_stratify.py` buckets pages by OCR quality (`clean` / `degraded` / `hard` /
  `text_poor`) and writes an annotation manifest with a deterministic 80/10/10 split.
* **Gold data.** One UTF-8 transcription per manifest page
  (`gold/<doc>/<doc>-<page>.txt`, optionally `.entities.tsv`).
* **Comparison.** `bench_compare.py` writes `page_scores.csv`, `aggregate_scores.csv` and
  `report.md` (CER, WER, NED, optional entity P/R/F1). The output is byte-identical across
  reruns.
* **Config.** Both scripts also read an INI config (`--config config_docu.txt`, sections
  `[STRATIFY]` and `[BENCHMARK]`).

## Development

```bash
pip install -r requirements.txt -r requirements_digital.txt -r requirements-test.txt
pytest -q                                                    # the whole suite (no LibreOffice needed: DOC/XLS use a fake soffice)
ruff check . && ruff format --check .
python tests/fixtures/digital/make_fixtures.py --verify      # the generated PDF/DOCX fixtures are current
python atrium_openapi.py export --app service.api:app --out service/openapi.json   # after an API change
python atrium_openapi.py check  --app service.api:app --spec service/openapi.json
python ../atrium-project/tools/ci/image_closure.py --repo-root . --worktree        # the image's file list
```

How to contribute, the release procedure and the history are in
[CONTRIBUTING.md](CONTRIBUTING.md) 📎. The design record of every issue is in
[`agent_dev_logs/`](agent_dev_logs) (digests and plans).

---

## Acknowledgements 🙏

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^1] 🔗

- **Developed by** UFAL [^5] 👥
- **Funded by** ATRIUM [^4] 💰
- **Shared by** ATRIUM [^4] & UFAL [^5] 🔗
- **Pipeline partners**: AMČR (the Archaeological Map of the Czech Republic) and the ATRIUM hub [^2]
- **Frameworks used**:
  - **pdfplumber** / pdfminer.six and **pypdfium2** (PDF text, geometry, page labels)
  - **python-docx** and **lxml** (DOCX, and the ODF/OOXML parts in the shared reader)
  - **Docling** [^6] (the opt-in layout and table engine)
  - **LibreOffice** [^7] (legacy DOC/XLS conversion, headless)
  - **FastAPI** + **uvicorn** (the service)
  - UFAL **flexiconv** [^8] (optional TEITOK conversion adapter)

**©️ 2026 UFAL & ATRIUM**

[^1]: https://github.com/ufal/atrium-digital-convert
[^2]: https://github.com/ufal/atrium-project
[^4]: https://atrium-research.eu/
[^5]: https://ufal.mff.cuni.cz/
[^6]: https://github.com/docling-project/docling
[^7]: https://www.libreoffice.org/
[^8]: https://github.com/ufal/flexiconv
