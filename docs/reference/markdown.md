# The record as annotated Markdown

The Markdown rendering of a record: its cues, the detail profiles and the TEITOK input.

`/reformat` with `markdown=true` returns the record rendered as **annotated Markdown**. The same
renderer is available on the command line, and the TEITOK/ALTO renderer sits beside it:

```bash
python3 api_util/json_to_md.py CTX000000001.document.json --detail standard
python3 api_util/xml_to_md.py  CTX000000001.teitok.xml --format layout --detail minimal
python3 api_util/doc_to_visual_md.py report.pdf --output report.md         # convert + render in one step
```

* **The format.** The Markdown is page-sectioned (`## Page N`). The visual-layout cues ride in HTML
  comments (`DOC_META`, `BBOX`, `PAGE_BREAK`, `NEEDS_OCR`, `HEADER_*`/`FOOTER_*`, headings, GFM
  tables, footnotes). The full taxonomy is `CUE_SCHEMA` in [`api_util/layout_md.py`](../../api_util/layout_md.py).
* **Text never forges a cue** ([#6](https://github.com/ufal/atrium-digital-convert/issues/6)).
  Document text that holds `<!--` or `-->` has them escaped (`&lt;!--`, `--&gt;`), and a line of
  text that starts with `#` starts with `\#`, so a line reading `## Page 7` or `<!-- PAGE_BREAK -->`
  is text. A page label, a `needs_ocr_reason` or a font name inside a cue stays on one line. A
  reader that meets a raw `<!--`, a `## Page N` or a `PAGE_BREAK` is reading the renderer's own
  structure: there are exactly as many `## Page` sections as pages. Text without these characters
  renders as before; `--format text` keeps the document's text as it is.
* **The one model-facing representation.** Annotated Markdown is the single general input a language
  model is given (digital-convert#3). PDF, DOCX, TEITOK, PAGE XML and ALTO stay upstream, as sources;
  nothing downstream takes raw HTML or XML as a prompt. The record route and the TEITOK/ALTO route emit
  the same cue vocabulary and the same page labels. What the model said (`enrichment`) is never read back
  into the Markdown, and entities, keywords and enrichment are checked on the record, not in the Markdown.
  [`tests/test_md_stress.py`](../../tests/test_md_stress.py) holds both routes to this at every detail profile:
  each page and line once and in order, the cues each profile promises, the grouped structure, no
  `enrichment`, and the same output on every run and hash seed.
* **Who uses it.** The whole-document runs of the keyword clients read `.md` files
  ([atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract)'s `openrouter_client.py` and
  `ollama_client.py`); its service works line by line on the record and does not need it. How the renderer
  is shared with that repository (vendored with a pin) is still to be settled (atrium-project#72 B).
* **One source.** The rendering is a pure function of the record, so the Markdown cannot differ
  from the JSON.

## Detail profiles (`--detail`)

Three cue profiles, the values of the record's `regenerable.markdown.detail`
(hub [#70](https://github.com/ufal/atrium-project/issues/70), item 1):
* the **text lines are the same in all three**; a lighter profile only drops cues;
* the cue sets nest (minimal ⊂ standard ⊂ full);
* `full` is the default.

| Cue                                                                                                                        | `full`   | `standard`                                                                    | `minimal` |
|----------------------------------------------------------------------------------------------------------------------------|----------|-------------------------------------------------------------------------------|-----------|
| `# doc`, `## Page`, `PAGE_BREAK`, `NEEDS_OCR`, headings, footnotes, GFM tables, `HEADER_*`/`FOOTER_*`, figure placeholders | ✓        | ✓                                                                             | ✓         |
| `OCR` provenance, `DOC_META`, whole-line `**bold**`/`*italic*`                                                             | ✓        | ✓                                                                             | –         |
| `BBOX`                                                                                                                     | per line | one per block (a `group_id` run; a table keeps its own; ungrouped lines none) | –         |
| `LAYOUT_MARGIN` (canvas minus the body-line union; record route, pages with a canvas and boxes)                            | ✓        | –                                                                             | –         |

What each profile costs, from `python3 scripts/detail_budget.py` (characters; ≈tokens = chars / 4):

| Input                                                      | full            | standard       | minimal        |
|------------------------------------------------------------|-----------------|----------------|----------------|
| hub E2E scan `CTX192100040.alto.xml` (xml_to_md layout)    | 32 234 (≈8 058) | 21 102 (−35 %) | 15 535 (−52 %) |
| hub E2E scan `CTX192601143.alto.xml` (xml_to_md layout)    | 26 614 (≈6 653) | 12 555 (−53 %) | 9 536 (−64 %)  |
| `CTX000000002.teitok.xml` writer sample (xml_to_md layout) | 785             | 529 (−33 %)    | 385 (−51 %)    |
| `two_column.pdf` fixture (JSON route)                      | 1 293           | 907 (−30 %)    | 625 (−52 %)    |
| `enrichable.pdf` fixture (JSON route)                      | 596             | 354 (−41 %)    | 282 (−53 %)    |
| `rich.docx` fixture (JSON route; DOCX has no boxes)        | 632             | 632 (0 %)      | 628 (−1 %)     |

* **The recipe.** The record's `regenerable.markdown` recipe (`json_to_md@1.1`) is written only when
  the record can actually be rendered.
* **Unemitted cues.** Cues in the catalogue that no profile emits (`INDENT`, `LAYOUT_COLUMN`,
  `FONT`, `STYLE`, `WATERMARK`, strike, underline, alignment) are listed, each with its reason, in
  [`layout_md.RESERVED`](../../api_util/layout_md.py).
* **The deprecated direct converters.** `api_util/pdf_to_md.py` and `docx_to_md.py` are reached
  only through `doc_to_visual_md.py --legacy` / `--ocr`, for A/B checks, and render `full` only.

**TEITOK input.** `xml_to_md.py` reads a TEITOK (`*.teitok.xml`) or raw ALTO document.
* The TEITOK files come from atrium-nlp-enrich (format 2, `teitok-2`) or from flexiconv.
* The line-level reader [`api_util/teitok_read.py`](../../api_util/teitok_read.py) is vendored
  byte-identical from atrium-nlp-enrich, together with `bbox_scale.py` and `flexiconv_convert.py`,
  and pinned by `tests/test_vendored_teitok_parity.py`.
* The format itself is described in nlp-enrich's documentation, section
  [TEITOK XML: the output format](https://github.com/ufal/atrium-nlp-enrich/blob/test/docs/how-it-works.md#teitok-xml-the-output-format).
