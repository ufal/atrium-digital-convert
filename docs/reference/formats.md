# Formats and what the docproc holds

Every input format, how it is recognised and read, and which layout cues reach which docproc field. Since
[atrium-project#84](https://github.com/ufal/atrium-project/issues/84) the output is docproc
([schemas/docproc/](../../schemas/docproc/README.md)), not the record; the development dict behind the Markdown
view keeps the older record-shaped names (`lines[].group_id`, `style`, `tables[]`), listed last in the table below.

## Formats

The type is decided by the file's content. The extension only names the file, so a mislabelled
upload is read as what it is; for plain text it chooses the dialect (`.csv`, `.tsv`, `.json`).
Each type takes one of two workflows ([atrium-digital-convert#9](https://github.com/ufal/atrium-digital-convert/issues/9)):

* **visual**: a document with pages. Every page gets a text-layer verdict, and a page whose text cannot be used
  is `needsAtr`, so the whole document goes to the ATR with that page forced and the page goes to
  page-classification. An office document keeps its native text and takes its pages from a LibreOffice rendering
  (see below).
* **textual**: text with nothing to render, extracted as it is. It has no page geometry and never goes to the
  ATR, only straight to scoring.

| Input | Workflow | Recognised by                        | Text from                                                                   | `sourceFormat` | Pages                                                | Geometry                             |
|-------|----------|--------------------------------------|-----------------------------------------------------------------------------|----------------|------------------------------------------------------|--------------------------------------|
| PDF   | visual   | `%PDF-`                              | `digital_pdf.py` (pdfplumber, pypdfium2), or Docling (`--engine docling`)   | `pdf-digital`  | PDF pages, named by `/PageLabels` (`i`, `ii`, `A-1`) | exact boxes, points, top-left origin |
| DOCX  | visual   | ZIP with `word/document.xml`         | `digital_docx.py` (python-docx, lxml)                                       | `docx`         | the rendering's pages                                | boxes measured on the rendering      |
| DOC   | visual   | OLE2 with a `WordDocument` stream    | headless LibreOffice → DOCX → `digital_docx.py`                             | `doc`          | the rendering's pages (of the DOCX)                  | boxes measured on the rendering      |
| ODT   | visual   | ZIP, ODF `mimetype`                  | `text_formats.py`, the shared reader (vendored from atrium-ocr-postprocess) | `odt`          | the rendering's pages                                | boxes measured on the rendering      |
| RTF   | visual   | `{\rtf`                              | `text_formats.py`                                                           | `rtf`          | the rendering's pages                                | boxes measured on the rendering      |
| ODS   | textual  | ZIP, ODF `mimetype`                  | `text_formats.py`                                                           | `ods`          | one per sheet, labelled by the sheet name            | none                                 |
| XLSX  | textual  | ZIP with `xl/workbook.xml`           | `text_formats.py`                                                           | `xlsx`         | one per sheet, labelled by the sheet name            | none                                 |
| XLS   | textual  | OLE2 with a `Workbook`/`Book` stream | headless LibreOffice → XLSX → `text_formats.py`                             | `xls`          | one per sheet                                        | none                                 |
| TXT   | textual  | text, not one of the below           | `digital_text.py`, with the shared reader's decoding                        | `txt`          | form-feed sections                                   | none                                 |
| CSV   | textual  | text named `.csv`                    | `digital_text.py` (the standard `csv` module)                               | `csv`          | one                                                  | none                                 |
| TSV   | textual  | text named `.tsv`/`.tab`             | `digital_text.py`                                                           | `tsv`          | one                                                  | none                                 |
| JSON  | textual  | text named `.json`                   | `digital_text.py` (the standard `json` module)                              | `json`         | one                                                  | none                                 |

* **`sourceFormat` of a PDF** is `pdf-scanned` when no page's text layer is used (every page needs the ATR),
  else `pdf-digital`, a mixed PDF too. `txt`, `csv`, `tsv` and `json` extend ask 13's list in the proposal schema.
* **Refused with a reason:**
  * images: `unsupported`, HTTP 415. Images skip digital-convert and go to the ATR (H13);
  * PPTX, ODP, EPUB, Markdown, JSON Lines, HTML, XML (ALTO, PAGE, hOCR among them), compressed files and any other
    type: `unsupported`, HTTP 415, which lists the accepted types;
  * an encrypted file: `encrypted`;
  * a broken one: `corrupt`. This includes a CSV with a quoting error, a JSON that is not one document, and a
    reading the shared reader would only make by skipping content (D13);
  * a ZIP bomb: `zip_limits_exceeded`.
* **Office documents: native text on rendered pages** (`office_render.py`, `render_place.py`).
  * The text is the native reader's. It reads the document as written: headings, notes, tables, tracked changes
    accepted.
  * The pages are LibreOffice's rendering of the document, read by the light PDF engine. The rendering is made from
    a copy with the tracked changes accepted (DOCX and ODT; a DOC from its converted DOCX), because LibreOffice
    draws deletions.
  * Each native line is matched word by word to the rendering, in reading order, and cut where the rendering breaks
    it. A line of the docproc is therefore a visual line of the native text, with the box of its rendered words
    (`measured`).
  * A native line the rendering does not show, such as hidden text or a text box with no size, keeps its text on
    the page of the line before it, with `bboxPrecision: absent`. It is never dropped.
  * Each page then gets the PDF rules. Text over a picture (a scan pasted into a report) is `needsAtr`; a page with
    text and a photo is not.
  * A page whose rendered body words the native text covers less than `RENDER_TEXT_COVERAGE` (0.8; 3 words at
    least) shows text the reading does not have, such as a drawing, a chart or an embedded object. It is `needsAtr`.
  * The rendered PDF is what the visual steps read for a page that needs the ATR: page-classification takes PDFs
    only, and the ATR's page numbers and boxes must be the docproc's (H2, D28). It comes back as `rendered_pdf` from
    `/reformat` (`--rendered-out` on the command line).
  * The PDF is not stored. The paradata name it by SHA-512, `stored: false` (D23): the PDF this run handed over. A
    rerun lays the pages out the same, so the docproc does not change; the PDF's own bytes may differ.
* **The textual workflow: extracted as it is.**
  * A TXT keeps its physical lines; a blank line ends a paragraph (one block).
  * A CSV or TSV row is one line of its non-empty cells joined by a tab, the header row and every column included
    (the shared reader would keep only a `text` column).
  * A JSON gives every string value, in document order, one block each. Keys, numbers, booleans and null are
    structure.
  * The decoding is the shared reader's: BOM, UTF-8, then the archive's code pages (cp1250 first). Undecodable bytes
    become U+FFFD and stay for scoring to judge.
  * A line longer than `MAX_LINE_CHARS` (1000) is wrapped at a word boundary, as ocr-postprocess's own text path
    does. Each wrap is a `split` in the paradata `limits` and in `limits_applied`.
  * Decode flags (`Garbage`) stay on the lines, but a textual page never needs the ATR.
* **No geometry is fabricated.** The textual workflow's docproc pages carry no size and their blocks no `bbox` (the
  proposal allows it for flow documents, ask 13).
* **Spreadsheets.** A row's text cells become one line, joined by a tab. Numbers, dates and
  formulas are not text and are dropped by the reader. Each sheet is one block, so a consumer
  keeps a sheet together. A repeated cell or row past `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP` fails the
  conversion rather than dropping content (D13). Spreadsheet cell grids are a follow-up.
* **The shared reader.** ODT, ODS, XLSX and RTF go through atrium-ocr-postprocess's reader (the hub's
  "one document reader", atrium-project#72), not a second fork.
  `tests/test_vendored_reader_parity.py` pins the copy by its SHA-256. The para-drift workflow
  compares it with ocr-postprocess's `test` head, and the hub's `scripts/revendor_shared.sh`
  refreshes it.
* **LibreOffice** (conversion and rendering):
  * LibreOffice runs headless, with a private, throw-away profile and a time limit
    (`LIBREOFFICE_TIMEOUT_S`). `LIBREOFFICE_BIN` names the binary (default: `soffice` or
    `libreoffice` on `PATH`).
  * The paradata name the original file by its SHA-512, so the run describes the file that
    was uploaded, not the converted or rendered one. `sourceFormat` is the original's.
  * LibreOffice is listed among the run's components with its version, as a fact.
  * Without LibreOffice an office document or an XLS fails with `dependency_missing` (HTTP 501, CLI exit 2). PDF,
    ODS, XLSX and the text-only inputs still work.

## What the docproc holds

| Layout cue                | PDF (light engine)                                     | DOCX, DOC                                                | ODT / RTF             | ODS / XLSX / XLS | TXT / CSV / TSV / JSON                     | docproc field (development dict)                                         |
|---------------------------|--------------------------------------------------------|----------------------------------------------------------|-----------------------|------------------|--------------------------------------------|--------------------------------------------------------------------------|
| Pages                     | PDF pages, named by `/PageLabels`                      | the rendering's pages                                    | the rendering's pages | sheets           | form-feed sections (TXT); one              | `pages[].pageNumber`, routing (`pages[]`: `page`, `page_index`)          |
| Text blocks / paragraphs  | vertical gaps, per column                              | one per paragraph and page, one per table cell           | paragraph, table cell | sheet            | paragraph (TXT), file, string value (JSON) | `blocks[]` with `lines[]` (`lines[].group_id`)                           |
| Lines                     | visual lines                                           | visual lines of the native text                          | the same              | rows             | physical lines, rows; wrapped at 1000      | `blocks[].lines[]`                                                       |
| Reading order             | words, columns (column-major), headers first           | document order; text boxes after their anchor            | document order        | row order        | file order                                 | `readingOrder` (`lines[]` order)                                         |
| Headings                  | font size against the body size; bold body-size titles | outline level, then style name (`Heading N`, `Nadpis N`) | —                     | —                | —                                          | `type: heading`, `level` (`style.heading_level`)                         |
| Bold / italic             | font name                                              | run → character style → paragraph style                  | —                     | —                | —                                          | — (`style.bold` / `.italic`)                                             |
| Running header / footer   | margin lines repeated across pages, page numbers       | each section's header and footer                         | —                     | —                | —                                          | `type: header` / `footer` (`style.region`)                               |
| Footnotes                 | — (Docling engine: yes)                                | footnotes and endnotes                                   | —                     | —                | —                                          | `type: footnote` (`style.region = footnote`)                             |
| Tables                    | ruled tables (Docling engine: any)                     | tables, merged cells, one per page they reach            | cells as lines        | — (follow-up)    | — (rows as lines)                          | `type: table`, `cellGrid` (`tables[]` + `cells[].group_id`)              |
| Bounding boxes, page size | exact, points, top-left origin                         | measured on the rendering, points                        | the same              | none             | none                                       | block and line `bbox`, `bboxPrecision`, page `width`/`height` (`canvas`) |
| Untrustworthy text        | mojibake, unmapped glyphs, no text layer, prior OCR    | the same, and text the rendering shows that it lacks     | the same              | decode flag only | decode flag only                           | line `flags: [Garbage]`, page `textLayer`, `needsAtr` (`needs_ocr`)      |

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
