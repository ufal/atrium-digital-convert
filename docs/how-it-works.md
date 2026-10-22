# How digital-convert works

What the converter decides about each page, and how a page reaches OCR. The formats and the record's fields are
in [reference/formats.md](reference/formats.md); the service's fields and errors in
[../service/README.md](../service/README.md).

## The record and its OCR hand-off

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
  It is refused as `ocr_text_layer` and belongs to the OCR route as a whole. A lower setting sends
  any document with a prior OCR layer to that route whole.
* **Which pages to re-acquire.** `needs_ocr` marks the pages whose text the converter cannot vouch
  for. `/describe` makes the whole per-page decision: among those pages, page types send drawings
  and photographs to `none` and handwriting to `htr`, and two rules send decoding pages to `ocr`
  (lost letters, and the quality model). Its `summary.reacquire_pages` (routes `ocr` and `htr`) is the page list for
  ATR/HTR; with `stages=none` it calls no other service.
* **The legacy DOC and XLS** were accepted by AMČR on
  [#4](https://github.com/ufal/atrium-digital-convert/issues/4) (2026-09-26). LibreOffice
  converts the file to DOCX/XLSX first and is not counted in the record's licence; see
  [Paradata and provenance](reference/paradata.md#paradata-and-provenance).

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
               "regions": {"page_header": 0, "page_footer": 0, "footnote": 0},
               "words": 16, "damaged_words": 0},
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
  * `garbled`: a layer that does not decode: mojibake, or lines that are over 15 % glyphs without a
    Unicode mapping (shown as U+FFFD; a font without /ToUnicode);
  * `ocr`: a prior OCR run;
  * `none`: no text layer: a scan, text drawn as curves, a page that draws nothing; also a page
    whose only text is its running header or footer over images covering at least
    `FURNITURE_ONLY_IMAGE_COVERAGE` (0.85) of it, a scan the PDF's producer numbered. Its page
    number stays in `lines[]`; the page carries no quality score;
  * `blank`: an empty page of a format without page images.
* **Page type** (`category`): page-classification's label, confidence and top-N. It is asked about
  the `needs_ocr` pages and the pages whose only text is a running header or footer over an image
  by default, or about every page with `classify_pages=all`. PDF only.
* **Readability** (`quality`):
  * with ocr-postprocess configured: its line-quality model (Clear / Noisy / Trash per line, the
    page band, the language);
  * without it: the converter's decode check only. Its `score`/`band` measure how the characters
    decode, not how the text reads.
* **Route** (`route`), deterministic, with `route_reason`:

  | The page                                           | Route                                                                                              |
  |----------------------------------------------------|----------------------------------------------------------------------------------------------------|
  | its text layer decodes                             | `nlp`, unless it loses letters or its prose reads as `Trash` (below) → `ocr`                       |
  | its only text is a running header or footer        | by its page type when that type carries text (`DRAW_L`, `PHOTO_L`, `TEXT_*`, `LINE_*`), else `nlp` |
  | no usable text, handwritten (`TEXT_HW`, `LINE_HW`) | `htr`                                                                                              |
  | no usable text, printed, typed or tabular          | `ocr`                                                                                              |
  | no usable text, graphical only (`DRAW`, `PHOTO`)   | `none`                                                                                             |
  | no usable text, page type unknown                  | `ocr`                                                                                              |
  | blank                                              | `none`                                                                                             |

  The categories are read through the page-category facets of `atrium_vocab.COLLECTIONS`, so a
  category added to the vocabulary registry is routed without a code change.

  A page whose text layer decodes still goes to `ocr` in two cases:
  * **lost letters:** at least `ROUTE_DAMAGED_WORD_SHARE` (0.05) of the words of its body text
    (outside the running header and footer) lose a letter to a glyph without a Unicode mapping
    (U+FFFD). The converter sees this itself, without any stage;
  * **the quality model:** ocr-postprocess calls more than `ROUTE_TRASH_SHARE` (0.5) of its *prose*
    lines `Trash`, on a page with at least 5 of them. A prose line is outside a table, has at least
    20 characters, is mostly letters and is not a table-of-contents line. Table cells, codes and
    coordinates read as `Trash` to a model of running text, but on a born-digital page they are exact.
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
