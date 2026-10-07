# 📓 atrium-digital-convert — agent_dev_logs/DEVLOG.md (timeline index)
> _**Since 2026-10-01 this is `ufal/atrium-digital-convert`, the born-digital stage** (file → `atrium_document`
> record; `POST /reformat`, `POST /describe`). The repository started as `atrium-llm-enrich`; its keyword/LLM stage
> moved to atrium-keyword-extract (#1). Open issues: **#1** (was llm-enrich#29, the repository's new role), **#2**
> (was llm-enrich#28, `api-digital`), **#3** (was llm-enrich#11, DU inputs), **#4** (was llm-enrich#10, PDF/DOCX
> inputs). Entries up to 2026-09-30 use the old numbers. Releases: **v1.0.0-beta** (2026-10-01, the transitional
> snapshot of llm-enrich 0.9.0) · **v1.1.0-beta** on `test` since 2026-10-04 (`20dabd8`, `b1cbb0c`), not yet tagged._
>
> _The header below is the llm-enrich timeline's, kept as it was on 2026-09-26:_

## (historical header)
> _LLM-driven enrichment of archaeological documents (local multi-GPU + remote-as-a-service). 6 open
> issues (#10, #11, #13, #18, #24, #25); #8 closed. AMČR baseline (atrium-project#67, 2026-09-26): #10 finish ·
> #13, #18 close · #11 defer · #24, #25 stop. `test` = `79b857d` (2026-09-26) · **v0.7.0**
> (2026-09-16, `f7b0ecf`). Everything since — the 2026-09-24 round-4 fixes, the 2026-09-25 #18 converter and the
> 2026-09-26 freeze files — is on `test`, not yet released._
> _Per-issue detail: `digests/{id}.digest.md` · `plans/{id}.plan.md` · `issues/` exports
> (source of truth). Cross-repo/hub history (DU benchmark hub#22, this repo's spin-out from
> hub#24) lives in `ufal/atrium-project/agent_dev_logs/DEVLOG.md` (deduplicated out of this file).
> Note: this repo's own **#24 "Application of olmOCR"** (opened 09-03) is a different,
> coincidentally-numbered issue — not the hub spin-out issue of the same number._

## 2026-07-12
- **#8 LLM applications to data — initialization of repository** — Opened by K4TEL as the repo-side
  continuation of hub [ufal/atrium-project#24](https://github.com/ufal/atrium-project/issues/24):
  spin the LLM-only subtasks out of the NameTag3/UDPipe-entangled `atrium-nlp-enrich` into a focused
  sibling repo. Engine copied byte-identical; the actual new work is `openrouter_client.py`,
  `ollama_client.py`, `api_util/xml_to_md.py`, and the torch-free `llm_client_shared.py`.
- **#8 (repo)** — **Document-Understanding scripts** landed (`f2ec956`): `eval_metrics.py` (CER/WER,
  normalized edit distance, entity F1, optional TEDS) and `sample_stratify.py` (quality-stratified
  80/10/10 page sampling) — the hub **#22** benchmark primitives. Licenses test `tests/test_para_licenses.py`
  (`83d7480`), GHA version bumps, dependabot merges (transformers ≥4.57.6, pydantic 2.13.4). Issue #8
  logs + digest/plan added and renamed from the `24.*` hub pair (`1b94264`, `c259453`). Suite at **83
  `def test_`** across 5 files; ruff clean.

## 2026-07-13 → 2026-07-15
- **#8 (repo)** — Test infrastructure hardened: test reqs + formatting (`3779460`); fixtures and tests
  imported from `nlp-enrich` with the `pandas` dependency (`2a09efe`, `43259a4`) — the suite grows well
  past the 83 baseline; `pytest` requirement bumped `>=8.0 → >=9.1.1` (PR #9, `fde9334`); version bump
  (`2540ad9`).

## 2026-07-16
- **#8 / hub #22 (repo)** — DU next steps and test-coverage/reqs updates (`9267382`, `dd90b13`); issue
  logs refreshed (`19e6a03`).

## 2026-07-17
- **#10 PDF and DOCX inputs handling for DU** — Opened by K4TEL: process input PDFs of three kinds
  (curves-instead-of-letters / scanned images / digital-born-with-text), add DOCX as an alternative
  input, and consider DOCX/HTML as intermediate formats on the PDF→LLM path.
- **#11 Decide on inputs of LLMs based on benchmarks existing for DU** — Opened by K4TEL (`question`,
  `development`): a format × detail-level matrix (PDF / InDesign XML / HTML+CSS / DOCX / Page-ALTO XML
  / MD / TXT); find the format used in DU benchmarks toward a FAIR standard; consider
  `ufal/atrium-page-classification` and **Grobid** for PDF processing.
- **#8 (repo)** — Large-model-on-CPU run notes updated (`90a8762`).

## 2026-07-19 → 2026-07-20
- **#10** — **Research pass** (survey + routing, no code): three PDF classes collapse to two paths via
  a cheap `pdffonts`/PyMuPDF font+char+image census (digital-born → extract; curves+scans → render+OCR),
  with a decode-sanity guard for the garbled-diacritics (no-`/ToUnicode`) case. Permissive-first tool
  survey; recommendation to reuse the repo's **Markdown** (doc-level) or **TEITOK** (line-level) targets,
  not a new format. Digest+plan added (`5ee844f`). **Blocker** noted: flexiconv's license is undeclared
  upstream (`para_config.txt:32-33`).
- **#11** — Deep-research report (Gemini) on agentic, roadmap-driven FAIR document navigation added as
  `digests/11.digest.md` (`59f0ddb`, `f88b216`); issue logs refreshed (`752ac64`).

## 2026-07-21
- **#10** — K4TEL added two comments steering the issue to implementation: use PDF-to-MD / DOCX-to-MD
  tools and record **page borders + as many visual layout cues as possible as HTML comments inside the
  Markdown**, and made "the list of all possible visual layout pieces" — an **exhaustive taxonomy** of
  cues with their exact MD/HTML encodings (`<!-- PAGE_BREAK -->`, `<!-- BBOX -->`, `<!-- FONT -->`,
  `<!-- HEADER_START -->`, `~~strike~~`, footnotes, tables, `<!-- WATERMARK -->`, …).

## 2026-07-22

* **#10** — **First implementation landed** (`3e7a909`): a visually-rich Markdown converter for **DOCX + digital-born PDF**.
* New `api_util/` modules introduced: `layout_md.py` (dependency-free cue vocabulary + `CUE_SCHEMA`), `docx_to_md.py`, 
`pdf_to_md.py`, and `doc_to_visual_md.py`.
* Pipeline creates a `.md` in `INPUT_DIR`, which is consumed unchanged by `run_document_level()` — HTML-comment cues 
pass through as inert text.
* The scanned/curve-only **OCR path stays deferred** (pages flagged `NEEDS_OCR`; tool choice benchmark-gated under hub #22).

## 2026-07-23

* **#10** — Implementation updated on the `test` branch: unified schema via `api_util/xml_to_md.py --format layout` 
now emits identical cues for TEITOK/ALTO based on coordinate sets.
* The opt-in OCR path is enabled via `--ocr`, resolving pages with `pypdfium2` and transcribing with Tesseract `ces`, 
tagged explicitly with `<!-- OCR: engine=tesseract, lang=ces -->`.
* Pipeline integration auto-converts `.pdf`/`.docx` files dropped into `INPUT_DIR` via openrouter/ollama clients, 
fetching citations natively via the `page` field.
* **#11** — Decision drafted: the ingestion diet is annotated Markdown, utilizing HTML comments as low-token positional 
hints, bypassing HTML and keeping TEITOK as the spatial truth.
* Slated a bake-off through the #22 harness with Docling/Marker for robust table processing and token cost analyses.
* **#13 The intermediate steps - data format to use** — Opened by K4TEL to solidify intermediate candidates: MD with 
HTML for forms/tables as LLM input, TEITOK.XML for correct layout, and JSON for search and metadata storage.
* Drafted a systemic approach allocating separate authority for each plane: Reading (Annotated Markdown),
Layout/preservation (TEITOK.XML), and Search/knowledge (`AtriumDocument` JSON).

## 2026-07-24

* **#13** — Opus 5 ultracode refinements proposed preventing the pipeline from being forced into full monolithic 
runs by relying entirely on a deterministic merger.
* Transient images and thumbnail paths were entirely dropped from references to restrict linkages solely to persistent 
items like original inputs or output artifacts.
* Proposed a dedicated stateless pure function service called `atrium-aggregate` to assemble the `AtriumDocument` via `POST /aggregate`.

## 2026-07-25

* **#10** — Concluded that MD equipped with visual info inside comments supplies LLM input, whereas JSON defines 
the overarching document schema per Issue #13, and TEITOK.XML acts as the visually accurate record alongside NLP enrichment capabilities.
* **#13** — Adopted the paradata-pair model where every tool receives a document JSON, modifies its owned blocks,
and emits an updated JSON byte-identical to untouched parameters.
* `atrium_document.py` and `atrium_document.schema.json` defined as hub-canonical shared files within the ecosystem.
* `alto-postprocess` refined internally to remove redundant langID values and coordinate closely with the `atrium_document` 
draft added initially to `atrium-project` and `llm-enrich` (commit `4175b06`).

## 2026-07-26

* **#13** — `AtriumDocument` ecosystem integration expanded: the JSON schema draft landed on `atrium-nlp-enrich`, 
`atrium-page-classification`, and `atrium-translator`.
* `para-drift` GHA check added to the `atrium-project` hub.
* Component beta releases shipped: `atrium-alto-postprocess` (v1.3.0-beta), `atrium-page-classification` 
(v1.7.0-beta), `atrium-nlp-enrich` (v0.18.0), and `atrium-translator` (v0.10.0).
* `atrium-llm-enrich` integrated the schema at the API and `llm_run` levels (Commit `c565e1a`), pending decisions 
approval for a formal release.

## 2026-07-31

* **#13** — Alignment pass completed by Sonnet uncovering and resolving critical pipeline bugs.
* `atrium-nlp-enrich` `run_document_hook()` rewritten with real ALTO+CoNLL-U integration testing after failing to 
produce entities natively in production due to key errors.
* `atrium-alto-postprocess` switched from `set_blocks` to `merge_blocks` for field-split outputs, preventing 
downstream overwrites.
* `atrium-translator` logic cleaned up by stripping dead entity translation code and enforcing correct schema boundaries.
* `atrium-llm-enrich` introduced `api_util/json_to_md.py` to regenerate the annotated-Markdown diet efficiently 
straight from the JSON record, rather than re-requesting a TEITOK or PDF file.

## 2026-08-01

* **#13** — E2E-related pipeline smoke achieved CLI convergence: `--document-json`/`--document-json-out` file-pair 
flags successfully added to `atrium-page-classification`, `atrium-translator`, `atrium-alto-postprocess`, 
`atrium-nlp-enrich`, and `atrium-llm-enrich`.
* `openrouter_client.py` within `atrium-llm-enrich` fully supports the converged flag structure for single-file scoping.
* A silent baseline dropping bug triggered by a mismatch in `doc_id` derivation between the translator file output and
`nlp-enrich` expectations was permanently fixed.

## 2026-08-02

* **#13** — Identified the necessity to build a PDF and DOCX to JSON converter explicitly to service digital-born 
documents appropriately, mapping required actions back to the criteria in Issue #10.
* **#8** — Closed: the repo-initialization work this issue tracked (engine copy, `openrouter_client.py`/
`ollama_client.py`, torch-free `llm_client_shared.py`) has been done and superseded by #10/#11/#13 since July.
* **#18 Build explicit PDF/DOCX-2-JSON converter — digital-born documents** — Opened by K4TEL: supersedes/extends
#10. The Markdown-with-HTML-comments intermediate format is now deprecated in favor of `atrium_document` JSON as
the first-class canonical IR; a new `api_util/digital_to_json.py` must ingest digital-born PDF/DOCX directly into
the schema, mapping the #10 layout-cue taxonomy onto JSON nodes instead of Markdown comments.

## 2026-08-03 – 2026-08-04

* **#18** — Architecture defined and largely built same week: block-ownership decided (`BLOCK_OWNERS` gains tuple
values; `source.origin` selects the originator per document) — three defects this surfaced are fixed. A 08-04
review pass found the §1a "one originator per block" contract itself **escapable in five ways**; all five fixed
and tested. Digital-born converter scripts land (`4c28020`) alongside a large GHA infrastructure copy (dependabot,
CodeQL, api-contract, docker workflows — the standard five-repo template); doc-related dependencies added
(`054288d`); `atrium_document.py` re-aligned to the hub template repeatedly (`070908e`, `0f231ca`, `abbac79`,
`a02eee6`, `30c1c99`, `9ef2ae6`, `bd8ea39`, `8381b13`) as the shared contract kept moving underneath. Suite reaches
382 passed / 6 skipped / 0 failed by the end of this window.

## 2026-08-06

* **v0.6.0 — first release of the `digital-convert` originator.** `api_util/digital_to_json.py` (807 lines) plus
`tests/test_digital_to_json.py` (404 lines) land (`796c795`), completing #18's core task: a four-layer born-digital
PDF/DOCX converter with a **decode-sanity gate** that catches a text layer extracting *successfully and wrongly*
(CP1250 bytes misread as CP1252), reporting `needs_ocr` rather than silently rewriting text that `source.sha256`
still describes as unchanged. **Real defect fixed**: DOCX tables previously lost their text entirely —
`extract_docx()` now walks paragraphs and tables in document order (reusing `docx_to_md.py`'s existing in-order
walker), so cell text reaches both `lines[]` and `cells[].group_id`; before this, every table's text existed only
in `tables[].cells[].text`, invisible to `json_to_md`, nlp-enrich and the translator, with the documented join
resolving to nothing. Degenerate grids are now omitted rather than raising a Layer D violation on the converter's
own output. Also: a further "LLM review+fix round by Opus" (`f5418f3`), `ruff.toml` hardening (`f58e24c`), one more
`atrium_document.py` fix pass (`ce95280`), the version bump (`6c71425`), GHA test-coverage req fixes (`d475a78`),
and a Docker GHA update (`57d8073`).

## 2026-08-18 – 2026-08-20

* **The AMCR + TEATER vocabulary work ported over from `atrium-nlp-enrich`'s issue #6** (no dedicated issue here —
tracked purely through commits): `45d2668` aligns the vocabulary with nlp-enrich's harvest, `05aec39` fixes the
union, `8331b2e` fixes it again for the E2E GHA run. **v0.6.1** ships (08-19): vocabulary expanded and aligned,
explicitly flagged "work-in-progress in terms of the vocabulary definition" — llm-enrich is following nlp-enrich's
governance rulings (see that repo's DEVLOG, Phases 4-8) rather than making its own. Further GHA hardening
(`d52e0d7`, `3699c42` — another Opus-reviewed round touching GHA/template/converters) and a `vllm`-related
requirements fix (`549afa6`).

## 2026-09-03

* **#24 Application of olmOCR** — Opened by K4TEL: flags the LLM-based OCR system described in a blog post
(structure + coordinates, no comments yet) as worth reading about — a candidate for the still-deferred
scanned/curve-only OCR path from #10.
* Routine action bump (`d1b6bdd`); issue logs refreshed.

## 2026-09-04

* Vocabulary work continues in lockstep with nlp-enrich's #6 Phase 9-10 (see that repo's DEVLOG): `806efad` adds
the `vocab_sources.py` util here, `a92b6e6` brings over the decision-package docs (`6.D-eval.decision-package.md`,
`6.O3O4.decision-package.md`) and the new `vocab-drift.yml`/`vocab-refresh.yml` workflows, `5468e29` rebuilds the
vocab files from the flat harvest. Version bumped (`e5a674c`).

## 2026-09-06

* **v0.6.2** ships. Vocabulary and prompts brought fully in step with nlp-enrich's shipped state (`609c247`,
`084dda9`); `bef7fae` fixes the Docker build for the GHA digital-born e2e run; `ee16913` fixes GHA-related code in
`llm_client_shared.py`/`ollama_client.py`/`openrouter_client.py` with a new `tests/test_llm_client_shared.py`
(78 lines). Issue logs refreshed same day.

