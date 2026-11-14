# Strategy — [atrium-project#84](https://github.com/ufal/atrium-project/issues/84) (doc-schema-v2): digital-convert's part

> _**Refreshed 2026-11-13**: "Status — round 2" (the code, pushed 11-13 to `doc-schema-v2`) and round 4's notes. The file keeps
> the name `doc-schema-v2.plan.md` (it was `84.plan.md` on 11-12 and 11-13)._
> _**Refreshed 2026-11-12** on ARÚP's output contract of 12 November and atrium-project#84 (motyc, 11-12), at this
> checkout's head `6e49dec` (= `test`). The hub carries the whole change:
> [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md) ·
> [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) (the docproc schema
> of #84's comment 1) · [contract digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1211.md).
> This file is the repository's slice only; its 11-11 version (written before the
> issue existed), in which digital-convert wrote the born-digital record, read images and CSV, and kept the
> `ocr_text_layer` refusal — all superseded below._

**Status (2026-11-13)**: 🟡 **Implemented, pushed 11-13 to `doc-schema-v2`; see "Status — round 2". Deferred items are listed there.**

**Status (2026-11-12)**: 📋 **Planned, plans only. digital-convert becomes a docproc producer: it runs first on every
PDF and office file, gives a verdict for every page, never refuses a document and never decides from a sample; a
page it cannot use is `needs_atr` (D20, H4, H12). It no longer writes the record: ocr-postprocess builds it from
docproc. Its docproc is the stored one when no page needs ATR (H14). Code starts after Kate's and Ronald's answers
on the two-level contract (16–20 November) and Ronald's additive schema changes (ask 13).**

**Status (2026-11-11)**: 📋 Planned: the born-digital record writer on every input, with decode markers.
_(superseded 11-12)_

## What it writes

**In the pipeline: `atr/docproc-json`** (ARÚP's schema, #84 comment 1, with the additive changes of Ronald's ask 13).
The record is ocr-postprocess's; the Markdown view stays a development output (contract §4).

| docproc part                                                                               | Written here                                                                                                                    | Note                                                                                                              |
|--------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `version`, `sourceFormat`, `documentLanguage`                                              | yes                                                                                                                             | `sourceFormat` gains ODT and spreadsheets (ask 13); the producer is named in the paradata, not in `pipeline`      |
| `metadata.routing.pages[]`                                                                 | one entry for **every** page: the text-layer verdict (`digital`, `garbled`, `ocr`, `none`, `blank`), `needs_atr` and its reason | today's `pages[].text_layer`, `needs_ocr`, `needs_ocr_reason` move here (D27); an empty page routed `empty` (D29) |
| `pages[]`                                                                                  | `pageNumber`, `width`, `height` (pt), UserUnit factor; no entry for an empty page                                               | one coordinate space: CropBox origin, after `/Rotate`, 1/72 inch × UserUnit (D28)                                 |
| `blocks[]`                                                                                 | paragraph-level blocks with `id`, `type`, `bbox`, `readingOrder`, `confidence` 1.0; tables with `cellGrid`                      | from today's lines, groups and tables                                                                             |
| `blocks[].lines[]`                                                                         | each line's text, box and box precision (`measured`)                                                                            | optional in the schema (ask 13); these are the exact lines H6 keeps                                               |
| line flag                                                                                  | the decode verdict (`Garbage`, `Inverted`) as a flag scoring may read                                                           | rule 2; never in `categ`                                                                                          |
| `imageRef`, `imageData`, `pipeline`, `processedAt`, `sourceFile`, `sourceHash`, `warnings` | not stored                                                                                                                      | asks 3, 12; the original by SHA-512 in the paradata                                                               |

**No longer written:** the record (`source`, `pages`, `content`, `lines`, `tables`), `categ`, `quality_score`,
`lang`, `quality_band`, `source.sha256`, the licence detail.

## Tasks (next rounds)

1. **A docproc writer** (D20). From the internal `DigitalDocument` (`api_util/digital_ir.py`): pages, paragraph
   blocks with their lines, tables as `cellGrid`, reading order; validated against the shared docproc schema once
   it is in aiscr-docs-pipeline (D30). `POST /reformat` (`service/api.py:668`) returns docproc; the record writer
   (`api_util/digital_to_json.py`, `normalize()`) and `json_to_md.render_record()` (498) remain for development
   output only. Line numbering from 1 (`renumber_lines()`, `digital_ir.py:327`).
