# Paradata, licences and the output schema

What a run records about itself, the components it lists with their licences, and the schema its output follows.

## Paradata and provenance

Paradata schema 3.0 ([atrium-project#84](https://github.com/ufal/atrium-project/issues/84); ARÚP's output contract
of 12 November 2026). What a run read and wrote is named by AMČR distribution name and SHA-512, never by a path:

* **Inputs** (`object`): the uploaded file as `original` (AMČR's name for that distribution is open, #84), and the
  seed, when one was sent, as `atrium/document-json`.
* **Output** (`result`): the docproc as `atr/docproc-json`. When a page needs the ATR, the ATR's docproc is the
  stored one, so this one is `stored: false`, still named by its SHA-512 (H14). The Markdown and the `/describe`
  report are development outputs and are not named.
* **Parameters**: the engine (`light`, `docling`).
* **A failed conversion** writes nothing, and its paradata say `action_status: failed` with the error (D13).

Where it is:

* **The service.** Every `/reformat` and `/describe` response carries `paradata`: the run's RO-Crate
  `CreateAction` (`atrium_rocrate.action_from_paradata`), program `digital-convert`. `/describe` also returns each
  stage's own paradata in `stages[]`.
* **The command line.** `--paradata-dir DIR` writes `YYMMDD-HHmmss_digital-convert.json`
  ([`atrium_paradata.py`](../../atrium_paradata.py)).

Neither the docproc nor the paradata carry a run's history inside the output: no time stamp, no producer id, no
path in the docproc (ask 3 of #84).

**The components a run lists.** The paradata's `components` list what the run actually used, each with its own
licence, as declared in [`para_config.txt`](../../para_config.txt), as facts. No licence of the output is computed
or recorded (D2): the rights in an output depend first on the source document, and they are stated by whoever
holds them (for the AMČR pilot, AMČR; agreed on 22 October 2026,
[atrium-project#6](https://github.com/ufal/atrium-project/issues/6);
[Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/)). For this tool:
* **The light engine and the shared reader** are MIT, BSD-3-Clause or Apache-2.0.
* **The Docling engine** adds Docling and docling-parse (MIT), the layout model (Apache-2.0) and TableFormer's
  model weights (CDLA-Permissive-2.0, which places no terms on the results of a computation).
* **LibreOffice** (MPL-2.0) is declared but **never logged**. It converts the container format of
  a text it does not author, and AMČR accepted it on exactly that condition (#4). The conversion is
  reported beside the output instead (`reader.conversion` in the response).
* **PyMuPDF** (AGPL-3.0) is deliberately not used; see `requirements_digital.txt`.

## The output schema

The output is docproc, validated against
[schemas/docproc/document.atrium-proposal.schema.json](../../schemas/docproc/README.md): the ATR's schema as
delivered, with asks 3 and 13 of #84 applied. digital-convert writes no ATRIUM record since #84; ocr-postprocess
builds it from the docproc.

The record schema and the other shared modules (`atrium_document.py`, `atrium_service.py`, `atrium_openapi.py`,
`atrium_limits.py`, `atrium_vocab.py`, `atrium_rocrate.py`, …) are still vendored from the hub, because the service
contract and the seed check use them. The `para-drift` workflow keeps them byte-identical, so they are never edited
here; the hub's `scripts/revendor_shared.sh` refreshes them. `doc-schema-v1` stays the frozen, published record
schema; `doc-schema-v2` is not tagged yet ([tests/test_schema_freeze.py](../../tests/test_schema_freeze.py)).
