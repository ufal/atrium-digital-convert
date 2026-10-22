# Formats and what the record holds

Every input format, how it is recognised and read, and which layout cues reach which record field.

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
| Headings                  | font size against the body size; bold body-size titles     | outline level, then style name (`Heading N`, `Nadpis N`) | —                     | —                     | `lines[].style.heading_level`                  |
| Bold / italic             | font name                                                  | run → character style → paragraph style                  | —                     | —                     | `lines[].style.bold` / `.italic`               |
| Running header / footer   | margin lines repeated across pages, page numbers           | each section's header and footer                         | —                     | —                     | `lines[].style.region`                         |
| Footnotes                 | — (Docling engine: yes)                                    | footnotes and endnotes                                   | —                     | —                     | `lines[].style.region = footnote`              |
| Tables                    | ruled tables (Docling engine: any)                         | tables, merged cells                                     | cells as lines        | — (follow-up)         | `tables[]` + `cells[].group_id`                |
| Bounding boxes, page size | exact, points, top-left origin                             | none                                                     | none                  | none                  | `lines[].bbox`, `pages[].canvas`               |
| Untrustworthy text        | mojibake, unmapped glyphs, no text layer, prior OCR        | the same decode check                                    | the same decode check | the same decode check | `lines[].categ = Garbage`, `pages[].needs_ocr` |

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
