# Paradata, licences and the record schema

What a run records about itself, the components it lists with their licences, and the schema the record follows.

## Paradata and provenance

* **The service.** Every `/reformat` and `/describe` response carries `paradata`: the run's RO-Crate
  `CreateAction` (`atrium_rocrate`), program `digital-convert`. It names the run id, what the call
  read (the file, with its digest, and the seed record when one was sent) and the record blocks it
  wrote.
* **Stage paradata.** `/describe` also returns each stage's own paradata in `stages[]`.
* **The command line.** `--paradata-dir DIR` writes `YYMMDD-HHmmss_digital-convert.json`
  ([`atrium_paradata.py`](../../atrium_paradata.py)).

**The components a record lists.** `provenance.license_detail` lists the components the run actually used, each with
its licence, as declared in [`para_config.txt`](../../para_config.txt). None of them is the licence of the record:
the rights in an output depend first on the source document, and they are stated by whoever holds them (for the
AMČR pilot, AMČR; agreed on 22 October 2026, [atrium-project#6](https://github.com/ufal/atrium-project/issues/6)).
`provenance.license` beside the list is an older calculated field, not a licence
([Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/)). For this tool:
* **The light engine and the shared reader** are MIT, BSD-3-Clause or Apache-2.0.
* **The Docling engine** adds Docling and docling-parse (MIT), the layout model (Apache-2.0) and TableFormer's
  model weights (CDLA-Permissive-2.0, which places no terms on the results of a computation).
* **LibreOffice** (MPL-2.0) is declared but **never logged**. It converts the container format of
  a text it does not author, and AMČR accepted it on exactly that condition (#4). The conversion is
  reported beside the record instead (`reader.conversion` in the response).
* **PyMuPDF** (AGPL-3.0) is deliberately not used; see `requirements_digital.txt`.

## The record schema

The record follows [atrium_document.schema.json](../../atrium_document.schema.json), schema version
**`1.0`**, frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`).
* [tests/test_schema_freeze.py](../../tests/test_schema_freeze.py) checks this repository's schema
  against the frozen copy beside it,
  [atrium_document.schema.doc-schema-v1.json](../../atrium_document.schema.doc-schema-v1.json).
* The schema files, `atrium_document.py` and the other shared modules (`atrium_service.py`,
  `atrium_openapi.py`, `atrium_limits.py`, `atrium_vocab.py`, `atrium_rocrate.py`, …) are vendored
  from the hub. The `para-drift` workflow keeps them byte-identical, so they are never edited
  here; the hub's `scripts/revendor_shared.sh` refreshes them.

What may change after the freeze is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