## 2026-09-07

* **State**: 5 open issues (#10, #11, #13, #18, #24); #8 closed. `test` and `main` both at `ee16913`, **v0.6.2**.
Confirmed live: the hub reusable-workflow reference is pinned to `@v1`. The document-format architecture (#10/#11/
#13) is implemented and stable; #18's core converter shipped in v0.6.0 with the DOCX-table defect it was built to
avoid already caught and fixed. The active thread is the vocabulary/prompt alignment with `nlp-enrich`'s #6 — this
repo has no issue of its own for that work, so its state should be read alongside nlp-enrich's DEVLOG rather than
this repo's issue tracker. #24 (olmOCR) is a fresh, unstarted lead on the still-deferred OCR path from #10.

## 2026-09-07 → 2026-09-17 (added 2026-09-24 from the changelog and commit subjects)

* **v0.6.3** (`2f2ebe2`, 09-07): born-digital `enrichment`-block parity (CLI and API agree when a consulted-but-empty
result still contributes a block; hub #49); Docker `HEALTHCHECK` and graceful `SIGTERM` for Kubernetes (hub #55). Its
changelog row says it closes #18; the issue stayed open (see `digests/18.digest.md`).
* **Hub standards rolled in** (09-08 → 09-15): SKOS view of the vocabulary (hub #51), RO-Crate module (hub #54),
`$PORT`/`HOST` (hub #58), `.env.example` + env contract (hub #60), logging contract (hub #61), 12-factor GHA edits, a
Docker test for GHA, `flexiconv` pinned to `@v0.3.10` (hub #62; it had resolved to the default branch).
* **v0.7.0** (`f7b0ecf`, 09-16): `entities[].pid` populated for the first time (`vocab_manager.concept_index()` /
`resolve_pid()`, `amcr` and `aat` only, merged as the `pid` field alone so nlp-enrich's rows survive); `union.skos.ttl`
from the raw harvest; all four SKOS mapping relations harvested; TEATER `quotes[]` as `dcterms:source`.
* **#25 opened** (09-17): archivatorium as a VLM OCR backend for #10's render+OCR path — see
[`digests/25.digest.md`](digests/25.digest.md). uvicorn bump (#26) on 09-23.

## 2026-09-23

* **TEITOK / flexiconv (nlp-enrich #9/#10/#28 umbrella, Stage 5; branch `claude/inspiring-cerf-2gdtd1`, local).**
  * `api_util/teitok_read.py` and `api_util/flexiconv_convert.py` are re-vendored verbatim from atrium-nlp-enrich,
    together with `requirements_flexiconv.txt`, `tests/test_flexiconv_convert.py` and the real flexiconv v0.3.10
    fixtures. `tests/test_vendored_teitok_parity.py` pins their SHA-256 and compares them with a sibling nlp-enrich
    checkout when one exists.
  * The reader now handles flexiconv output (no `<s>`) and text-faithful spacing. It also ignores `<dtok>` and no
    longer takes UPOS from `@type`; `tests/test_teitok_read.py` was adapted accordingly (first `<pb>` = page 1).
  * The stale `api_util/teitok_alto.py` writer fork, its copied tests and their CoNLL-U fixtures are deleted.
  * `xml_to_md.py` uses `sentence_text()` / `pb_page_number()` and reads documents without `<s>`.
  * `para_config.txt` records flexiconv as GPL-3.0 (conditional); README / CONTRIBUTING updated.
  * `pytest -m "not slow"`: 935 passed, 18 environment-only skips. #10/#13 dev logs updated.

## 2026-09-24

* **TEITOK round 4 (atrium-nlp-enrich umbrella plan, Stage 7) — audit, dev logs.** The 09-23 Stage-5 work above is on
  `test` as `f62921c` (the branch name in that entry is historical). Re-checked this round:
  * the ten vendored TEITOK files still hash-equal nlp-enrich `3654e73` (v0.21.0, format 2);
  * **a `.teitok.xml` given as line-level input enriches zero lines** — `read_input_rows()` scores every TEITOK row 0.0
    and the line filter turns that into "Trash" (`tests/conftest.py`'s `stub_llm` hides it); a CSV without a
    `quality_score` column is hit the same way;
  * the GPU path calls `_should_process_line` with 6 of its 7 arguments (`llm_utils.py:2059`, `:2301`);
  * `xml_to_md.py` ignores the writer's bbox origin; the #13 design's "JSON → TEITOK re-projection already exists" was
    never true.
  * The hub's `agent_dev_logs/{digests,plans}/13.*` held this repo's #13 design (hub #13 is the CAA paper); the design
    content moved into [`digests/13.digest.md`](digests/13.digest.md) §2, and the hub pair was rewritten.
  * #10 and #13 digests/plans refreshed; fixes are planned with nlp-enrich's round 4 (#13 plan P5).
* **TEITOK round 4 — implemented the same day (#13 plan P5; delivered as files, then pushed as `951db5e`).**
  * `llm_client_shared.row_quality()` / `llm_utils._row_quality()`: a missing quality score is "unknown" (None) and
    skips the quality bands; the output record carries `"quality_score": null`; both GPU call sites pass it;
    `tests/conftest.py`'s `stub_llm` no longer patches the filter. TEITOK input now enriches its rows.
  * Re-vendored from nlp-enrich's round 4: `api_util/teitok_read.py` (lines numbered per page, a sentence crossing a
    `<pb/>` split into page parts, `page_idx`/`page_label`), `api_util/flexiconv_convert.py` (exit 3 / 1) and
    `tests/test_flexiconv_convert.py`; pins updated; nlp-enrich's released `CTX000000002` (a `<pb/>` inside s-5)
    vendored as `tests/fixtures/teitok/writer/CTX000000002.teitok.xml`.
  * `xml_to_md.py`'s TEITOK layout reader rebuilt on the reader's page model: one line per page part, lines numbered
    per page, `## Page <label>`, `DOC_META … origin=printspace` when the writer says so.
  * README, CONTRIBUTING (the "`llm_utils.py` copied verbatim" claim corrected; v0.5.2 row fixed; Unreleased row),
    `para_config.txt`, the `vocab-drift.yml` header; the #24-vs-#13 test comments.
  * `pytest -m "not slow"`: 947 passed, 18 environment-only skips; `ruff` clean.
* **#10** — stranak asked (09-23 22:56) which route to Markdown to take: markitdown, flexiconv or the custom
  converters. The digest and plan (§9) carry a draft answer from the code and the §5 measurements (flexiconv writes
  no Markdown; markitdown keeps no layout cues); the decision and the reply are the user's. _(Decided and posted the same day, 11:20 — next entry.)_
* #25 (archivatorium as a VLM OCR backend) was opened on 09-17 and had no digest/plan yet _(written in round 5, next entry)_.


## 2026-09-24 (later): round 4 on `test`; #10 answered; round 5 — dev logs and README

* **Pushed:** `951db5e` (round 4: TEITOK input enriched again, GPU path fixed, re-vendored reader, `xml_to_md` page
model) and `08dff48` (#10 digest and plan: the G1–G8 gap register and the work plan); `test` = `main`, CI green. The hub's
E2E ran with it the same day (run 35990199050, green, format 2) and so did the digital-born smoke (35990199010).
* **#10 answered on the issue** (11:20, 5813071038): c) our converters, kept narrow to the JSON route and one cue layer;
b) flexiconv for the TEITOK side; not a) markitdown. Next: close G1–G8, switch the clients' auto-convert to the JSON
route, delete the legacy converters.
* **Round 5 (dev logs and docs only here):** README *TEITOK input, in short* (where the format and its composition are
described, format 2's page model, how to recognise files with UDPipe-chunk pages); CONTRIBUTING Unreleased row.
Dev logs: #10 (answer posted), #11 (the 08-02 validation task and its state; the "nothing lost" criterion re-scoped),
#13 (pushed), #18 (converter shipped in v0.6.0; three of four boxes done, layout cues open), #24 (milestone, sibling
#25), **new #25 pair**; milestones relabelled on 2026-09-08 are in every pair.

## 2026-09-25: defect V-1 fixed (found by atrium-alto-postprocess#31)

* **Found:** the hub's `docs/skos_strategy.md` recorded the V-1 fix as taken on 2026-09-16 (F1, "pinned by
  `test_convert_drops_trash_lines_on_the_ocr_path`"), but `api_util/json_to_md.py` still had
  `DROP_CATEGORIES = frozenset({"Garbage", "Inverted"})` on `test` and `main`, and that test did not exist. So every
  alto-postprocess `Trash` line still reached the model on the OCR path — now also the lines of every non-ALTO input
  alto-postprocess's text-lines method reads.
* **Fixed:** `DROP_CATEGORIES = frozenset(atrium_vocab.UNTRUSTWORTHY_LINE_CATEGORIES)` (Garbage, Inverted, Trash) and the
  comment over it; `tests/test_json_to_md.py::test_convert_drops_trash_lines_on_the_ocr_path` pins it (Clear, Noisy and
  Non-text are kept; each originator's untrustworthy label is dropped). **Behaviour change:** what the model is shown on
  every OCR document. CONTRIBUTING Unreleased row.
* The hub's canonical `atrium_vocab.py` comments and the schema's `categ` description say the same now; re-vendor them
  with the hub's next `v1`.
* `pytest tests/test_json_to_md.py tests/test_digital_to_json.py tests/test_atrium_vocab.py`: 65 passed, 10 skipped; the
  fast suite with the hub's new `atrium_document.py`/`.schema.json`/`atrium_vocab.py` swapped in: 923 passed, as before.
  Not pushed: files delivered in chat.

## 2026-09-25 (later): #18 finished in the working tree — layout cues, one route, #10's G1–G8

* **Asked (K4TEL):** make llm-enrich contain everything #18 needs; light libraries by default, a heavyweight method on
  request; research the sibling repos first (all six `test` HEADs fetched); fix every problem found; deliver full files
  in chat with the repo path in the file name.
* **Decided in session:** Docling as the opt-in heavy engine (TableFormer's CDLA-Permissive-2.0 logged as is);
  `lines[].style.region` for page furniture and footnotes (additive); switch the route, delete nothing.
* **Done** ([`plans/18.plan.md`](plans/18.plan.md) §0e, [`plans/10.plan.md`](plans/10.plan.md) §11): Layer A split
  into `api_util/digital_pdf.py` (words, columns, ruled tables, headings, running headers/footers, `/PageLabels`,
  pypdfium2 census), `digital_docx.py` (pages as alto-postprocess #31 counts them, tracked changes, lenient package,
  headings, headers/footers, footnotes), `digital_docling.py` (`--engine docling`) over `digital_ir.py`; Layer B garble,
  foreign-letter and condemned-page rules; OCR-layer PDFs refused; content sniffing, exit codes 2/3/4, `build_record()`,
  `--paradata-dir`; `json_to_md` renders headings, emphasis, header/footer cues, footnotes, GFM tables and text-less
  pages; `doc_to_visual_md` goes through the JSON route (`--legacy`, `--ocr` reach the deprecated converters); cache
  sidecar. Six new fixtures, `tests/test_digital_parity.py`, `tests/test_digital_docling.py`.
* **Problems fixed on the way:** digital-born records never carried their components' licence (CC BY-NC 4.0 default;
  now MIT); the `-digital` image installed docling (torch) and docx2python unused (requirements split, new
  `digital-docling` stage, not published); `.docm`/`.dotx` never opened; stale manifest-path and licence claims in the
  #18 plan and `digital_born/README.md`.
* `pytest -m "not slow"`: 1013 passed, 19 environment-only skips; `ruff` clean; fixtures and vendored files unchanged
  against the manifest and the hub. Docling's live run not verified here (model download 403); its mapping is tested.
  **Not pushed: files delivered in chat.** Hub follow-ups: rank CDLA-Permissive-2.0 in `para_licenses.py`, declare
  `style.region` in the canonical schema, add DOCX and two-column stages to `e2e-digital-smoke.yml`.

## 2026-09-25 (round 2): #18's hub follow-ups, the re-vendor, and the sibling docs

* **Asked (K4TEL):** fetch `test` (`c3575f5`: the #18 round as delivered, tables re-padded) and deliver the cross-repo
  work left; issue comments as raw Markdown in chat.
* **Hub (delivered as files):**
  * `para_licenses.py` ranks CDLA-Permissive-2.0 at 1.
  * The canonical schema declares `lines[].style.region` as a closed enum: the first additive change after the freeze
    tag `doc-schema-v1`.
  * `e2e_assert.py --expect-layout`.
  * `e2e-digital-smoke.yml` Cases 4–6 (`rich.docx`, `two_column.pdf`, `ocr_layer.pdf` → exit 3). They are gated on
    the image's `--help` because `main` already has the new fixtures while `:latest` is v0.7.0.
  * `document_schema.md` changelog and `quality_score` fix; docs-site page marked unreleased.
* **Here (after `scripts/revendor_shared.sh`):**
  * The vendored schema, `para_licenses.py` and `tests/test_para_licenses.py`.
  * `test_heavy_engine_licence_is_recorded_truthfully` now pins MIT with nothing unknown, and fails against the old
    `para_licenses.py`.
  * The "not ranked yet" and "hub follow-up" text is removed from `para_config.txt`,
    `requirements_digital_docling.txt`, the README and `digital_ir.py`.
  * `CONTRIBUTING.md` Unreleased; [`plans/18.plan.md`](plans/18.plan.md) §0f.
* **alto-postprocess:** `docs/text_inputs.md`, `text_split.py` and `document_hook.py` no longer say this converter reads
  a DOCX as one page or has no column detection or mojibake guard.
* `pytest -m "not slow"`: 1016 passed, 19 environment-only skips (+3: the new licence cases); `ruff` clean; fixtures
  `--verify` clean. **Not pushed: files delivered in chat.**

## 2026-09-26: AMČR baseline (atrium-project#67) — every digest+plan pair refreshed

* **What arrived (motyc, 2026-09-26):** [atrium-project#67](https://github.com/ufal/atrium-project/issues/67), AMČR's
  bucket for every open issue, and five comments here: #10 16:11 (the born-digital path for the pilot), #24 16:13 and
  #25 16:14 (stop: OCR stays outside the tool repositories), #13 16:18 (close as completed), #18 16:19 (close with the
  release that carries `c3575f5`). **Adopted by ÚFAL as binding.**
* **Earlier the same day (K4TEL):** #10 05:30 (steps 1–2 of the 09-24 plan done), #18 05:29 (all four tasks done) and
  16:03 (the freeze made traceable in every repository; MANIFEST 17 → 19). `test` = `79b857d`: the freeze files
  (`atrium_document.schema.doc-schema-v1.json`, `tests/test_schema_freeze.py`), README and `CITATION.cff` naming
  `doc-schema-v1`, and the re-vendored freeze test (blob `7c35fbf1`) that turned para-drift green again.
* **Dev logs:**
  * `10.*` ✅ **finish** — plan §12: `api-digital` (W1), every AMČR born-digital type incl. DOC/XLS via LibreOffice
    (W2), one reader and one quality model with alto-postprocess (W3; needs one additive origin-check rule so
    alto-postprocess may score born-digital lines), the OCR hand-off into the same record (W4; the page-subset merge
    is missing), TEITOK from `lines[].bbox` + `pages[].canvas` for nlp-enrich (W5), keep refusing OCR-layer PDFs (W6),
    Docling/MinerU here as Phase 6 (W7). The OCR-engine choice and flexiconv for the long tail are struck.
  * `13.*` 🔒 **close-out** — leftovers: one LLM input/client → #67 R7; token-cost bake-off → hub #22; `--detail`,
    reserved cues, JSON→TEITOK back-projection, `entities[].translation_en` → a new "Deferred record extensions" issue
    (body in chat).
  * `18.*` 🔒 **close-out** — close with the release carrying `c3575f5` (proposed v0.8.0); plan §0g.
  * `11.*` ⏸️ **defer** — with hub #22; K4TEL's 09-25 status added.
  * `24.*`, `25.*` ⛔ **stop** — close as not planned; olmOCR → AMČR's backup-OCR evaluation; the Jan Švec contact kept
    for shared evaluation data.
* **Found:** `llm_client_shared.py:1248,1319` returns the path named after the filename-derived id while `finalize()`
  writes under the seed's id, so the API (`service/api.py:351-398`) returns an AMČR seed unenriched when the two ids
  differ; `/extract_keywords_text` hardcodes `doc_id="inline_text"` (`service/api.py:516`). Read, not reproduced —
  hub #67 plan §B.

  **Not pushed: files delivered in chat.**

## 2026-09-30 (after the meeting): this repository becomes the born-digital converter (#29); `api-digital` (#28)

* **What arrived:** the meeting with AMČR renamed this tool's service `/reformat` (the report's §2.2 row) and gave its
  keyword code to a new repository (nlp-enrich#40, 13:50); K4TEL opened #29 at 13:53: *turn this repo into
  atrium-digital-born-convertor*.
* **Dev logs:**
  * `29.*` 🆕 — the design options: rename in place (recommended; the open issues and the last two releases are the
    converter's), one service `api-digital` with `/reformat`, no service-to-service calls (Temporal chains the page
    classification and the scoring), the record as the "per-page blocks", LibreOffice for DOC/XLS inside the image,
    names (spelling to confirm before the first GHCR package), what stays and what leaves, and the renderer both need
    (vendored with pins).
  * `28.*` ✍️ **rewritten** in the house format (the draft of `c83f6e2` had no evidence or status): W1–W7 under the
    post-meeting names; the service built now, beside the LLM service, with its final service id and its own spec
    asset, so the rename never trips the release gate; `source.sha512` checked against the bytes.
  * `10.*` 🔄 §13: the steps are unchanged except for the names; #10 closes with #28.
  * `11.*` 🧭 banner: follows the LLM engine to keyword-extractor (transfer once it exists).
* **Found:** the base stage copies the whole repository (`Dockerfile:66`, `COPY . .`), so the LLM images carry the
  converter and research scripts and the `digital` image the LLM code; the hub's release gate would reject a changed
  service id under the old spec asset.

  **Not pushed: files delivered in chat.**

## 2026-09-30 — atrium-project#72 round 1: the production image declared; review tools off the images
* `.github/production-image.json` (today's `api` target; the converter's files and the renderer listed as core, the
  TEITOK reader and `bbox_scale.py` as nlp-enrich's pinned copies), checked by the hub's `tools/ci/image_closure.py`.
* `.dockerignore` keeps `bench_compare.py`, `eval_metrics.py`, `sample_stratify.py`, `corpus_review.py` and
  `vocab_review.py` out of every image — no published entrypoint reaches them (checked with the hub's closure walker);
  README notes they run from a checkout.
* `.github/workflows/para-drift.yml`: `vendored-from: atrium-nlp-enrich` — the pin tests now also compare the copies
  with nlp-enrich's `test` in CI. `docker.yml` names today's production target and what replaces it (#28, #29).
* Revendored the three declared-rename files. Tag draft: `v0.9.0`. **Not pushed: files delivered in chat.**

## 2026-10-04 — v1.1.0-beta prepared: `/reformat` + `/describe` (#2), the LLM code out (#1), the format matrix (#4 W2)

* **What arrived:** motyc's 2 Oct questions on #2 (the identity; whether `ocr_text_layer` refuses a whole document or
  flags pages; how the re-OCR'd pages are merged). The maintainer's decisions (4 Oct):
  * a **separate `/describe`** endpoint, so that `/reformat` stays a pure converter (the 30 Sep agreement);
  * the record plus a per-page report;
  * full scope: the API, the LLM removal and DOC/XLS/ODT/ODS/XLSX/RTF;
  * the release number **v1.1.0-beta**;
  * the page-classification and ocr-postprocess changes `/describe` needs are in scope too.
* **Code (working tree):**
  * `service/api.py` is rewritten as `atrium-digital-convert` (`previous=atrium-llm-enrich`).
  * `POST /reformat`: the record, Markdown on request, `paradata`; no calls.
  * `POST /describe`: the record plus `pages[]` (`text_layer`, `category`, `quality`, `route` nlp/ocr/htr/none,
    `layout`, `text`), `summary` and `stages[]`.
  * The stage clients (`service/stages.py`) never raise. Each returned record is adopted only past a guard (same
    document, the converter's fields intact). A stage that is not configured or not running is reported; the
    request still succeeds.
  * `tools/stage_stub.py` stands in for both stages (tests, compose profile `stub`).
  * The seed's `source.sha512` is checked before parsing (`source_digest_mismatch`, 422 / exit 3, registered at
    runtime until the hub's registry carries it).
  * `OCR_LAYER_DOCUMENT_SHARE` and `MAX_PAGES` became settings.
  * ODT/ODS/XLSX/RTF are read through ocr-postprocess's `text_formats.py` (vendored, SHA-pinned, in para-drift).
  * DOC/XLS go through headless LibreOffice (private profile, timeout). It is declared in `para_config.txt` and
    never logged, so a record's licence stays MIT.
* **Removed (#1):**
  * the LLM clients and engine, prompts, vocabulary tooling and data, `requirements_llm/remote`;
  * the GPU compose file, the `gpu-inference`/`vocab-*` workflows and their tests;
  * the `remote`/`llm` images.
  The transfer manifest for keyword-extract is in `digests/1.digest.md`; the files are recoverable from
  `v1.0.0-beta`.
* **Identity:**
  * images `ghcr.io/ufal/atrium-digital-convert-{api,digital}`;
  * `para_config.txt` (`digital-convert`, v1.1.0-beta), CITATION, compose, `.env.example`;
  * `.github/production-image.json` (closure check clean: 15 core, 8 shared, 3 vendored);
  * docker/security/para-drift/dependabot updated;
  * README, CONTRIBUTING and `service/README.md` rewritten.
* **Adjacent stages (same round, their own working trees):**
  * page-classification `v1.9.2-beta` (drafted as `v1.10.0-beta`): `pages` on `/predict_document`, categories under the record's own labels
    (`page_index`), `page_label`;
  * ocr-postprocess `v1.9.0-beta`: `POST /score_record` with `document_hook.write_scores()`;
  * the hub: `SCORING_FIELDS` in the canonical `atrium_document.py` (a scoring merge is noted, adds no row, and is
    stamped `contribution: "scoring"`; the fan-in and `e2e_assert.py` read it); re-vendored with
    `revendor_shared.sh`, which gained a sibling-owned row for `text_formats.py`; `k8s_deployment.md`, the schema doc
    and the docs site updated.
  * Live smoke: the real page-classification and ocr-postprocess apps (stand-in models) plus `/describe` on a PDF
    labelled `i, ii, 1, 2` routed `nlp / ocr / htr / none`; the categories landed on labels `1` and `2`.
* **Dev logs:**
  * `2.*` ✍️ rewritten for the two endpoints, the report, the route rules and the stage contract;
  * `1.*` 🔄 executed, with the transfer manifest;
  * `4.*` 🧭 banner: W1/W2/W6 land in v1.1.0-beta.
* **Checks:**
  * digital-convert 751 passed, 4 skipped (docling, flexiconv, Tesseract absent); ruff clean; spec current;
    fixtures verified; closure clean;
  * page-classification 710 passed; ocr-postprocess 2005 passed; hub 641 passed;
  * the canonical originator test passes in all six repositories (73 each);
  * a real LibreOffice DOC/XLS round trip.
  * One hub test fails locally only: Table B against keyword-extract's ledger, until keyword-extract#2.
* **Found:**
  * **The release gate.** `v1.0.0-beta` is the repository's only release and a full one carrying the LLM spec, so
    `openapi-compat` fails until it is marked a **pre-release** (the push-time job as well as the tag). Maintainer
    step.
  * **`blocks.lines.program`.** After ocr-postprocess scores a digital record, the merged block's `program` is
    `ocr-postprocess` (rule 4), which the fan-in check would have read as a mixed plane. Hence the stamp's
    `contribution: "scoring"`, honoured by `merge_document_records()` and `e2e_assert.py`.
  * **The original program still stamped in some places.** `bench_compare.py` still writes paradata as program
    `llm-enrich` (a research tool, #3, left as it is).

  Files delivered in chat; pushed by the maintainer the same day as `20dabd8` and `b1cbb0c`.

## 2026-10-04 (evening) — `test` re-read: the image smoke test's log check, stale references
* **`test` heads:**
  * digital-convert `b1cbb0c` (the code in `20dabd8`, the shared module in `b1cbb0c`);
  * page-classification `41c84ca` (as `v1.9.2-beta`), ocr-postprocess `648aae7`, hub `fc06875` (= `v1`);
  * nlp-enrich `5065929`, keyword-extract `dffe368`, translator `561d4a6` carry the re-vendored shared module.
* **Docker api smoke (red on `b1cbb0c`).**
  * After the container turned healthy, the hub's probe found no log record in the agreed
    `%(asctime)s %(levelname)s %(name)s %(message)s` shape at the default `LOG_LEVEL` (atrium-project#61).
  * uvicorn's lines have their own format, and a clean start of this service logged nothing else; the LLM service
    used to log its warmup.
  * **Fix:** `lifespan` logs one INFO record on a clean start: the version, whether LibreOffice was found, and the
    `/describe` stages configured (their names, never their URLs).
  * `tests/test_startup_log.py` (4) runs the real lifespan and matches the formatted record against the probe's
    pattern. Without the fix, 3 of them fail.
  * Live, `python -m service.api`:
    * printed the line;
    * served the committed spec, and `/info` `openapi_sha256` matched it;
    * exited 143 on SIGTERM;
    * with `LOG_LEVEL=WARNING`, did not print the line.
* **API Meta-Contract `openapi-compat` (red):** expected until `v1.0.0-beta` is marked a pre-release (entry above).
* **Stale references fixed:**
  * page-classification's release is `v1.9.2-beta`, not the drafted `v1.10.0-beta`: `tools/stage_stub.py`, this
    file, `digests/2`, `plans/2`;
  * tag `v1.0.0-beta` is commit `31534d5`, not `770ab3f`: `digests/1`, `digests/2`, `plans/1`;
  * `service/__init__.py` named the old service;
  * three converter docstrings pointed at `llm_client_shared.py` / `llm_utils.py` as if they were still here.
* **CONTRIBUTING:** the v1.1.0-beta row now also names `MAX_CONCURRENT_JOBS`, the two reader caps, the
  `MAX_UPLOAD_MB` default (10 → 50) and the startup record.
* **Checks:** a clean export of `test` with these files: 744 passed, 15 skipped (11 of them need an nlp-enrich
  checkout beside it); ruff clean; spec current.

  Files delivered in chat; pushed by the maintainer as `f64c6df`.

## 2026-10-05 — Alignment with the rest of the ecosystem
* Part of the hub's 2026-10-05 sweep of the eight ATRIUM repositories:
  * `text_formats.py` re-vendored from atrium-ocr-postprocess (docstrings now name this repository as the owner of
    the mirrored thresholds) with its new pin in `tests/test_vendored_reader_parity.py`. Push it together with
    ocr-postprocess: para-drift compares against ocr-postprocess's `test`.
  * `.github/workflows/release.yml`: the images are tagged without the leading `v` (`:1.1.0-beta`), as the README
    says; the header comment said `:v1.1.0-beta`.
* Elsewhere: nlp-enrich's and llm-enrich's READMEs and the hub's docs now say the LLM code went to
  atrium-keyword-extract and that this repository reads eight formats.

  Files delivered in chat; pushed by the maintainer as `f64c6df`.


## 2026-10-05 — `pages[].text_layer` in the record; `source_digest_mismatch` canonical; the W4 hand-off re-vendored
* **Why:** both were proposed on atrium-project#71 after v1.1.0-beta and taken in this round without new issues: a
  route step should read the text-layer verdict as a value, not from `needs_ocr_reason`'s prose; and the reason code
  AMČR accepted on #2 belongs in the shared registry, so every service publishes it.
* **Code:**
  * `api_util/digital_ir.py` holds `TEXT_LAYER_GARBLED`, `TEXT_LAYERS` and `text_layer_of()` (moved from
    `digital_report.py`, which imports them), so the record and `/describe` share one verdict.
  * `digital_to_json._page_rows()` writes `text_layer` on every page; the hub schema declares it as a closed enum.
  * `assess_page()` flags a prior-OCR page even when none of its words became a line (all table cells), so
    `needs_ocr` is exactly the `garbled`, `ocr` and `none` pages.
  * `service/stages.py`: the `/describe` guard holds `text_layer` (`CONVERTER_PAGE_FIELDS`).
  * `service/api.py`: the `REASON_CODES.setdefault(...)` registration is gone; the code comes from the vendored
    `service/atrium_service.py` with the same wording and status (the spec's reason table is unchanged).
* **Shared modules (re-vendored, hub round of 2026-10-05):** the OCR hand-off per page (W4; ocr-postprocess
  `v1.9.1-beta` replaces a flagged page's lines and leaves the rest of this record alone), `text_layer` in the schema
  and the dc grant, `source_digest_mismatch` in the registry. `service/openapi.json` regenerated: the record schema
  gains `text_layer`; compatible with `v1.1.0-beta`.
* **Tests:** `test_digital_to_json.py` (+3: every page's `text_layer` and the `needs_ocr` invariant, the
  table-only OCR page, the garbled fixture's record), `test_describe.py` (guard), `test_api_contract.py` (the
  canonical code). Full suite 774 passed, 4 skipped.
* **Docs:** README, `service/README.md`, `digital_born/README.md`, CONTRIBUTING row; `para_config.txt` and
  `CITATION.cff` at `v1.1.1-beta`.

  Files delivered in chat; pushed by the maintainer as `fe494cd` and released as `v1.1.1-beta`.

## 2026-10-05 (evening) — Issue logs refreshed after the two releases
* **Pairs:**
  * #1 and #2: rewritten for `v1.1.0-beta` / `v1.1.1-beta`. The hub E2E re-runs of 11:40 UTC cover the
    `v1.1.1-beta` code (born-digital 37304422187; the dispatch on `-digital:latest`, 37305992407, too). The 24-file
    list is now on atrium-keyword-extract#2 (11:37 UTC).
  * #4: a W-items table. W1–W4 and W6 are released; W5 (nlp-enrich) has no issue; W7 stays with atrium-project#22.
    The 2026-10-02 evidence table is brought to `2040ce1`. `/describe` is named as the one route that calls other
    services.
* **Docs** (in `2040ce1`):
  * version numbers in `api_util/digital_report.py` and `service/README.md`: `v1.2.0-beta` → `v1.1.1-beta`, and
    ocr-postprocess `v1.10.0-beta` → `v1.9.1-beta`;
  * the `v1.1.1` row of CONTRIBUTING;
  * the issue link in `api_util/layout_md.py` (→ #4).
* **Not a file:** the `v1.1.1-beta` release notes still cite ocr-postprocess `v1.10.0-beta`.
* **DEVLOG:** the 2026-10-04/05 entries name the commits that shipped (`f64c6df`, `fe494cd`).

  Files delivered in chat; the maintainer pushed the pairs as `0b10e07` (#4) and `d9a3b1b` (#1, #2).

## 2026-10-07 — The OCR originator's name in `needs_ocr_reason`
* `api_util/digital_to_json.py`: the reason for a prior OCR layer names `ocr-postprocess` (was `alto-postprocess`);
  the same wording in the docstrings of `api_util/digital_docling.py` and `api_util/doc_to_visual_md.py` (#2, plan
  H.6). References to the predecessor's history stay.
* **For #2 and #4:** the hub's digital E2E has its `-api` lane (Case 7); H.1 is met once it is green on `latest`.
  ocr-postprocess has the W4 API tests (H.4).
* **Checks:** `test_digital_to_json.py`, `test_describe.py`, `test_digital_formats.py`, `test_doc_to_visual_md.py`:
  121 passed; ruff clean.

  Files delivered in chat.

---
_Timeline index refreshed 2026-09-26 (AMČR baseline entry and header); earlier 2026-09-07 against live `test`/`main` HEAD, the `CONTRIBUTING.md` changelog table, and
open-issue state via the GitHub API; header and the 2026-09-24 entries refreshed 2026-09-24 against `test` `122915c`, then `c1ad762`, and after the push against `08dff48` (with the 09-07 → 09-17 gap filled). Nothing removed from the issues themselves (per hub #29); this file is a
derived reading aid in `agent_dev_logs/`._
