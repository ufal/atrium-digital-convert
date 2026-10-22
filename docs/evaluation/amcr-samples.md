# Born-digital PDFs in practice: the AMČR samples

What 15 AMČR excavation reports showed about the collection, and what the converter does about it.

[#5](https://github.com/ufal/atrium-digital-convert/issues/5) ran 15 excavation reports from AMČR
(ARUP/ARUB, 2008–2022, 2.1–156 MB, 34–529 pages; 2,514 pages, 525 MB) through every stage on the
cluster, twice (2026-10-09 and 10-14). What they show about the collection, and what the converter
does about it:

* **Most are assembled documents.** Text written in Word, exported, then combined in Acrobat with
  scanned or drawn parts: plans, photographs, forms, older reports. 10 of the 15 are mixed, so
  routing is per page, never per document. Of the 2,514 pages:
  * 1,981 carry a usable text layer (`digital`);
  * 533 do not. page-classification calls 434 of them photographs or drawings (`route: none`,
    nothing to read) and 99 text or tables (`ocr`);
  * two documents carry a prior OCR layer on some pages (Acrobat Paper Capture, PDFTron), under
    `OCR_LAYER_DOCUMENT_SHARE`: those pages are `ocr` and the document is converted;
  * one has 23 pages that draw only vector paths (plans, or text drawn as curves): `none`.
* **What born-digital text does to words**, all fixed in the light engine (`digital_pdf.py`):
  * Acrobat Distiller sets the Czech letters outside WinAnsi (ě č ř ů ň ť ď) in a second subset of
    the font. Words were split at each of them ("Ji č ín"): nearly 24,000 letters, 484 after the
    fix (432 of them the vertical headers below, also fixed since).
  * Small capitals are a full-size capital and smaller capitals: "O BRÁZEK" (212 times in one
    report), "T ABULKA", "N EGATIV". They are one word now ("OBRÁZEK"); real spaces ("P VI") stay.
  * Table headers set vertically were read one letter per line (432 lines on 77 pages of one
    report). A run of characters on a 90° baseline is one line now, read in its direction.
  * Headings were judged against a body size that table cells set, so in table-heavy reports
    every paragraph was a heading (1,927 of 30,938 lines in one report; Docling finds 192). Only
    the body text of `digital` pages counts now.
* **Glyphs without a Unicode mapping.** A font without /ToUnicode gives pdfminer no character. Each
  such glyph is U+FFFD (it was the text `(cid:N)`); a line over 15 % of them is `Garbage` and its
  page `garbled` (18 pages). One report loses every ř, ě, č and ů this way on the rest of its pages:
  `/describe` sends a page to OCR when at least `ROUTE_DAMAGED_WORD_SHARE` (5 %) of the words of its
  body text are damaged (29 pages, a median of 19 % of their words). Docling recovers none of them.
  Since v1.2.2-beta the record flags these pages itself (`needs_ocr`, `garbled`, the damaged words in
  the reason), so a step that reads only the record hands them to OCR; `/describe` routes them to `ocr`.
* **A page number over a scan.** A scanned page that Acrobat numbered has a text layer, the number.
  It is `none` when its only text is furniture and its images cover at least
  `FURNITURE_ONLY_IMAGE_COVERAGE` of it (2 pages: a drawing and a photograph). 36 pages of another
  report carry a running header over figures covering 58–64 % of them, and stay `digital`.
  page-classification calls 30 of them photographs or drawings, and 6 excavation plans with a
  legend (`DRAW_L`) whose labels ("Objekt 22") and title block are part of the image: `/describe`
  asks about such pages and sends a type that carries text to OCR, so these 6 are re-acquired.
* **The quality model and born-digital text.** ocr-postprocess's line-quality model judges running
  text. On born-digital pages its `Trash` lines are table cells, codes, coordinates, photo IDs and
  table-of-contents leaders, whose text is exact: counted over every line, `ROUTE_TRASH_SHARE` sent
  612 of the 1,981 `digital` pages to OCR. It counts prose lines only now, and moves none of them.
* **Section titles in bold at the body size** were body text (one report had 19 headings where
  Docling finds 192). A bold body-size line that reads as a title is a heading now, one level below
  the size-based ones: 2–100 characters, no trailing comma, semicolon or hyphen, not a list item,
  at most two bold lines in a row, and not the same bold line on three or more pages. The rule
  stands aside in a document whose bold body-size text is over `BOLD_HEADING_MAX_SHARE` (25 %) of
  its body text, and 0 switches it off.
* **Known, not handled:** doubled glyphs on cover pages ("ARC CHEOLO GICKÝ"), letter-spaced captions
  and map labels, unruled tables read column by column.
* **Resources** (10-14, an A40 node; times differ between nodes, memory does not):
  * the light engine: 3–50 s per document, a peak of at most 0.7 GB on the command line and 0.8 GB
    in the service (783 MB for a 282-page report; 560 MB for the 156 MB one). Memory follows the
    text a document holds, not its size; budget about 1 GB plus the upload limit per
    `MAX_CONCURRENT_JOBS` slot. `MAX_UPLOAD_MB` is 250 by default (200 from #5 to 10-22);
  * Docling (`--engine docling`, on the GPU): 25–341 s and 2.2–7.4 GB per document;
  * `/describe` with both stages: up to 276 s (page-classification 135 s on 149 flagged pages,
    202 s on all 529 with `classify_pages=all`). `STAGE_TIMEOUT_S` is 360 s by default since then.
* **What the stages receive.** The compact record `/describe` sends a stage reached 14 MB, and
  page-classification also gets the whole PDF: its defaults (10 MB per part, 50 pages per call)
  need raising for these files (`service/README.md` § Limits).
* **For a model.** The Markdown runs up to about 290k tokens at `full` and 150k at `minimal` for a
  400–530-page report, so a model stage downstream has to chunk it.

`tools/samples_report.py` measures all of this on a run: a "Text defects" table, the quality model's
moves and what it judged (by kind of line), the pages whose only text is furniture, and the limits
the run suggests.