2. **A verdict for every page, no refusal** (H12, D27). `_refuse_ocr_layer()` (`digital_to_json.py:909`, called at
   952; 422 `ocr_text_layer`, threshold `OCR_LAYER_DOCUMENT_SHARE` 0.5, `tool_limits.py:58-63`) goes: an earlier OCR
   layer makes its pages `needs_atr`, and the document goes through the ATR whole (H4). A page that cannot be
   opened or decoded is `needs_atr`, not an error. `needs_ocr`/`needs_ocr_reason` (written at 569-647, 1009) become
   `needs_atr` and its reason in routing. Limits that are not about content stay refusals (`MAX_PAGES`, 879-887;
   the upload size): the whole job fails (D13). _(11-13, #9 point 3: a page whose words lose letters is not
   `needs_atr`; that is scoring's question. Done in round 4.)_
3. **No decision from a sample** (H12). `PDF_OBJECT_CAP` (`tool_limits.py:114`; `text_formats.py:2762-2850`) stops
   counting a page's objects at 20,000 and notes `pdf_objects_capped`; the text is read in full, but the page's
   verdict (invisible text, images, mirrored and rotated runs) then rests on the first objects only. Either count all
   objects for the verdict, or make a capped page `needs_atr`. Likewise `ODF_REPEAT_CAP` (`sheet_repeat_capped`,
   vendored from ocr-postprocess): a capped sheet fails or is read whole, never trimmed.
4. **The decode flag** (rule 2). `classify_line()` (`digital_to_json.py:457`) and `decode_sanity()` (408) keep
   their logic; their verdict becomes the docproc line flag. The page mean and the bands go; scoring is
   ocr-postprocess's (rule 3).
5. **One coordinate space** (D28). No UserUnit, CropBox or `/Rotate` handling exists in `api_util/digital_pdf.py`
   today (pdfplumber boxes, top-left, 495-511): add it, and record the UserUnit factor on the page. Docling's boxes
   go through the same conversion (`top_left()`).
6. **Inputs** (H12, H13). Every PDF and office file (`KINDS`, `digital_to_json.py:312`: PDF, DOCX, ODT, ODS, XLSX,
   RTF, DOC, XLS; `sniff()`, 709). Images skip digital-convert (H13): the 11-11 task "add images" is withdrawn. CSV,
   TSV and TXT are not named by the contract: the hub's Q9. _(11-13, #9 point 4: they skip the visual steps and go
   straight to scoring; who reads them into the record is still Q9.)_
