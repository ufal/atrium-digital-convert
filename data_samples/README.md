# AMČR samples: born-digital conversion

The 15 PDFs, converted on 2026-10-09 by atrium-digital-convert ([issue #5](https://github.com/ufal/atrium-digital-convert/issues/5)).

| Folder      | File per document     | What it is                                                                                                   |
|-------------|-----------------------|--------------------------------------------------------------------------------------------------------------|
| `records/`  | `<doc>.document.json` | The converted document: text lines with their positions on the page, tables, and a verdict for every page.  |
| `reports/`  | `<doc>.describe.json` | Per page, where it goes next: `route` is `nlp` (the PDF's own text is used) or `ocr` (the page needs OCR), with `route_reason`. `summary` lists the pages that need OCR. |
| `markdown/` | `<doc>.md`            | The same text, readable. A page without usable text carries a `<!-- NEEDS_OCR: … -->` comment with the reason; a text editor shows it, a rendered view hides it. |

**Page verdicts** (`pages[].text_layer` in the records):
- `digital`: the PDF's own text. Only these pages skip OCR.
- `none`: no text, such as a scan or a drawing.
- `ocr`: text from an earlier OCR run.
- `garbled`: text that does not decode. No page here has this verdict.

**Coming with the next run:** page types and line-quality scores.
