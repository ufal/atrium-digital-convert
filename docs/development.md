# Development

How to set up a checkout and run the checks, how the code is laid out, where the repository came from, and where
the maintainers keep their working records. For contributors.

## Set up and run the checks

To run the converter from a checkout, see [usage.md](usage.md#set-up-a-checkout). To work on it and run the
tests:

```bash
pip install -r requirements.txt -r requirements_digital.txt -r requirements-test.txt
pytest -q                                                    # the whole suite (no LibreOffice needed: DOC/XLS use a fake soffice)
ruff check . && ruff format --check .
python tests/fixtures/digital/make_fixtures.py --verify      # the generated PDF/DOCX fixtures are current
python atrium_openapi.py export --app service.api:app --out service/openapi.json   # after an API change
python atrium_openapi.py check  --app service.api:app --spec service/openapi.json
python ../atrium-project/tools/ci/image_closure.py --repo-root . --worktree        # the image's file list
```

How to contribute, the code and test conventions, the release procedure and the release history are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## How the code is laid out

The converter is built in layers. Each module's docstring carries the detail.

1. **Layer A, the readers.** `api_util/digital_pdf.py` (pdfplumber and pypdfium2), `digital_docx.py`
   (python-docx and lxml), `digital_docling.py` (opt-in), `digital_text.py` (ODT, ODS, XLSX and RTF, through the
   vendored `text_formats.py`) and `digital_legacy.py` (DOC and XLS, through headless LibreOffice). Each writes the
   internal representation in `digital_ir.py`.
2. **Layers B–D.** `api_util/digital_to_json.py` normalises the text and checks that it decodes. It then builds
   the record (`atrium_document.DocumentRecord`) and runs the output gate: field survival and the JSON Schema.
3. **The per-page assessment.** `api_util/digital_report.py` is pure and deterministic: routes, quality
   summaries, layout counts.
4. **The service.** `service/api.py` serves `/reformat`, `/describe` and the shared `/info`, `/health` and
   `/ready`. `service/stages.py` holds the page-classification and ocr-postprocess clients and the record guard.
   `tools/stage_stub.py` stands in for both stages in tests and local demos.
5. **The renderer.** `api_util/json_to_md.py`, `layout_md.py` and `xml_to_md.py` turn a record, or TEITOK or
   ALTO, into annotated Markdown. atrium-keyword-extract vendors them.
6. **The document types, offline (#8).** `tools/build_document_types.py` generates
   `decisions/document_type.v2.json`, AMČR's document types with one question set per input, from
   atrium-keyword-extract's vocabulary build; keyword-extract vendors the list. `tools/decide_documents.py` asks a
   decision model about PDF pages and documents through `decision_client.py`, vendored from
   atrium-page-classification and pinned by `tests/test_vendored_decision_parity.py`. Neither tool is in an image
   or the service ([usage](usage.md#document-types-for-decision-models-offline)).

## History of the repository

- **Before:** the code was `atrium-llm-enrich`, an LLM keyword stage that also held the born-digital converter
  (from its v0.6.0).
- **`v1.0.0-beta`:** the first release of the new repository `atrium-digital-convert`. It held
  `atrium-llm-enrich` 0.9.0's code, with that tool's service id, program id (`llm-enrich`) and images.
- **`v1.1.0-beta`:** the born-digital stage under its own name. The keyword stage moved to
  [atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract)
  ([#1](https://github.com/ufal/atrium-digital-convert/issues/1)). The service id became
  `atrium-digital-convert`, and the spec declares the former id (`x-atrium-service-previous`). The images became
  `ghcr.io/ufal/atrium-digital-convert-{api,digital}`. The record's program id is `digital-convert`.
- **The removed keyword and LLM code** is recoverable from tag `v1.0.0-beta`. The transfer manifest is in
  [`agent_dev_logs/digests/1.digest.md`](../agent_dev_logs/digests/1.digest.md).

Each release, version by version, is in CONTRIBUTING's
[Release History](../CONTRIBUTING.md#release-history).

## Working records

[`agent_dev_logs/`](../agent_dev_logs) is the maintainers' working record. It is not documentation for users or
operators, and no other page links to it: they cite the issue instead. It holds:

- `DEVLOG.md`, the dated log of each round of work;
- `digests/`, one digest per issue: its status, the decisions taken and what is left;
- `plans/`, the plan written for each issue;
- `issues/`, exports of issue threads.

The hub lists every ATRIUM repository's working records on its
[development history](https://ufal.github.io/atrium-project/development-history/) page.
