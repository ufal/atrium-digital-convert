# ATRIUM digital-convert: born-digital documents into ATRIUM document records

digital-convert reads a document that was created on a computer, and so already holds its text, and writes it as
an ATRIUM document record: its pages, its lines in reading order, headings, tables and, for a PDF, exact geometry.
The record is the JSON every later ATRIUM stage adds to. Use it for born-digital PDFs and office files; a scan,
or a PDF that is mostly a prior OCR run, belongs to the OCR route instead. It uses no OCR, no model and no network.

[Releases](https://github.com/ufal/atrium-digital-convert/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/digital-convert/)

Formerly `atrium-llm-enrich`: until v1.0.0-beta this repository also held the keyword stage, which moved to
[atrium-keyword-extract](https://github.com/ufal/atrium-keyword-extract) ([#1](https://github.com/ufal/atrium-digital-convert/issues/1)).
Since v1.1.0-beta the service id is `atrium-digital-convert` and the images are
`ghcr.io/ufal/atrium-digital-convert-{api,digital}`; the record's program id stays `digital-convert`.

## Inputs and outputs

| Input                                                                                  | Output                                                                                                              |
|----------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| PDF, DOCX, ODT, ODS, XLSX, RTF, recognised by content; DOC and XLS through LibreOffice | the record: `source`, `pages`, `lines`, `content`, `tables`, `provenance`, validated against the frozen schema      |
| optionally, AMČR's seed record (`doc_id`, `source.sha512`), checked against the file   | per page, `text_layer` and, where the text cannot be used as it is, `needs_ocr` with its reason                     |
|                                                                                        | on request, the record as annotated Markdown; with `/describe`, a per-page assessment and where each page goes next |

The record follows the hub's [document contract](https://ufal.github.io/atrium-project/ecosystem/document-contract/).
Each format, how it is read and what reaches the record: [docs/reference/formats.md](docs/reference/formats.md).

## Quick start

The service, from the published image, on small documents generated from this repository's test fixtures:

```bash
python3 tests/fixtures/digital/make_fixtures.py --outdir fixtures     # 17 small PDFs and DOCX files
docker run --rm -p 8000:8000 ghcr.io/ufal/atrium-digital-convert-api:<version>   # the release without its v
curl --fail-with-body -sS -X POST localhost:8000/reformat -F "file=@fixtures/minimal.pdf" -o out.json
```

Expected: HTTP 200 and `out.json` with `document_json`, whose `source.origin` is `digital-born-pdf`, whose two
`pages` are `digital`, and whose first `lines` read "Excavation report, block one, line one." `POST /describe`
with the same file adds `pages[]` with a `route` for each page; it calls page-classification and ocr-postprocess
only when their URLs are set, and says so in `stages[]`.

The same conversion on the command line, from a checkout with its [setup](docs/usage.md#setup) done:

```bash
python3 api_util/digital_to_json.py fixtures/minimal.pdf --document-json-out minimal.document.json
```

## How it works

Each page's text layer is checked before anything is trusted: a layer that decodes is `digital`; one that does
not (broken font encodings, glyphs without a Unicode mapping, words losing letters) is `garbled`; a prior OCR run
is `ocr`; a page without text, or whose only text is a page number over a scan, is `none`. Every page that is not
`digital` (or `blank`) is flagged `needs_ocr` with a reason, and those pages go to the OCR route while the record is
still written. `/describe` then decides, page by page, whether a page goes on to NLP, to OCR, to handwriting
recognition or nowhere, using page-classification's page types and ocr-postprocess's line quality when they are
deployed. The rules, with an example: [docs/how-it-works.md](docs/how-it-works.md).

## Where it fits in ATRIUM

For an AMČR upload that is born-digital, digital-convert is the first stage and the record's originator: it alone
writes the positional part of the record. Its record goes on to nlp-enrich and keyword-extract; the pages it flags
go to the OCR/HTR route, and ocr-postprocess merges their new text back into the same record, page by page. In
the AMČR deployment Temporal chains the stages, and `/reformat` calls no other service. The whole chain: the
[hub's workflow page](https://ufal.github.io/atrium-project/workflows/digital-convert/). Its SSH Open Marketplace
record: [Born-digital document converter: PDF and office files into structured text without OCR -
ATRIUM](https://marketplace.sshopencloud.eu/tool-or-service/jGxgQI).

## Limits and maturity

- **Born-digital only.** A PDF whose pages are mostly a prior OCR run (`OCR_LAYER_DOCUMENT_SHARE`, 0.5) is refused
  as `ocr_text_layer`; such pages within a born-digital PDF are flagged for OCR.
- **Uploads up to 250 MB by default.** AMČR's originals reach about 1.5 GB; a deployment can raise `MAX_UPLOAD_MB`,
  but above 250 MB memory, concurrency and timeouts are not yet tested, and the upload is held in memory
  ([Limits](service/README.md#limits)).
- **Only PDF has geometry.** The other formats give pages and lines without positions.
- **Known, not handled:** unruled tables are read by column, and letter-spaced captions and map labels split into
  pieces ([the AMČR samples](docs/evaluation/amcr-samples.md)).
- **DOC and XLS need LibreOffice**, which the images carry; without it they fail with `dependency_missing`.
- **The Docling engine is opt-in** (`--engine docling`, PDF only, a GPU and its models); the default light engine
  needs neither.

## Documentation

| To                                                     | Read                                                                           |
|--------------------------------------------------------|--------------------------------------------------------------------------------|
| set it up, run the command line, the service, Docker   | [docs/usage.md](docs/usage.md)                                                 |
| deploy and operate the service, its errors and limits  | [service/README.md](service/README.md)                                         |
| see the API contract                                   | [service/openapi.json](service/openapi.json)                                   |
| set every option                                       | [docs/configuration.md](docs/configuration.md)                                 |
| understand the text layers, `needs_ocr` and the routes | [docs/how-it-works.md](docs/how-it-works.md)                                   |
| look up the formats and the record's fields            | [docs/reference/formats.md](docs/reference/formats.md)                         |
| read the record as annotated Markdown                  | [docs/reference/markdown.md](docs/reference/markdown.md)                       |
| see the paradata, licences and the record schema       | [docs/reference/paradata.md](docs/reference/paradata.md)                       |
| see what the AMČR samples showed                       | [docs/evaluation/amcr-samples.md](docs/evaluation/amcr-samples.md)             |
| run the document-understanding benchmark               | [docs/evaluation/benchmark.md](docs/evaluation/benchmark.md)                   |
| contribute, test, release                              | [docs/development.md](docs/development.md), [CONTRIBUTING.md](CONTRIBUTING.md) |

The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Licence, citation and acknowledgements

The code is MIT ([LICENSE](LICENSE)). It reads PDFs with pdfplumber/pdfminer.six and pypdfium2, office files with
python-docx and lxml (and the reader shared with atrium-ocr-postprocess), the legacy formats through
[LibreOffice](https://www.libreoffice.org/) (MPL-2.0), and, opt-in, [Docling](https://github.com/docling-project/docling);
the service runs on FastAPI and uvicorn, and [flexiconv](https://github.com/ufal/flexiconv) is an optional TEITOK
adapter (GPL-3.0). The inventory of components and their licences is [para_config.txt](para_config.txt); what a
run records about them is in [docs/reference/paradata.md](docs/reference/paradata.md). Cite with
[CITATION.cff](CITATION.cff).

Developed by [ÚFAL](https://ufal.mff.cuni.cz/), Charles University, with AMČR (the Archaeological Map of the Czech
Republic) as the pipeline partner. Funded by [ATRIUM](https://atrium-research.eu/) (GA 101132163), with the support
of the LINDAT/CLARIAH-CZ research infrastructure and the AIS CR infrastructure (LM2023031). Contact: the
[issue tracker](https://github.com/ufal/atrium-digital-convert/issues).