7. **SHA-512** (#84 §4): `sha256_file()` (`digital_ir.py:148`) leaves the output; the original is named in the
   paradata by its SHA-512 (`sha512_file()`, 152); `verify_seed_digest()` (`digital_to_json.py:833`) stays.
8. **No licence, no run data** (D1, D2): `license_detail_for()` (`digital_to_json.py:1167`, calls at 1184, 1217)
   goes; nothing of the run in docproc.
9. **`/describe` has no role** (H11). It stays a people's endpoint (`service/api.py:737`); its stage calls
   (`service/stages.py`, `PAGE_CLASSIFICATION_URL`/`OCR_POSTPROCESS_URL` 71-72) are out of the pipeline. Parked:
   not extended.
10. **Office files rendered for page geometry** (#9 point 5, 11-13): DOCX and the other office files are rendered,
    so their pages and lines get boxes in pt as a PDF's do; only the pages that need the ATR go to page-classification
    and the ATR, not every page with an embedded image. Ask 13's "flow document without a page size" then concerns
    the text-only inputs alone. Detail: [`9.plan.md`](9.plan.md).

## Paradata (H14; #84 §4)

One action per run:

- **Its docproc is stored** (no page needs ATR): in `paradata/atr/docproc-json`; object = the original
  (distribution name, SHA-512); result = `atr/docproc-json` (SHA-512).
- **A page needs ATR** (its docproc is not stored): in `paradata/atrium/document-json`; result = its docproc by
  SHA-512, **marked not stored** (D23). The record builder names the same SHA-512 as an input of the merge.
- **Parameters:** the engine (light or Docling) and what shaped the verdicts. **Limits:** `split` only (no sampling,
  H12). **Components** (`para_config.txt`), facts with their terms: LibreOffice (MPL-2.0), pdfplumber (MIT), pypdfium2,
  Docling (MIT; its model weights with their own terms), flexiconv (MIT), and the rest as listed; never merged.

## Status — round 2 (11-13)

Local branch from `test`, on the re-vendored shared modules (record 2.0, paradata 3.0,
`ContentLimitError`); delivered as an archive with the commands to run; pushed 11-13 (below). Nothing posted.

- **Task 1, docproc:** `api_util/docproc_writer.py` writes docproc 1.1 and validates it against
  `schemas/docproc/document.atrium-proposal.schema.json`. That file is #84 comment 1's schema
  (`document.actual.schema.json`, kept verbatim) with asks 3 and 13 applied by `tools/docproc_proposal.py`; a test
  pins that the two differ by those asks only. `/reformat` returns docproc; the CLI writes it (`--docproc-out`). The
  Markdown and `/describe` read a development dict, not a record.
- **Task 2:** no refusal (`ocr_text_layer`, `OCR_LAYER_DOCUMENT_SHARE` gone): such pages are `needsAtr` with a
  reason; `metadata.routing.pages[]` lists every page (`text-layer`, `atr`, `empty`).
- **Task 3, in part:** `ODF_REPEAT_CAP` raises. **Open:** `PDF_OBJECT_CAP` still judges a page with over 20,000
  objects on its first ones (`sampled`).
- **Task 4:** the decode verdict is a line flag (`Garbage`, `Inverted`); no bands.
- **Task 5, deferred:** UserUnit, CropBox, `/Rotate` (ask 10, D28).
- **Tasks 6–8:** images get `415 unsupported_media_type` (H13); CSV and TXT unchanged (Q9); SHA-512 only; no licence
  and no run data in docproc.
- **Task 9:** `/describe` parked; its call to `/score_record` no longer has a record to send (noted, not fixed).
- **Paradata (H14):** `object`: `original` and the seed; `result`: `atr/docproc-json`, `stored: false` when a page
  needs the ATR.
- **Tests:** 959 passed.
- **Pushed** to `doc-schema-v2` (11-13) without `api_util/docproc_writer.py`, `schemas/docproc/`,
  `tools/docproc_proposal.py` and `tests/test_docproc_writer.py` (a conversion failed without the writer). Round 4
  adds them, declares the writer in `.github/production-image.json` and re-vendors the addendum's shared files.
- **[#9](https://github.com/ufal/atrium-digital-convert/issues/9) points 1–3 (round 4):** a page whose words lose
  letters to unmapped glyphs is no longer routed to the ATR, and `ROUTE_DAMAGED_WORD_SHARE` goes with that route;
  `/describe` still counts the damaged words but routes nothing on them; the README's `garbled` means a layer that
  does not decode. The suite passes (959). Points 4 and 5: [`9.plan.md`](9.plan.md).
- **Q15 (hub, 11-13):** Kate's reply on atrium-project#84 has the stored docproc written by the ATR. Until that is
  answered, `stored` stays as H14 says (`true` when no page needs the ATR).

## Depends on

- Kate's answer on #84 (16 November) and Ronald's on aiscr-docs-pipeline#17: D20, D24; asks 3 (run data out),
  4 (routing of every page), 5 (forced pages echoed), 10 (coordinates), 13 (optional `lines[]`, the verdict,
  `needs_atr`, `sourceFormat`, the `pipeline` pattern).
- Hub tasks 2 (no licence), 3 (`file_entity` with SHA-512 and the not-stored mark), 4 (`atrium_limits`), 7 (docproc
  fixtures; `e2e-digital-smoke.yml`); re-vendoring.
- ocr-postprocess's slice: the record builder reads this docproc and keeps its exact lines (H6).
- Hub questions Q9 (CSV/TXT) and Q10 (the unstored docproc of a mixed document).

## Superseded in this repository's logs

Marked in place in open issues' files (` _(superseded 11-12: …)_`); closed issues' files are not edited.

- This file's 11-11 version (replaced): the record writer on every input, images and CSV/TXT as inputs, the
  `ocr_text_layer` refusal kept, `lines[].decode` in the record, `pages[].origin`, `group_id`/`style` in the record.
- `plans/5.plan.md`, `digests/5.digest.md` (11-12 section): the 29 lost-letter pages flagged "in the record" → in
  docproc's routing (D27); the `ocr_text_layer` refusal → pages `needs_atr` (H12); the sample outputs in
  `data_samples/` → replaced (atrium-project#16, 11-12).
- `plans/8.plan.md`, `digests/8.digest.md`: parked (atrium-project#83), unchanged.

## Not in scope

- `/describe` in the pipeline (H11); the decision models of #8 (parked).
- The route and the ATR (the pipeline's and ARÚP's); page classification (beside the ATR, on `needs_atr` pages).
- Chunking and page rendering (the cluster side, D3, H1); `forms` (D19); full-text translation.
