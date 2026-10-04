# 🤝 Contributing to ATRIUM digital-convert

Thank you for your interest in contributing! This repository is the **born-digital stage** of the
ATRIUM pipeline: it reads born-digital documents (PDF, DOCX, ODT, ODS, XLSX, RTF, and DOC/XLS
through LibreOffice) into `atrium_document` records, on the command line
(`api_util/digital_to_json.py`) and as the HTTP service `api-digital` (`service/api.py`:
`POST /reformat`, `POST /describe`). Until v1.0.0-beta it was `atrium-llm-enrich`; the keyword
stage it held moved to [atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract)
([#1](https://github.com/ufal/atrium-digital-convert/issues/1)).

This document describes the development workflow, code conventions, and rules for
contributors. ATRIUM-wide conventions (branching, commit types, the test/lint standard) are
identical across all repositories; anything repo-specific is called out explicitly.

## 📦 Release History

| Version         | Highlights                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Status      |
|:----------------|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:------------|
| **v1.1.0-beta** | **The born-digital stage, under its own name** (#1, #2, #4; atrium-project#72). Service id `atrium-digital-convert` (the rename is declared in the spec, `x-atrium-service-previous`), images `ghcr.io/ufal/atrium-digital-convert-{api,digital}`, program id `digital-convert`. **`POST /reformat`** (file + optional AMČR seed → record, Markdown on request, `paradata`; calls no other service) and **`POST /describe`** (the same conversion + a per-page assessment: text layer, page type from page-classification, readability from ocr-postprocess, route `nlp`/`ocr`/`htr`/`none`, layout, text; the stages are called only when `PAGE_CLASSIFICATION_URL`/`OCR_POSTPROCESS_URL` are set, and their records are adopted only past a guard). The seed's `source.sha512` is checked before parsing (`source_digest_mismatch`). **Formats:** ODT, ODS, XLSX and RTF through atrium-ocr-postprocess's reader (vendored, SHA-pinned), DOC and XLS through headless LibreOffice (declared, never logged into the licence). New settings `OCR_LAYER_DOCUMENT_SHARE`, `ROUTE_TRASH_SHARE`, `MAX_PAGES`, `LIBREOFFICE_TIMEOUT_S`, `STAGE_TIMEOUT_S`. **Removed:** the keyword/LLM code, the vocabulary tooling, their workflows and the `remote`/`llm` images (recoverable from `v1.0.0-beta`; transfer manifest in `agent_dev_logs/digests/1.digest.md`). Released after `v1.0.0-beta` was marked a pre-release, so the spec starts a fresh baseline.                                                                                                                                                                                                                                                                                                        | Pre-release |
| **v1.0.0-beta** | First release from the new repository `atrium-digital-convert` (atrium-digital-convert#1; atrium-project#72); the code is `atrium-llm-enrich` 0.9.0's, and its service id, program id (`llm-enrich`) and images are unchanged for now. **Run provenance (atrium-project#71):** every success carries its `CreateAction` as `paradata` (no paradata file is written), `run_uuid` stamps every block, an AMČR seed is checked against the seed profile, `ATRIUM_RUN_AGENT` names the operator. Re-vendored record contract with the program successor map (`llm-enrich` → `keyword-extract`); regenerated `openapi.json`; vocabulary metadata refreshed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Pre-release |
| **v0.9.0**      | Typed OpenAPI contract and release asset; every limit a setting (cut replies never used, 413/502); `--detail standard`/`minimal` Markdown profiles (atrium-project#70); CC0 vocabularies; arbitrary-UID images; review/research tools out of the images; production-image declaration; TEITOK copy parity checked in CI.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Pre-release |
| **v0.8.0**      | TEITOK/flexiconv input enrichment restored and the OCR-path `Trash`-line leak fixed (defect V-1); digital-born PDFs/DOCX now route through split light engines (`digital_pdf.py`/`digital_docx.py`) with layout cues and an optional Docling engine (#18); the document record schema frozen as `doc-schema-v1`; and a seed/doc_id mismatch that silently dropped `enrichment` from AMČR-seeded records is fixed (atrium-project#68).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release |
| **v0.7.0**      | **`entities[].pid` is finally populated.** The schema calls that block "the ARIADNE/GoTriple hook" and nothing in the ecosystem had ever written it — every record produced so far carried null. `vocab_manager.concept_index()` / `resolve_pid()` resolve a label (lemma first, then surface) against the harvested AMCR + TEATER artifacts, and `llm_client_shared.entity_pid_rows()` merges only the `pid` field, so nlp-enrich's morphology and spans survive. Only `amcr` and `aat` are filled — `wikidata`/`geonames` stay `None`, because a hook that lies is not a hook — and a row is emitted only when something resolved, so re-runs don't re-attribute nlp-enrich's block for no content. Deliberately a lookup, not a prompt field: putting 707 Getty URIs into `nested_keep` would inject them into every system prompt and change model behaviour. **New SKOS view of the harvest**: `union.skos.ttl` (5.2 MB, the sources' own URIs, no minted identifiers) built by `vocab_build.py --from-flat --skos` from the raw harvest rather than the deduplicated nesting — 624 label collisions would otherwise vanish. The harvester was also under-reading its sources: all four SKOS mapping relations are now captured (was `exactMatch` only), and TEATER's `quotes[]` Getty URLs are harvested as `dcterms:source` citations, never as mappings. AMCR's `hierarchie_vyse` is emitted as `skos:related`, not `skos:broader` — all 1,176 edges cross heslář boundaries. Plus SKOS (#51) and RO-Crate (#54) vendored, `$PORT`/`HOST` (#58), `.env.example` + env contract (#60), logging contract (#61), the Dockerfile security layer, and `flexiconv` pinned to `@v0.3.10` (#62) after resolving to whatever the default branch happened to be. | Pre-release |
| **v0.6.3**      | Fixes the born-digital `enrichment`-block parity bug (CLI and API now agree on when a consulted-but-empty result still contributes a block) — closes `atrium-project#49` and this repo's #18. Adds Docker `HEALTHCHECK` + `SIGTERM`/graceful-shutdown handling for Kubernetes (issue #55).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release |
| **v0.6.2**      | Vocabulry and Prompts aligned with the `nlp-enrich` repo. Supplementary materials and scripts expanded. Fixes for GHA docker applied. Vocabulary files updated.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Pre-release |
| **v0.6.1**      | Vocabulry expanded and aligned with the `nlp-enrich` repo. Supplementary materials and scripts included. Work-in-progress in terms of the vocabulary definition.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Pre-release |
| **v0.6.0**      | **First release of the `digital-convert` originator** — `api_util/digital_to_json.py`, the four-layer born-digital PDF/DOCX converter whose decode-sanity gate catches a text layer that extracts *successfully and wrongly* (CP1250 bytes read as CP1252), reporting `needs_ocr` rather than rewriting text that `source.sha256` still describes. **DOCX tables no longer lose their text:** `extract_docx()` walks paragraphs and tables in document order using the in-order walker `docx_to_md.py` already had, so cell text reaches `lines[]` and `cells[].group_id` joins back to it — previously every table's text existed only in `tables[].cells[].text`, invisible to `json_to_md`, nlp-enrich and the translator, with the documented join resolving to nothing; degenerate grids are now omitted rather than raising Layer D on the converter's own output. Also the AMCR/TEATER vocabulary subsystem aligned with nlp-enrich, a nightly that runs the full suite instead of collecting nothing, and the re-vendored `atrium_document.py`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Pre-release |
| **v0.5.2**      | Re-vendored `atrium_document.py`: `DocumentRecord` now **inherits `doc_id` from the baseline** instead of overwriting it with the caller's derivation. Fixing the `Path.stem` derivation stopped *this* tool forking a record, but `DocumentRecord.__init__` still stamped whatever id its caller passed over the key the baseline arrived with, so no tool could opt out of the class by being careful at one call site. Also in this window: `canonical_doc_id()` replaced `Path.stem` in `openrouter_client.py` / `ollama_client.py` and made `teitok_read.doc_id_from_path()` a thin wrapper around it, `/enrich` reached parity on the `document_json` part, digital-born probe scripts were added under `digital_born/`, and `tests/test_document_originators.py` -> canonical shared set.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Pre-release |
| **v0.5.1**      | End-to-end GHA pipeline for JSON input-output by `atrium_document` standard is refined, and tested for the draft JSON schema design. Template updated.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Pre-release |
| **v0.5.0**      | Major GHA workflows update with references to `@v1` on hub repo. Updated template scripts. Added atrium_document dtandard for input-output of JSONs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Pre-release |
| **v0.4.0**      | OpenAPI standards applied - draft. `atrium_document` draft added for cross-repo JSON expansion. Added PDF and DOCS to MD convertors draft. Edited GHA release workflow.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Pre-release |
| **v0.3.0**      | Updated dependencies. Added DU code. Added <PDF/DOCX>-2-MD transformers draft. Updated tests coverage. Refreshed issue logs (digests+plans)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release |
| **v0.2.0**      | Added new files according to plan. Added new GHA files. Added Document Understanding draft scripts. Added new tests. Fixed according to Fable review.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release |
| **v0.1.0**      | Initial repo: LLM engine copied from `atrium-nlp-enrich`, NameTag/UDPipe dropped, `openrouter_client.py` + `ollama_client.py` + `api_util/xml_to_md.py` added.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Pre-release |

**Versioning rules (enforced by CI):** the `[tool] version` in `para_config.txt`, `version:` in
`CITATION.cff`, and the git tag MUST agree (prefix-tolerant: `v0.1.0` in `para_config.txt` ==
`0.1.0` in `CITATION.cff`) — `security.yml` fails the build otherwise. Update `date-released:`
in `CITATION.cff` to the actual release date on every version bump.

---

## 🏗️ Project Contributions & Capabilities

See [README.md](README.md) for full usage; in brief, the code is layered (the converter's module
docstrings carry the detail):

1. **Layer A, readers**: `api_util/digital_pdf.py` (pdfplumber + pypdfium2),
   `digital_docx.py` (python-docx + lxml), `digital_docling.py` (opt-in), `digital_text.py` (ODT,
   ODS, XLSX, RTF through the vendored `text_formats.py`), `digital_legacy.py` (DOC/XLS through
   headless LibreOffice), all into the internal representation in `digital_ir.py`.
2. **Layers B–D**: `api_util/digital_to_json.py`: normalisation and decode sanity, the record
   (`atrium_document.DocumentRecord`), and the output gate (field survival + JSON Schema).
3. **The per-page assessment**: `api_util/digital_report.py`, pure and deterministic (routes,
   quality summaries, layout counts).
4. **The service**: `service/api.py` (`/reformat`, `/describe`, the shared `/info`, `/health`,
   `/ready`) and `service/stages.py` (the page-classification and ocr-postprocess clients, the
   record guard). `tools/stage_stub.py` stands in for both stages in tests and local demos.
5. **The renderer**: `api_util/json_to_md.py`, `layout_md.py`, `xml_to_md.py` (record or
   TEITOK/ALTO → annotated Markdown), which atrium-keyword-extract vendors.

---

## 🌿 Branches & Environments

| Branch | Environment          | Rule                                                                          |
|--------|----------------------|-------------------------------------------------------------------------------|
| `test` | Staging              | Base for all development. Always branch from `test`.                          |
| `main` | Stable / Integration | Merged exclusively by a human reviewer. Do not open PRs directly into `main`. |

```text
test  ←  feature-<name>
test  ←  bugfix-<name>
main  ←  (humans only, after test stabilises)
```

### 🏷️ Branch Naming

| Type           | Pattern          | Example                    |
|----------------|------------------|----------------------------|
| New feature    | `feature-<name>` | `feature-odt-tables`       |
| Bug fix        | `bugfix-<name>`  | `bugfix-pdf-page-labels`   |
| Hotfix on main | `hotfix-<name>`  | `hotfix-seed-digest`       |

---

## 🔁 Contributor Workflow

1. **Create an issue** (or find an existing one) describing the problem or feature.
2. **Branch from `test`:**
```bash
   git checkout test && git pull origin test
   git checkout -b feature-<name>
```
3. **Implement** following the code conventions below.
4. **Run the fast checks** (see Testing) before every commit.
5. **Open a Pull Request** targeting `test`. Use a **Draft PR** while work is in progress.

---

## 📋 Pull Request Format

Every PR must include:

* **Issue link:** `Closes #<number>` or `Refs #<number>`
* **Motivation:** why the change is needed
* **Description of change:** what changed and how
* **Testing:** what was run, what passed, what could not be executed (and why)

**Do not open PRs into `main`** — merging into `main` is the maintainers' responsibility.

---

## ✏️ Commit Messages

Format: `[type] concise description of what changed`

| Type       | When to use                           |
|------------|---------------------------------------|
| `add`      | Added content (general)               |
| `edit`     | Edited existing content (general)     |
| `remove`   | Removed existing content (general)    |
| `fix`      | Bug fix                               |
| `refactor` | Refactoring without behaviour change  |
| `test`     | Adding or updating tests              |
| `docs`     | Documentation only                    |
| `chore`    | Build, dependencies, CI configuration |
| `style`    | Formatting, no logic change           |
| `perf`     | Performance optimisation              |

---

## 🧪 Code Conventions & Testing

### Code conventions
* **Comments:** short and informative; add one when the function name doesn't fully explain intent.
* **Argument types:** give every function argument a default type (`int`, `list`, …).
* **Console flags:** every new CLI flag ships with a `help=` message.
* **Settings:** every limit or setting is declared in `tool_limits.py` and listed in
  `.env.example` and the `## Limits` table of `service/README.md`; `tests/test_limits_contract.py`
  and `tests/test_env_contract.py` fail when one of the three is missing.
* **An API change** regenerates `service/openapi.json` in the same commit
  (`python atrium_openapi.py export --app service.api:app --out service/openapi.json`). Removing an
  operation or a reason code is a breaking change the release gate refuses.
* **The production image** is declared in `.github/production-image.json`: a new module the
  service imports goes into its `core` list (`python ../atrium-project/tools/ci/image_closure.py
  --repo-root . --worktree`).
* **Record ownership:** the converter writes only `digital-convert`'s fields; a field another stage
  owns is never filled here (`atrium_document.BLOCK_FIELD_OWNERS`). `/describe` adopts a stage's
  record only past the guard in `service/stages.py`.
* **Shared files have one owner each:**
  * the hub-canonical modules (`atrium_*.py`, `para_licenses.py`, `service/atrium_service.py`,
    the schema files, …) come from `ufal/atrium-project`'s `docs/templates/shared/`, see below;
  * `text_formats.py` is atrium-ocr-postprocess's reader, pinned by
    `tests/test_vendored_reader_parity.py` (the names it imports from `tool_limits.py` keep
    ocr-postprocess's spelling);
  * `api_util/{teitok_read,flexiconv_convert,bbox_scale}.py` are atrium-nlp-enrich's, pinned by
    `tests/test_vendored_teitok_parity.py` (plus `requirements_flexiconv.txt`, the flexiconv
    fixtures and one writer sample).

  Never fork their logic locally: change the owner, then re-vendor with the hub's
  `scripts/revendor_shared.sh` and update the pin. This repo only *reads* TEITOK: the writer lives
  in nlp-enrich.

### Minimum checks before every commit
```bash
python -m compileall -q .                 # 1. compiles
pre-commit run --all-files                # 2. ruff (shared ruff.toml)
pytest -m "not slow" --tb=short           # 3. fast lane — no models, no GPU, no network
```

### Running the test suite
The fast lane requires **no ML models, GPU, network or LibreOffice** (the DOC/XLS tests run a
fake `soffice`; `/describe` is tested against `tools/stage_stub.py` on a local port):
```bash
pip install -r requirements.txt -r requirements_digital.txt -r requirements-test.txt
pytest -m "not slow" --tb=short                          # before every commit
pytest -m "not slow" --cov=. --cov-report=term-missing   # with coverage
python tests/fixtures/digital/make_fixtures.py --verify  # the generated fixtures are current
```
Tests that need Docling's models or the network must be marked `@pytest.mark.slow`; they run in
[`.github/workflows/scheduled-smoke.yml`](.github/workflows/scheduled-smoke.yml).

### Linting
Ruff is the ATRIUM standard. Run `ruff check --config ruff.toml .` before opening a PR — this
repo's `ruff.toml` (line-length 100, `E`/`F`/`W`/`I`/`B`) matches `atrium-nlp-enrich`'s, not the
hub's 120-column default, since the two repos share vendored files. The vendored
`text_formats.py` is excluded from `ruff format` (it is formatted by its owner).

---

## 🔗 Shared ("drop-in") code
The `atrium_*.py` modules, `para_licenses.py`, `service/atrium_service.py`, the record schema and its
frozen copy, and the shared tests are **canonical** in `ufal/atrium-project/docs/templates/shared/`
(listed in its `MANIFEST.json`) and copied verbatim into each tool repository.

* **Do not fork their logic locally:** edit the canonical copy in the hub, then re-sync with the
  hub's `scripts/revendor_shared.sh` (which also refreshes the sibling-owned copies, such as
  `text_formats.py`).
* **CI drift-check:** [`para-drift.yml`](.github/workflows/para-drift.yml) fails the build if this
  repo's copy diverges from the canonical source, and compares the sibling-owned copies with their
  owners' `test` heads.
* **A reason code the shared registry lacks** (today `source_digest_mismatch`) is registered at
  runtime in `service/api.py` beside the registry, until the hub's canonical
  `atrium_service.py` carries it.
* **Configuration:** `para_config.txt` is the only per-repo dependency: the program id, the
  version, and the licence of every component the converter may use.

---

## 📁 Repository Documentation Management

| File                | Audience        | Responsibility                                |
|---------------------|-----------------|-----------------------------------------------|
| `README.md`         | GitHub visitors | Project overview, formats, usage, quick start |
| `CONTRIBUTING.md`   | Developers      | Code conventions, branches, PRs, testing      |
| `service/README.md` | API consumers   | Endpoints, fields, errors, limits             |
| `agent_dev_logs/`   | Maintainers     | Per-issue digests and plans; the DEVLOG       |

Do not duplicate rules across files — cross-reference the canonical source.

---

## 📞 Contacts & Acknowledgements
Maintainer: **lutsai.k@gmail.com** [^1] · Developed by UFAL [^2] · Funded by ATRIUM [^3]

**©️ 2026 UFAL & ATRIUM**

[^1]: https://github.com/ufal/atrium-digital-convert
[^2]: https://ufal.mff.cuni.cz/
[^3]: https://atrium-research.eu/