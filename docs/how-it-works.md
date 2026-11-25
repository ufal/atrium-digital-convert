# How digital-convert works

What the converter decides about each page, and how a page reaches the ATR, AIS CR's text-recognition service. The
formats and the docproc's fields are in [reference/formats.md](reference/formats.md); the service's fields and errors
in [../service/README.md](../service/README.md).

## docproc and the route to the record

```
AMČR upload ──► seed (doc_id, source.sha512, filename, media_type)
                   │
                   ▼
   born-digital? ──yes──► digital-convert  /reformat ──► docproc ──► ocr-postprocess builds the record
                   │        visual: PDF;                 │                ──► nlp-enrich, keyword-extract, …
                   │        DOCX/DOC/ODT/RTF rendered    │
                   │        (never ATR)                  ├─ PDF pages routed `atr` ──► the ATR (whole document, those
                   │        textual: sheets, TXT,        │                              pages forced; its docproc stored)
                   │        CSV, TSV, JSON (never ATR)   │
                   no (an image)                         └─ pages with `graphics` ──► page-classification (those pages
                   ▼                                                                   of the PDF or the rendered PDF)
                  ATR
```

**Two workflows:**

* **Visual.** A PDF is read on its own pages. An office document (DOCX, DOC, ODT, RTF) keeps its native text and
  takes its pages from a LibreOffice rendering: each native line is matched word by word to the rendered PDF, cut
  where the rendering breaks it and given the box of its rendered words (`api_util/render_place.py`). Every page
  then gets the same decode verdicts as a PDF page, but an office document never goes to the ATR: its text is
  what its XML holds, read in full. A pasted scan, a page that is only a picture, has no text of its own and is
  routed `empty`, with its picture counted in `graphics`. The rendered PDF is the page source of page-classification
  for an office document, so its page numbers and boxes are the docproc's. It is not stored, and the paradata name
  it by SHA-512.
* **Textual.** A spreadsheet or a text-only file (TXT, CSV, TSV, JSON) has no pages to render. Its text is
  extracted as it is, it carries no geometry, and it goes straight to scoring. Its pages are never routed `atr`.
  A sheet keeps every non-empty cell, numbers and dates in the form the sheet displays them: an ODS stores
  that text itself, and an XLSX or XLS is displayed by LibreOffice in cs-CZ and read as the ODS it makes.

digital-convert writes the layout as docproc and no record; ocr-postprocess builds the record from it.

* **The AMČR seed.** With a seed (`document_json`), the docproc is named by the seed's `doc_id`. The seed's
  `source.sha512` is checked against the uploaded bytes before anything is parsed: a mismatch is
  `source_digest_mismatch` (HTTP 422, CLI exit 3), so nothing is produced for the wrong file. The seed is read,
  never written, and the run's paradata name it as an input.
* **Pages the converter cannot vouch for** carry `needsAtr` with a reason (`needsAtrReason`), and every page in
  `pages[]` says why in `textLayer` (`digital`, `garbled`, `ocr`, `none`):
  * no text layer (a scan, text drawn as curves): `none`;
  * a text layer that does not decode (broken font encodings, replacement characters): `garbled`;
  * a prior OCR run: `ocr`.
  Only a PDF page can need the ATR. Those pages are routed `atr` in `metadata.routing.pages[]`. An empty page is routed `empty` and is not in
  `pages[]`. A PDF page without text counts as empty only when its rendering shows nothing at all: no pixel of a
  50 dpi render darker than 250 of 255 (`BLANK_RENDER_DPI`, `BLANK_INK_LEVEL` in `api_util/digital_pdf.py`, named in
  the paradata as `blank_page`); any mark keeps it `none`, for the ATR. No document is refused for its pages. When a
  page needs the ATR, the ATR's docproc is the stored one, so this one is recorded as an intermediate
  (`stored: false`); ocr-postprocess keeps this docproc's exact lines on the other pages when it builds the record
  from both.
* **The legacy DOC and XLS** are read through LibreOffice: it converts a DOC to DOCX first, which is then rendered
  like any office document, and an XLS, like an XLSX, to ODS. LibreOffice is among the components a run lists, with
  its version, as a fact; see [Paradata and provenance](reference/paradata.md#paradata-and-provenance).

The per-page assessment of `/describe`, which goes further than `needsAtr`, is parked during the AMČR pilot:
[parked features](reference/parked.md).
