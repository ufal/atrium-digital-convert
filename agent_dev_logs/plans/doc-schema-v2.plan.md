# Strategy — doc-schema-v2 (atrium-project, issue to be opened): digital-convert's part

> _Written **2026-11-11** after the meeting of that day (ARÚP's output contract) and K4TEL's points in session, at
> this checkout's head `336217b` (11-10). The hub carries the whole change:
> [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md) ·
> [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) ·
> [meeting digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1111.md).
> This file is the repository's slice only. Rename it to the issue number once the hub issue is opened._

**Status (2026-11-11)**: 📋 **Planned, plans only this round. digital-convert is the first step on every input and
one of the two line originators; it calls no other tool (H11). Code starts after the hub's shared modules.**

## Its fields

Only what this repository writes or stops writing; the full tables are the hub plan's.

| Field                                                                 | Written here? | Note                                                                                 |
|-----------------------------------------------------------------------|---------------|--------------------------------------------------------------------------------------|
| `source` (`sha512`, `filename`, `media_type`, `origin`, `page_count`) | yes           | `sha512` required; `sha256` goes (task 5)                                            |
| `content`, `tables[]`                                                 | yes           | the Markdown view (`json_to_md.render_record`) carries the same content              |
| `pages[].page_index`, `page`                                          | yes           | `page_index` already written (`digital_to_json.py:996`); `page` is the label         |
| `pages[].canvas`                                                      | yes           | `{width, height, unit: "pt"}`; PDF today, images new; none for office files and text |
| `pages[].origin`                                                      | yes, new      | `text_layer`                                                                         |
| `pages[].text_layer`, `needs_ocr`, `needs_ocr_reason`                 | yes           | signals to the route, not decisions (D14, H4)                                        |
| `pages[].decode_score`                                                | yes, new      | the page's share of decodable text                                                   |
| `pages[].quality_score`, `quality_band`                               | no longer     | ocr-postprocess's (D18)                                                              |
| `lines[].page_index`, `line`, `text`                                  | yes           | `line` from 1, today from 0; `page_index` new on the line row                        |
| `lines[].origin`, `bbox`, `bbox_level`                                | yes           | `text_layer`; pt, top-left; `line`. PDF only                                         |
| `lines[].group_id`, `style`                                           | yes           | layout cues                                                                          |
| `lines[].decode`, `decode_score`                                      | yes, new      | `ok`, `garbled`, `inverted`; the names are Q5                                        |
| `lines[].categ`, `quality_score`, `lang`                              | no longer     | ocr-postprocess's only (D17, D18)                                                    |

## Tasks (next rounds)

1. **Every input** (`KINDS`, `api_util/digital_to_json.py:312`; `sniff()`, 709; `README.md:14-17`). Today PDF,
   DOCX, ODT, ODS, XLSX, RTF, DOC, XLS. Add images (PNG, JPEG, TIFF): `canvas` from the pixel size and DPI, in pt;
   `text_layer: none`, `needs_ocr: true`; a visual description only (variant C). Add CSV, TSV and TXT through the
   vendored `text_formats.py`: lines and content, no canvas, no boxes (variant B).
2. **`/describe` calls nothing** (H11). Remove the calls to page-classification (`POST /predict_document`) and
   ocr-postprocess (`POST /score_record`, fallback `/process`) and their `PAGE_CLASSIFICATION_URL` /
   `OCR_POSTPROCESS_URL` (`service/stages.py:1-46, 172-180, 313-466`). The per-page route suggestions
   (`api_util/digital_report.py:68-72`: `nlp`, `ocr`, `htr`, `none`) stay, as signals for the pipeline's route step.
   The `ocr_text_layer` refusal (`OCR_LAYER_DOCUMENT_SHARE`) stays too; the route reads it as "OCR it" (H4).
3. **The decode markers** (D17, D18). `lines[].decode` and `lines[].decode_score` from `classify_line()` (457-468)
   and `decode_sanity()` (408-454); `pages[].decode_score` in place of the page mean and the bands
   `BAND_CLEAR_AT`/`BAND_NOISY_AT` (297-298, 597-602). Stop writing `categ`, `quality_score`, `lang` and
   `quality_band` (216-253; `normalize()`, 650-687, 665-668). The Markdown view follows.
4. **The line row** (H2, D8, D16). `line` from 1 (`api_util/digital_ir.py:325-337`, `api_util/digital_text.py:127`);
   `page_index` on each line row (`digital_to_json.py:1021`); `origin: text_layer` on pages and lines;
   `bbox_level: line`. Boxes stay as they are (pdfplumber, `digital_pdf.py:495-511`; Docling through `top_left()`,
   `digital_docling.py:171`); `canvas` names its unit, pt (`digital_pdf.py:963-970`).
5. **SHA-512** (K4TEL, §5.5). `sha256_file()` (`api_util/digital_ir.py:140-154`, used at `digital_to_json.py:1124`)
   gives way to SHA-512 for `source.sha512`. `sha512_file()` and `verify_seed_digest()` (833-860, the 422
   `source_digest_mismatch`) stay. The paradata name files through the shared `file_entity` (`service/api.py:604,
   721`) once hub task 3 gives it SHA-512 and the distribution name.
6. **No licence in the record** (D2). `license_detail_for()` (`digital_to_json.py:1167`) and its call go.
7. **Limits refuse** (D13). Page and size limits already refuse (`LimitExceeded`: `MAX_PAGES` 422, the upload 413).
   The vendored reader's `ODF_REPEAT_CAP` writes a `trimmed` note (`tool_limits.py:108`); it is to refuse instead,
   changed in ocr-postprocess, the reader's owner, and re-vendored.
8. **Run data out** (D1). No `provenance`, `assembled` stamps or `derived_from`; one `run_uuid` per block (Q1),
   through the re-vendored `atrium_document.py`. `README.md:16` loses `provenance` from the output.

## Paradata

One action per run, in `paradata/atrium/document-json`:
- **Object:** the original (distribution name, SHA-512) and the previous record version, when there is one.
- **Result:** the record blocks `source`, `pages`, `content`, `lines`, `tables`.
- **Parameters:** the engine (light or Docling); the threshold for an earlier OCR layer
  (`OCR_LAYER_DOCUMENT_SHARE`, 0.5).
- **Limits:** `sampled` (`PDF_OBJECT_CAP`) and `split` only.
- **Components, as facts with their terms** (`para_config.txt`): LibreOffice (MPL-2.0), pdfplumber (MIT), Docling
  (MIT; its model weights with their own terms), flexiconv (MIT), and the rest as listed. Never merged; the
  document's licence lives only in AMČR's RO-Crate.

## Depends on

- Hub tasks 1 (`_migrate_1_to_2()` renumbers these lines from 1 and maps `Garbage`/`Inverted` to `decode`), 2 (no
  licence merge), 3 (`file_entity`), 4 (`atrium_limits`), 5 (the decode vocabulary), re-vendored with
  `scripts/revendor_shared.sh`.
- Q1 (where the per-block `run_uuid` lives), Q5 (the names `decode`/`decode_score`).
- ocr-postprocess's slice: it scores every line (else the lines lose their category when `categ` leaves here) and
  its D14 merge reads `decode`; the reader for CSV/TSV/TXT and the `ODF_REPEAT_CAP` change come from there.
- The proof gate (D12): a document of each variant A–D through the assembled chain.

## Superseded in this repository's logs

Marked in place in open issues' files (` _(superseded 11-11: …)_`; line numbers after this round's edits); closed
issues' files are not edited.
- `plans/5.plan.md:79` — `/describe` asks page-classification by default → H11.
- `plans/5.plan.md:122` — `summary.reacquire_pages` as AMČR's way to find OCR pages → the route is the pipeline's (H4).
- `plans/5.plan.md:129` — unscored lines keep the converter's values; the stage `partial` → D17, D13; Q7.
- `digests/5.digest.md:140` — `/describe` calls page-classification and ocr-postprocess → H11.
- `digests/5.digest.md:184` — a `skipped` note while the service answers 200 → D13.
- `plans/5.plan.md:9`, `digests/5.digest.md:44` — chunking in a post-pilot issue → the cluster side (D3, H1).
- `plans/8.plan.md:68, 252`, `digests/8.digest.md:29, 167` — the stage lands after the pilot set → parked.
- `plans/8.plan.md:232` — a third stage in `service/stages.py` → parked; H11.
- `plans/8.plan.md:124`, `digests/8.digest.md:166` — images as `regenerable` recipes → D1.
- `plans/8.plan.md:76` — the SHA-256 of `amcr_flat.json` → moot while parked.
- Closed, not edited: `digests/2.digest.md:31-32` and `digests/4.digest.md:221` (`/describe` calls both stages →
  H11); `digests/2.digest.md:82` (the `Garbage`/`Inverted` verdicts stand → D17, D18); `plans/4.plan.md:35` (W4
  replaces a flagged page's lines whole → D14).

## Not in scope

- `/describe` inside the pipeline (parked); the endpoint stays for people and calls nothing.
- The decision models of #8 (parked during the pilot phase).
- The route (the pipeline's step) and page classification (bundled with the ATR call, on every PDF and image).
- Chunking and page rendering (the cluster side, D3, H1); `forms` (D19); full-text translation.
