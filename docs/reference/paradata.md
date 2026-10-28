# Paradata, licences and the record schema

What a run records about itself, the licence fields of a record, and the schema the record follows.

## Paradata and provenance

* **The service.** Every `/reformat` and `/describe` response carries `paradata`: the run's RO-Crate
  `CreateAction` (`atrium_rocrate`), program `digital-convert`. It names the run id, what the call
  read (the file, with its digest, and the seed record when one was sent) and the record blocks it
  wrote.
* **Stage paradata.** `/describe` also returns each stage's own paradata in `stages[]`.
* **The command line.** `--paradata-dir DIR` writes `YYMMDD-HHmmss_digital-convert.json`
  ([`atrium_paradata.py`](../../atrium_paradata.py)).

**The licence label of a record.** `provenance.license` is the most restrictive licence among the components
the run actually used, as declared in [`para_config.txt`](../../para_config.txt). It is a calculated summary of the
components, not the licence of the record: the rights in an output depend first on the source document, and they
are stated by whoever holds them (for the AMČR pilot, AMČR; agreed on 22 October 2026,
[atrium-project#6](https://github.com/ufal/atrium-project/issues/6)). For this tool:
* **The light engine and the shared reader** are MIT, BSD-3-Clause or Apache-2.0, so a record
  says **MIT**.
* **The Docling engine** adds TableFormer's CDLA-Permissive-2.0, which the shared licence table
  ranks with MIT, so a Docling record also says MIT.
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
