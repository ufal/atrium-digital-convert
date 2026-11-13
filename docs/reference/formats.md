# Formats and what the docproc holds

Every input format, how it is recognised and read, and which layout cues reach which docproc field. Since
[atrium-project#84](https://github.com/ufal/atrium-project/issues/84) the output is docproc
([schemas/docproc/](../../schemas/docproc/README.md)), not the record; the development dict behind the Markdown
view keeps the older record-shaped names (`lines[].group_id`, `style`, `tables[]`), listed last in the table below.

## Formats

The type is decided by the file's content. The extension only names the file, so a mislabelled
upload is read as what it is.

| Input | Recognised by                        | Reader                                                                      | `sourceFormat`      | Pages                                                  | Geometry                             |
|-------|--------------------------------------|-----------------------------------------------------------------------------|---------------------|--------------------------------------------------------|--------------------------------------|
| PDF   | `%PDF-`                              | `digital_pdf.py` (pdfplumber, pypdfium2), or Docling (`--engine docling`)   | `pdf-digital`       | PDF pages, named by `/PageLabels` (`i`, `ii`, `A-1`)   | exact boxes, points, top-left origin |
| DOCX  | ZIP with `word/document.xml`         | `digital_docx.py` (python-docx, lxml)                                       | `docx`              | explicit, section and Word's last rendered page breaks | none                                 |
| ODT   | ZIP, ODF `mimetype`                  | `text_formats.py`, the shared reader (vendored from atrium-ocr-postprocess) | `odt`               | explicit and soft page breaks                          | none                                 |
| ODS   | ZIP, ODF `mimetype`                  | `text_formats.py`                                                           | `ods`               | one per sheet, labelled by the sheet name              | none                                 |
| XLSX  | ZIP with `xl/workbook.xml`           | `text_formats.py`                                                           | `xlsx`              | one per sheet, labelled by the sheet name              | none                                 |
| RTF   | `{\rtf`                              | `text_formats.py`                                                           | `rtf`               | `\page` breaks                                         | none                                 |
| DOC   | OLE2 with a `WordDocument` stream    | headless LibreOffice → DOCX → `digital_docx.py`                             | `doc`               | as DOCX                                                | none                                 |
| XLS   | OLE2 with a `Workbook`/`Book` stream | headless LibreOffice → XLSX → `text_formats.py`                             | `xls`               | one per sheet                                          | none                                 |

* **`sourceFormat` of a PDF** is `pdf-scanned` when no page's text layer is used (every page needs the ATR),
  else `pdf-digital`, a mixed PDF too.
* **Refused with a reason:**
  * PPTX, ODP, EPUB, plain text, images and any other type: `unsupported`, HTTP 415, which lists the
    accepted types (H13: an image goes to the ATR);
  * an encrypted file: `encrypted`;
  * a broken one: `corrupt`;
  * a ZIP bomb: `zip_limits_exceeded`.
* **Only PDF has geometry.** The other formats have no page coordinates without rendering them, so
  their docproc pages carry no size and their blocks no `bbox` (the proposal allows it for flow documents, ask
  13): no box is fabricated.
* **Spreadsheets.** A row's text cells become one line, joined by a tab. Numbers, dates and
  formulas are not text and are dropped by the reader. Each sheet is one block, so a consumer
  keeps a sheet together. A repeated cell or row past `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP` fails the
  conversion rather than dropping content (D13). Spreadsheet cell grids are a follow-up.
* **The shared reader.** ODT, ODS, XLSX and RTF go through atrium-ocr-postprocess's reader (the hub's
  "one document reader", atrium-project#72), not a second fork.
  `tests/test_vendored_reader_parity.py` pins the copy by its SHA-256. The para-drift workflow
  compares it with ocr-postprocess's `test` head, and the hub's `scripts/revendor_shared.sh`
  refreshes it.
* **DOC/XLS conversion:**
  * LibreOffice runs headless, with a private, throw-away profile and a time limit
    (`LIBREOFFICE_TIMEOUT_S`). `LIBREOFFICE_BIN` names the binary (default: `soffice` or
    `libreoffice` on `PATH`).
  * The paradata name the original file by its SHA-512, so the run describes the DOC/XLS that
    was uploaded, not the converted file; `sourceFormat` is `doc`/`xls`.
  * Without LibreOffice a DOC/XLS fails with `dependency_missing` (HTTP 501, CLI exit 2); every
    other format still works.

## What the docproc holds

| Layout cue                | PDF (light engine)                                         | DOCX                                                     | ODT / RTF             | ODS / XLSX            | docproc field (development dict)                                          |
|---------------------------|------------------------------------------------------------|----------------------------------------------------------|-----------------------|-----------------------|---------------------------------------------------------------------------|
| Pages                     | PDF pages, named by `/PageLabels`                          | explicit, section and Word's last rendered page breaks   | page breaks           | sheets                | `pages[].pageNumber`, routing (`pages[]`: `page`, `page_index`)           |
| Text blocks / paragraphs  | vertical gaps, per column                                  | one per paragraph, one per table cell                    | paragraph, table cell | sheet                 | `blocks[]` with `lines[]` (`lines[].group_id`)                            |
| Reading order             | words, columns (column-major), headers first               | document order; text boxes after their anchor            | document order        | row order             | `readingOrder` (`lines[]` order)                                          |
| Headings                  | font size against the body size; bold body-size titles     | outline level, then style name (`Heading N`, `Nadpis N`) | —                     | —                     | `type: heading`, `level` (`style.heading_level`)                          |
| Bold / italic             | font name                                                  | run → character style → paragraph style                  | —                     | —                     | — (`style.bold` / `.italic`)                                              |
| Running header / footer   | margin lines repeated across pages, page numbers           | each section's header and footer                         | —                     | —                     | `type: header` / `footer` (`style.region`)                                |
| Footnotes                 | — (Docling engine: yes)                                    | footnotes and endnotes                                   | —                     | —                     | `type: footnote` (`style.region = footnote`)                              |
| Tables                    | ruled tables (Docling engine: any)                         | tables, merged cells                                     | cells as lines        | — (follow-up)         | `type: table`, `cellGrid` (`tables[]` + `cells[].group_id`)               |
| Bounding boxes, page size | exact, points, top-left origin                             | none                                                     | none                  | none                  | block and line `bbox`, `bboxPrecision`, page `width`/`height` (`canvas`)  |
| Untrustworthy text        | mojibake, unmapped glyphs, no text layer, prior OCR        | the same decode check                                    | the same decode check | the same decode check | line `flags: [Garbage]`, page `textLayer`, `needsAtr` (`needs_ocr`)       |

* **Engines.**
  * `--engine light` (default) uses `requirements_digital.txt`: permissive licences, no models, no
    network.
  * `--engine docling` is opt-in and PDF only (`requirements_digital_docling.txt`). Docling's layout
    and table models decide reading order, headings, furniture, footnotes and tables on complex
    pages, and the light engine's lines keep their exact geometry.
  * Docling needs its model weights: `docling-tools models download layout tableformer -o DIR` and
    `DOCLING_ARTIFACTS_PATH=DIR`. The Docker target `digital-docling` does this at build time; it is
    built locally, never published. OCR stays off in both engines.
* **Output gate.** A docproc is never written if it fails any of these:
  * the proposal schema (`schemas/docproc/document.atrium-proposal.schema.json`);
  * the seed digest check.
