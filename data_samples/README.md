# AMČR samples: born-digital conversion

The 15 PDFs, converted on 2026-10-15 by atrium-digital-convert `993d33a`, the code of `v1.1.2-beta`
(cluster job 7108263, [issue #5](https://github.com/ufal/atrium-digital-convert/issues/5)), with
page-classification's five models and ocr-postprocess's line-quality model on one 24 GB GPU. The
PDFs themselves are not here.

| Folder                                    | File per document     | What it is                                                                                                                                     |
|-------------------------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| `records/light/`                          | `<doc>.document.json` | The converted document, light engine: text lines with their positions on the page, tables, and a verdict for every page.                       |
| `records/docling/`                        | `<doc>.document.json` | The same with `--engine docling` (layout and table models), for comparison.                                                                    |
| `markdown/full/`, `standard/`, `minimal/` | `<doc>.<detail>.md`   | The light record as Markdown, at three levels of detail. A page without usable text carries a `<!-- NEEDS_OCR: … -->` comment with the reason. |
| `reports/reformat/`                       | `<doc>.reformat.json` | The service's `/reformat` answer: the record and its Markdown.                                                                                 |
| `reports/describe/none/`                  | `<doc>.describe.json` | `/describe` without other services: per page, its text layer and where it goes next (`route`, `route_reason`).                                 |
| `reports/describe/stages/`                | `<doc>.describe.json` | `/describe` with page-classification (on the pages without usable body text) and ocr-postprocess's line-quality model (on the text).           |
| `reports/describe/all/`                   | `<doc>.describe.json` | `/describe` with page-classification on every page: the page type of each one.                                                                 |

**Page verdicts** (`pages[].text_layer` in the records):
- `digital`: the PDF's own text. Only these pages skip OCR.
- `none`: no text, such as a scan or a drawing; also a scan whose only text is the page number
  Acrobat added (2 pages here).
- `ocr`: text from an earlier OCR run (two documents).
- `garbled`: text that does not decode, here letters the PDF gives no character for (18 pages).

**Routes** (`pages[].route` in the reports): `nlp` (use the text), `ocr` (re-acquire the page),
`htr` (handwriting), `none` (a photograph or drawing: nothing to read); `summary.reacquire_pages`
lists a document's `ocr` and `htr` pages. Of the 2,514 pages, 1,981 have their own text; of the
other 533, page-classification calls 434 photographs or drawings and 99 text or tables
(`reports/describe/all/`). In `reports/describe/stages/` the routes are 1,946 `nlp`, 434 `none`
and 134 `ocr`. The 134 are:
- the 99;
- 29 pages of CTX202000362 that lose every ř, ě, č and ů (shown as `�`) in at least 5 % of their
  words. They stay `digital` in the records;
- 6 excavation plans of CTX202000362 (pp. 37, 41, 44, 45, 47, 49, `DRAW_L`) whose labels
  ("Objekt 22") and title block are part of the image, and whose only text is the running header.

**Quality** (`pages[].quality` in `reports/describe/stages/`): ocr-postprocess's model judges
running text. Its band says `Trash` for 626 `digital` pages, mostly tables and photo or drawing
captions, whose text is nevertheless exact; the route counts running text only and keeps all of
them `nlp`.

**What these files still get wrong:**
- **Line scores in `reports/describe/stages/`.** In 6 documents, ocr-postprocess could not score
  10 batches of lines (1,280 lines) in the GPU memory left to it, and gave them the worst score.
  The stage's paradata says so (`limits_applied`, `ppl_batch_lines`). 155 of these lines read
  `Trash` or `Noisy` instead of `Clear`, and one page's band changed. No route changed.
- **Bold section titles are not headings.** A title set in bold body-size type ("1. Lokalizace a
  okolnosti výzkumu") stays a paragraph in the light records and their Markdown: CTX201202683 has
  19 headings, its Docling record 192.
- **Letter-spaced captions** read with spaces inside words (CTX201900002), and doubled letters on
  some title pages ("R REPUBLLIKY").
