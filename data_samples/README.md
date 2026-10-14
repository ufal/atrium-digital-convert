# AMČR samples: born-digital conversion

The 15 PDFs, converted on 2026-10-14 by atrium-digital-convert (cluster job 7100515,
[issue #5](https://github.com/ufal/atrium-digital-convert/issues/5)). The PDFs themselves are not
here.

| Folder                                    | File per document     | What it is                                                                                                                                     |
|-------------------------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| `records/light/`                          | `<doc>.document.json` | The converted document, light engine: text lines with their positions on the page, tables, and a verdict for every page.                       |
| `records/docling/`                        | `<doc>.document.json` | The same with `--engine docling` (layout and table models), for comparison.                                                                    |
| `markdown/full/`, `standard/`, `minimal/` | `<doc>.<detail>.md`   | The light record as Markdown, at three levels of detail. A page without usable text carries a `<!-- NEEDS_OCR: … -->` comment with the reason. |
| `reports/reformat/`                       | `<doc>.reformat.json` | The service's `/reformat` answer: the record and its Markdown.                                                                                 |
| `reports/describe/none/`                  | `<doc>.describe.json` | `/describe` without other services: per page, its text layer and where it goes next (`route`, `route_reason`).                                 |
| `reports/describe/stages/`                | `<doc>.describe.json` | `/describe` with page-classification (on the pages without usable text) and ocr-postprocess's line-quality model (on the text).                |
| `reports/describe/all/`                   | `<doc>.describe.json` | `/describe` with page-classification on every page: the page type of each one.                                                                 |

**Page verdicts** (`pages[].text_layer` in the records):
- `digital`: the PDF's own text. Only these pages skip OCR.
- `none`: no text, such as a scan or a drawing; also a scan whose only text is the page number
  Acrobat added (2 pages here).
- `ocr`: text from an earlier OCR run (two documents).
- `garbled`: text that does not decode, here letters the PDF gives no character for (18 pages).

**Routes** (`pages[].route` in the reports): `nlp` (use the text), `ocr` (re-acquire the page),
`htr` (handwriting), `none` (a photograph or drawing: nothing to read); `summary.reacquire_pages`
lists a document's `ocr` and `htr` pages. Of the 2,514 pages, 1,981
use their own text; of the other 533, page-classification calls 434 photographs or drawings and 99
text or tables (`reports/describe/all/`).

**Known defects of these files** (fixed in the code since this run; a re-run replaces them):
- **The quality check moved good pages.** In `reports/describe/stages/`, 612 `digital` pages go to
  `ocr` because ocr-postprocess calls their table cells, codes, coordinates or table-of-contents
  lines `Trash`. Their text is exact; the rule now counts running text only and moves none of them.
- **Lost letters stay `nlp`.** CTX202000362 loses every ř, ě, č and ů (shown as `�`) on its
  `digital` pages. `/describe` now sends the 29 where at least 5 % of the words are damaged to
  `ocr`; the record keeps them `digital`.
- **Page types from 2 of 5 models** in `reports/describe/stages/` (a page-classification bug on its
  first start): 21 of the 533 routes there differ from `reports/describe/all/`, which used all 5.
- **Small capitals split** ("O BRÁZEK", "T ABULKA", "N EGATIV"), in both engines.
- **Vertical table headers read one letter per line** (CTX201703695: "K_číslo" as K / _ / č …).
- **Paragraphs marked as headings** in table-heavy reports (CTX201202683, CTX201400303), so their
  Markdown shows most paragraphs as `#####` lines (1,927 in CTX201202683).
