# Paradata, licences and the output schema

What a run records about itself, the components it lists with their licences, and the schema its output follows.

## Paradata and provenance

Paradata schema 3.0. What a run read and wrote is named by AMČR distribution name and SHA-512, never by a path:

* **Inputs** (`object`): the uploaded file as `original` (AMČR's name for that distribution is still open), and the
  seed, when one was sent, as `atrium/document-json`.
* **Output** (`result`): the docproc as `atr/docproc-json`. When a page needs the ATR, the ATR's docproc is the
  stored one, so this one is `stored: false`, still named by its SHA-512. An office document's rendered PDF is an
  output too, as `rendered-pdf` (a stand-in name until AMČR gives one), always `stored: false`: the docproc's pages
  and boxes are its pages, and page-classification reads it. Its digest names the PDF this run
  handed over. A rerun lays the pages out the same, so the docproc does not change, but the PDF's bytes may differ.
  The Markdown and the `/describe` report are development outputs and are not named.
* **Parameters**: the engine (`light`, `docling`), the workflow (`visual`, `textual`); for the visual workflow, the
  smallest picture a page's `graphics` count (`graphic_min_side_pt`: 24); for an office document, the pinned
  LibreOffice export options (`render`); for an XLSX or XLS, the locale LibreOffice displayed its cells in
  (`spreadsheet_locale`: `cs-CZ`).
* **Limits**: the splits the output rests on: `max_line_chars` when lines of a textual input were wrapped.
* **A failed conversion** writes no docproc, and its paradata say `action_status: failed` with the error. On the
  command line a failure after the docproc is written (the rendered PDF, the Markdown) leaves the docproc on disk.

Where it is:

* **The service.** Every `/reformat` response carries `paradata`: the run's RO-Crate `CreateAction`
  (`atrium_rocrate.action_from_paradata`), program `digital-convert`, and so does the parked `/describe`.
* **The command line.** `--paradata-dir DIR` writes `YYMMDD-HHmmss_digital-convert.json`
  ([`atrium_paradata.py`](../../atrium_paradata.py)).

Neither the docproc nor the paradata carry a run's history inside the output: no time stamp, no producer id, no
path in the docproc.

**The components a run lists.** Each run lists the components it used, each with its own licence or terms, as
declared in [`para_config.txt`](../../para_config.txt). No licence of an output is computed: the rights in an output
follow the source document, and whoever holds them states them; for the AMČR pilot, AMČR
([Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/)). For this tool:
* **The light engine and the shared reader** are MIT, BSD-3-Clause or Apache-2.0.
* **The Docling engine** adds Docling and docling-parse (MIT), the layout model (Apache-2.0) and TableFormer's
  model weights (CDLA-Permissive-2.0, which places no terms on the results of a computation).
* **LibreOffice** (MPL-2.0) converts a DOC/XLS and renders an office document for its pages; the text stays the
  native readers'. It is listed with its version whenever a run uses it, as a fact. The conversion and the
  rendering are also reported beside the output (`reader.conversion`, `reader.rendering`).
* **PyMuPDF** (AGPL-3.0) is deliberately not used; see `requirements_digital.txt`.

## The output schema

The output is docproc, validated against
[schemas/docproc/document.atrium-proposal.schema.json](../../schemas/docproc/README.md): the ATR's schema with the
changes the README there lists. digital-convert writes no ATRIUM record; ocr-postprocess builds it from the
docproc.

The record schema and the other shared modules (`atrium_document.py`, `atrium_service.py`, `atrium_openapi.py`,
`atrium_limits.py`, `atrium_vocab.py`, `atrium_rocrate.py`, …) are still vendored from the hub, because the service
contract and the seed check use them. The `para-drift` workflow keeps them byte-identical, so they are never edited
here; the hub's `scripts/revendor_shared.sh` refreshes them. `doc-schema-v1` stays the frozen, published record
schema; 2.0 is published as `doc-schema-v2` once the pilot has proven it
([tests/test_schema_freeze.py](../../tests/test_schema_freeze.py)).
