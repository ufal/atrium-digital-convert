# docproc schemas

digital-convert writes the document's layout as docproc (`atr/docproc-json`), the layout format the ATR, AIS CR's
text-recognition service, writes for scanned pages. ocr-postprocess builds the ATRIUM record from it. The schema is
ARÚP's: ARÚP versions it under an ARUP-CAS identifier in its aiscr-docs-pipeline repository, where the outputs are
validated; the ATR's team and ÚFAL propose changes, and ARÚP decides. These two files are the copies digital-convert
validates against; a new version from ARÚP is copied here.

| File                                   | What it is                                                                                       |
|----------------------------------------|--------------------------------------------------------------------------------------------------|
| `document.actual.schema.json`          | the ATR's schema as its team delivered it on 10 November 2026, verbatim                          |
| `document.atrium-proposal.schema.json` | the same schema with the changes below applied; what digital-convert's docproc validates against |

The second file is generated, never edited by hand: `python tools/docproc_proposal.py` writes it from the first, and
`tests/test_docproc_writer.py` pins that the two differ only by these changes. The generator names them by the
numbers of the requests sent to ARÚP (3, 5, 10 and 13) and of Ronald's answer 12. Request 14, what a page shows
besides its text (`graphics`, `tables` in routing), is withdrawn (atrium-project#84 decision 6): what a page shows is
its blocks, `table` blocks for its tables and `figure` blocks for its pictures.

**No run data required** (request 3): `processedAt` and `pipeline` are not required; the run, the original and its
digest are the paradata's. digital-convert writes neither, nor `sourceFile` or `sourceHash` (a SHA-256).

**Other producers and formats** (request 13):

- `pipeline` accepts any producer id (`^[a-z0-9][a-z0-9-]*(/[a-z0-9-]+)?$`; the ATR's `pipeline-e2e` fits);
- `sourceFormat` adds `odt`, `ods`, `xlsx`, `rtf`, `doc`, `xls`, and the text-only inputs `txt`, `csv`, `tsv`, `json`;
- a page may carry `textLayer` (`digital`, `garbled`, `ocr`, `none`, `blank`), `needsAtr` with `needsAtrReason`,
  and `userUnit`;
- a block may carry `lines[]`: `text`, `bbox`, `bboxPrecision` (`measured`, `derived`, `absent`), `language`,
  `flags` (digital-convert's decode verdicts `decode:garbled`, `decode:inverted`);
- a page of a flow document (a spreadsheet, a text-only input: nothing to render) has no `width`/`height`, and its
  blocks no `bbox`. Office documents are rendered, so their pages have a size.

**The forced pages echoed** (request 5): a `metadata.routing.pages[]` entry may carry `forced` and `forced_reason`,
the pages the caller forced the ATR to read and why. ocr-postprocess checks them against digital-convert's
`needsAtr` pages when it merges a mixed document.

**One coordinate space** (request 10): every box is on the displayed page, origin top-left of the CropBox, after
`/Rotate`, in 1/72 inch × `userUnit`. A page may carry `pixelRatio`: for an image docproc wraps into a PDF, the
image's pixels per unit. digital-convert writes `userUnit` when a PDF page's `/UserUnit` is not 1.

**Pictures without the picture** (answer 12 on aiscr-docs-pipeline#17, ARÚP's decision 6 on atrium-project#84):
an image-class block (`image`, `figure`, `chart`, `seal`) no longer requires `imageRef`; the stored docproc holds
where a picture is, not the picture. ARÚP relaxes the rule in its own version; this proposal applies it until then.
digital-convert writes each picture a page shows as a `figure` block with its `bbox`, no `lines`, no `content` and no
`imageRef`; a drawing it cannot box on the rendering has no `bbox`.

Everything else is the ATR's: block ids `p{page}-b{n}`, dense `readingOrder`, `cellGrid` tables,
`metadata.routing`. digital-convert routes every page in `metadata.routing.pages[]` (`text-layer`, `atr`, `empty`)
and lists in `pages[]` only the pages that are not empty; a PDF page with no text whose rendering shows nothing is
routed `empty`. It emits `version` `1.1`.
