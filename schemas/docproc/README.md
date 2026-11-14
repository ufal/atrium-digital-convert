# docproc schemas

Since ARÚP's output contract of 12 November 2026 ([atrium-project#84](https://github.com/ufal/atrium-project/issues/84)
§1) digital-convert writes the document's layout as docproc (`atr/docproc-json`), the format the ATR writes for
scanned pages. ocr-postprocess builds the ATRIUM record from it.

| File                                   | What it is                                                                                          |
|----------------------------------------|-----------------------------------------------------------------------------------------------------|
| `document.actual.schema.json`          | the ATR's schema as delivered (Ronald, 10 November 2026; #84 comment 1), verbatim                   |
| `document.atrium-proposal.schema.json` | the same schema with asks 3 and 13 of #84 applied; what digital-convert's docproc validates against |

The proposal is generated, never edited by hand: `python tools/docproc_proposal.py` writes it from the actual schema,
and `tests/test_docproc_writer.py` pins that the two differ only by these asks.

**Ask 3** (no run data required): `processedAt` and `pipeline` are not required; the run, the original and its
digest are the paradata's. digital-convert writes neither, nor `sourceFile` or `sourceHash` (a SHA-256).

**Ask 13** (other producers and formats):

- `pipeline` accepts any producer id (`^[a-z0-9][a-z0-9-]*(/[a-z0-9-]+)?$`; the ATR's `pipeline-e2e` fits);
- `sourceFormat` adds `odt`, `ods`, `xlsx`, `rtf`, `doc`, `xls`;
- a page may carry `textLayer` (`digital`, `garbled`, `ocr`, `none`, `blank`), `needsAtr` with `needsAtrReason`,
  and `userUnit`;
- a block may carry `lines[]`: `text`, `bbox`, `bboxPrecision` (`measured`, `derived`, `absent`), `language`,
  `flags` (digital-convert's decode verdicts `Garbage`, `Inverted`);
- a page of a flow document (DOCX, ODT, a spreadsheet) has no `width`/`height`, and its blocks no `bbox`.

Everything else is the ATR's: block ids `p{page}-b{n}`, dense `readingOrder`, `cellGrid` tables,
`metadata.routing`. digital-convert routes every page in `metadata.routing.pages[]` (`text-layer`, `atr`, `empty`)
and lists in `pages[]` only the pages that are not empty (H15). It emits `version` `1.1`.

The schema's home is ARUP-CAS/aiscr-docs-pipeline under an ARÚP identifier; ARÚ/B decides, the ATR and ÚFAL propose
(#84 §7). Until then, these files are the proposal.
