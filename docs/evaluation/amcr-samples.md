# Born-digital PDFs in practice: the AMČR samples

**In short:** 15 AMČR excavation reports (2,514 pages) were run through the converter and its two stages, twice, in
October 2026. Most are assembled documents, so each page is routed on its own: 1,981 pages carry a usable text
layer and 533 do not. The runs exposed text defects, now fixed in the light engine, and a few layouts still read
badly. For a user: expect some pages to go to OCR, and plan memory for large uploads. It gives no accuracy figure.

What 15 AMČR excavation reports showed about the collection, and what the converter does about it.

Issue [#5](https://github.com/ufal/atrium-digital-convert/issues/5) ran 15 excavation reports from AMČR through
every stage on the cluster, twice (2026-10-09 and 2026-10-14). The reports come from ARÚP and ARÚB and date from
2008 to 2022. They range from 2.1 to 156 MB and from 34 to 529 pages (2,514 pages and 525 MB in all). They show
the following about the collection, and the converter handles each point as stated:

* **Most are assembled documents.** Text written in Word was exported, then combined in Acrobat with scanned or
  drawn parts: plans, photographs, forms, older reports. 10 of the 15 are mixed, so routing is per page, never
  per document. Of the 2,514 pages:
  * 1,981 carry a usable text layer (`digital`);
  * 533 do not. page-classification calls 434 of them photographs or drawings (`route: none`, nothing to read)
    and 99 text or tables (`ocr`);
  * two documents carry a prior OCR layer on some pages (Acrobat Paper Capture, PDFTron), under
    `OCR_LAYER_DOCUMENT_SHARE`. Those pages are `ocr` and the document is converted;
  * one has 23 pages that draw only vector paths (plans, or text drawn as curves): `none`.
* **What born-digital text does to words**, all fixed in the light engine (`digital_pdf.py`):
  * Acrobat Distiller sets the Czech letters outside WinAnsi (ě č ř ů ň ť ď) in a second subset of the font.
    Words were split at each of them ("Ji č ín"), which affected nearly 24,000 letters. After the fix, 484
    remained, and 432 of those were the vertical headers below, which are now read correctly too;
  * small capitals are a full-size capital and smaller capitals: "O BRÁZEK" (212 times in one report),
    "T ABULKA", "N EGATIV". They now read as one word ("OBRÁZEK"). Real spaces ("P VI") stay;
  * table headers set vertically were read one letter per line (432 lines on 77 pages of one report). A run of
    characters on a 90° baseline is now one line, read in its direction;
  * headings were judged against a body size that table cells set, so in table-heavy reports every paragraph
    was a heading (1,927 of 30,938 lines in one report; Docling finds 192). Now only the body text of `digital`
    pages counts.
* **Glyphs without a Unicode mapping.** A font without /ToUnicode gives pdfminer no character. Each such glyph
  is now U+FFFD (it was the text `(cid:N)`). A line over 15 % of them is `Garbage` and its page `garbled` (18
  pages). One report loses every ř, ě, č and ů this way on the rest of its pages. `/describe` sends a page to
  OCR when at least `ROUTE_DAMAGED_WORD_SHARE` (5 %) of the words of its body text are damaged (29 pages, a
  median of 19 % of their words). Docling recovers none of them. The record flags these pages itself
  (`needs_ocr`, `garbled`, the damaged words in the reason), so a step that reads only the record hands them to
  OCR. `/describe` routes them to `ocr`.
* **A page number over a scan.** A scanned page that Acrobat numbered has a text layer: the number. The page is
  `none` when its only text is furniture and its images cover at least `FURNITURE_ONLY_IMAGE_COVERAGE` of it (2
  pages: a drawing and a photograph). 36 pages of another report carry a running header over figures covering
  58–64 % of them, and stay `digital`. page-classification calls 30 of them photographs or drawings, and 6
  excavation plans with a legend (`DRAW_L`). The labels of those plans ("Objekt 22") and their title block are
  part of the image. `/describe` asks about such pages and sends a type that carries text to OCR, so these 6
  are re-acquired.
* **The quality model and born-digital text.** ocr-postprocess's line-quality model judges running text. On
  born-digital pages its `Trash` lines are table cells, codes, coordinates, photo IDs and table-of-contents
  leaders, whose text is exact. Counted over every line, `ROUTE_TRASH_SHARE` sent 612 of the 1,981 `digital`
  pages to OCR. The converter now counts prose lines only, and that moves none of them.
* **Section titles in bold at the body size** were body text (one report had 19 headings where Docling finds
  192). A bold body-size line that reads as a title is now a heading, one level below the size-based ones. The
  conditions are 2–100 characters, no trailing comma, semicolon or hyphen, not a list item, at most two bold
  lines in a row, and not the same bold line on three or more pages. The rule stands aside in a document whose
  bold body-size text is over `BOLD_HEADING_MAX_SHARE` (25 %) of its body text, and 0 switches it off.
* **Known, not handled:** doubled glyphs on cover pages ("ARC CHEOLO GICKÝ"), letter-spaced captions and map
  labels, unruled tables read column by column.
* **Resources** (2026-10-14, an A40 node; times differ between nodes, memory does not):
  * the light engine takes 3–50 s per document. Its peak is at most 0.7 GB on the command line and 0.8 GB in the
    service (783 MB for a 282-page report; 560 MB for the 156 MB one);
  * memory follows the text a document holds, not its size. Budget about 1 GB plus the upload limit per
    `MAX_CONCURRENT_JOBS` slot. `MAX_UPLOAD_MB` is 250 by default;
  * Docling (`--engine docling`, on the GPU) takes 25–341 s and 2.2–7.4 GB per document;
  * `/describe` with both stages takes up to 276 s: page-classification 135 s on 149 flagged pages, and 202 s on
    all 529 with `classify_pages=all`. `STAGE_TIMEOUT_S` is 360 s by default.
* **What the stages receive.** The compact record `/describe` sends a stage reached 14 MB. page-classification
  also gets the whole PDF, and its defaults (10 MB per part, 50 pages per call) need raising for these files
  (`service/README.md` § Limits).
* **For a model.** The Markdown runs up to about 290k tokens at `full` and 150k at `minimal` for a 400–530-page
  report, so a model stage downstream has to chunk it.

`tools/samples_report.py` measures all of this on a run. It prints a "Text defects" table, the quality model's
moves and what it judged (by kind of line), the pages whose only text is furniture, and the limits the run
suggests.
