# AMČR samples: born-digital conversion

The 15 PDFs, converted on 2026-10-09 by atrium-digital-convert ([issue #5](https://github.com/ufal/atrium-digital-convert/issues/5)).

| Folder      | File per document     | What it is                                                                                                                                                               |
|-------------|-----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `records/`  | `<doc>.document.json` | The converted document: text lines with their positions on the page, tables, and a verdict for every page.                                                               |
| `reports/`  | `<doc>.describe.json` | Per page, where it goes next: `route` is `nlp` (the PDF's own text is used) or `ocr` (the page needs OCR), with `route_reason`. `summary` lists the pages that need OCR. |
| `markdown/` | `<doc>.md`            | The same text, readable. A page without usable text carries a `<!-- NEEDS_OCR: … -->` comment with the reason; a text editor shows it, a rendered view hides it.         |

**Page verdicts** (`pages[].text_layer` in the records):
- `digital`: the PDF's own text. Only these pages skip OCR.
- `none`: no text, such as a scan or a drawing.
- `ocr`: text from an earlier OCR run.
- `garbled`: text that does not decode. No page here has this verdict, but some should (below).

**Known defects of this draft** (converted with `v1.1.1-beta`; fixed in the code since, and gone in the next run):
- **Words split at Czech letters.** Words are split around ě, č, ř, ů, ň, ť and ď ("Ji č ín", "Oprávn ě ná"): about 24,000 times, mostly in CTX201400303, CTX201703695, CTX201303221, CTX202100048 and CTX201006925. The PDFs set those letters in a second font, and the converter split words at each font change.
- **`(cid:N)` in the text.** These are letters the PDF gives no character for: CTX202000362 (78 pages, every ř, ě, č and ů), CTX202100048 (pages 33–39, almost all of the text) and CTX202200566 (page 16, all of it). Such pages are marked `digital` here.
  - In the next run each such letter is U+FFFD (`�`).
  - A page whose lines are mostly such letters is `garbled` and needs OCR (18 pages).
- **A page number over a scan.** CTX201703695 pages 67 and 68 are scans whose only text is a page number. Here they are `digital`; next they are `none` and need OCR.

**Coming with the next run:** page types and line-quality scores.
