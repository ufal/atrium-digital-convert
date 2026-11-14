# ATRIUM digital-convert: born-digital documents into docproc layout

digital-convert reads a [born-digital](https://ufal.github.io/atrium-project/glossary/) document, one made on a
computer, such as a PDF or an office file. It writes the document's layout as docproc (`atr/docproc-json`), the
format the ATR writes for scanned pages: pages, blocks and lines in reading order, headings, tables and, for a PDF,
exact positions, with a verdict on each page's text layer. ocr-postprocess builds the ATRIUM record from it. Use it
when the text is already in the file; a page whose text cannot be used is routed to the ATR. It needs no OCR, no
model and no network.

[Releases](https://github.com/ufal/atrium-digital-convert/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/digital-convert/)

## Inputs and outputs

| Input                                                                                  | Output                                                                                                                                       |
|----------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| PDF, DOCX, ODT, ODS, XLSX, RTF, recognised by content; DOC and XLS through LibreOffice | docproc: pages, blocks with their `lines[]`, tables as `cellGrid`; validated against [the proposal schema](schemas/docproc/README.md)        |
| optionally, AMČR's seed (`doc_id`, `source.sha512`), checked against the file          | per page, `textLayer` and, where the text cannot be used, `needsAtr` with its reason; `metadata.routing` routes every page                   |
|                                                                                        | on request, the document as annotated Markdown; with `/describe` (parked), a per-page assessment                                             |

Since ARÚP's output contract of 12 November 2026 ([atrium-project#84](https://github.com/ufal/atrium-project/issues/84))
digital-convert writes no record and refuses no document for its pages. Each format, how it is read and what reaches
the docproc: [docs/reference/formats.md](docs/reference/formats.md).

The service and the command line take the same inputs and give the same results. They run the same code for the
docproc, the Markdown and the per-page assessment:

| Result                              | Service                                         | Command line (`api_util/digital_to_json.py`)                                          |
|-------------------------------------|-------------------------------------------------|---------------------------------------------------------------------------------------|
| the docproc                         | `POST /reformat` → `docproc`                    | `<doc_id>.docproc.json` (or `--docproc-out PATH`)                                     |
| the document as annotated Markdown  | `markdown=true` → `markdown`                    | `--markdown` → `<doc_id>.md`                                                          |
| the per-page assessment, the routes | `POST /describe` → `pages`, `summary`, `stages` | `--describe` → `<doc_id>.describe.json` (`--stages`, `--classify-pages`, `--no-text`) |
| the seed (read only)                | the `document_json` part                        | `--document-json`                                                                     |
| the run's paradata                  | `paradata`, a `CreateAction` in the answer      | a file, with `--paradata-dir`                                                         |

Only the command line has the opt-in Docling engine (`--engine docling`, a local build); the service image runs
the light engine. Every difference, and why: [docs/usage.md](docs/usage.md#the-command-line-and-the-service).
Two offline research tools, outside the images and the service, ask a decision model the AMČR document type of
PDF pages and documents, from the rendered pages alone
([usage](docs/usage.md#document-types-for-decision-models-offline)); decision models are parked during the pilot.

## Quick start

**What you need:** Docker for the service; for the sample files and the command line, a checkout of this
repository and Python 3.11 (the version of the image and of CI). No network is needed to convert a file, and no
GPU. DOC and XLS files need LibreOffice, which the images carry.

The service, from the published image, on small documents generated from this repository's test fixtures:

```bash
python3 tests/fixtures/digital/make_fixtures.py --outdir fixtures     # 17 small PDFs and DOCX files
docker run --rm -p 8000:8000 ghcr.io/ufal/atrium-digital-convert-api:<version>   # the release without its v
curl --fail-with-body -sS -X POST localhost:8000/reformat -F "file=@fixtures/minimal.pdf" -o out.json
```

Expected: HTTP 200 and `out.json` with `docproc`, whose two `pages` are `textLayer: digital`, routed `text-layer`,
and whose first block reads "Excavation report, block one, line one."

The same conversion on the command line, from a checkout with its [setup](docs/usage.md#set-up-a-checkout) done:

```bash
python3 api_util/digital_to_json.py fixtures/minimal.pdf --out-dir out --markdown --paradata-dir out/paradata
```

Expected: `out/minimal.docproc.json` (the docproc `/reformat` returns), `out/minimal.md` (its `markdown`) and the
run's paradata in `out/paradata/`: the original read and the docproc written, by SHA-512.

## How it works

Each page's text layer is checked before anything is trusted:

- `digital`: the layer decodes;
- `garbled`: it does not decode (broken font encodings, glyphs without a Unicode mapping). A layer that decodes but
  whose words lose some letters stays `digital`: whether its text reads is ocr-postprocess's scoring
  (atrium-digital-convert#9);
- `ocr`: it is an earlier OCR run;
- `none`: the page has no text, or its only text is a page number over a scan;
- `blank`: an empty page.

Every page that is not `digital` (or `blank`) carries `needsAtr` with a reason and is routed `atr`; an empty page
is routed `empty`. A line whose text layer decodes to wrong characters keeps the flag `Garbage` (`Inverted` for
mirrored text). The rules, with an example: [docs/how-it-works.md](docs/how-it-works.md).

## Where it fits in ATRIUM

For an AMČR upload that is born-digital, digital-convert is the first stage. Its docproc goes to ocr-postprocess,
which builds the record from it; the record goes on to nlp-enrich and keyword-extract. When a page needs the ATR,
the ATR's docproc is the stored one and this one is an intermediate (`stored: false` in the paradata); the merge
of a mixed document is the next round of #84. In the AMČR deployment Temporal chains the stages, and `/reformat`
calls no other service. The whole chain: the
[hub's workflow page](https://ufal.github.io/atrium-project/workflows/digital-convert/). Its SSH Open Marketplace
record: [Born-digital document converter: PDF and office files into structured text without OCR -
ATRIUM](https://marketplace.sshopencloud.eu/tool-or-service/jGxgQI).

## Limits and maturity

- **docproc is a proposal on our side.** The ATR's schema (as delivered on 10 November) with asks 3 and 13 of #84
  applied; until ARÚ/B adopts it under its identifier, the proposal is in [schemas/docproc/](schemas/docproc/README.md).
- **Uploads up to 250 MB by default.** AMČR's originals reach about 1.5 GB, so a deployment must raise
  `MAX_UPLOAD_MB` for them. Above 250 MB, memory, concurrency and timeouts are not yet tested, and the upload
  is held in memory ([Limits](service/README.md#limits)).
- **Only PDF has geometry.** The other formats give pages and lines without positions
  ([formats](docs/reference/formats.md)). Boxes are in PDF points from the page's top-left; UserUnit, CropBox and
  /Rotate are not yet applied (#84, ask 10).
- **No partial output.** A limit that would cut content (the ODF repeat cap) fails the conversion (D13).
- **Tried on 15 AMČR excavation reports, with no accuracy figure.** The defects they showed were fixed or are
  listed below ([what they showed](docs/evaluation/amcr-samples.md)).
- **Known, not handled.** Unruled tables are read by column, and letter-spaced captions and map labels split
  into pieces ([the AMČR samples](docs/evaluation/amcr-samples.md)).
- **DOC and XLS need LibreOffice.** The images carry it. Without it these files fail with `dependency_missing`,
  so use the image or convert them to DOCX or XLSX first ([errors](service/README.md#errors)).
- **The Docling engine is opt-in.** It reads PDF only and needs a GPU and its models (`--engine docling`); the
  default light engine needs neither ([usage](docs/usage.md#convert-a-file-on-the-command-line)).
- **`/describe` is parked** (not part of the pilot): it assesses the converter's development dict, and the 2.0
  stages it calls may refuse that dict.

## Documentation

| For                 | To                                                      | Read                                                                           |
|---------------------|---------------------------------------------------------|--------------------------------------------------------------------------------|
| using it            | set it up, run the command line, the service, Docker    | [docs/usage.md](docs/usage.md)                                                 |
|                     | read the document as annotated Markdown                 | [docs/reference/markdown.md](docs/reference/markdown.md)                       |
|                     | look up the formats and the docproc fields              | [docs/reference/formats.md](docs/reference/formats.md)                         |
|                     | see the docproc schemas                                 | [schemas/docproc/README.md](schemas/docproc/README.md)                         |
|                     | see the paradata and the components' licences           | [docs/reference/paradata.md](docs/reference/paradata.md)                       |
| running the service | deploy and operate it, read its errors and limits       | [service/README.md](service/README.md)                                         |
|                     | see the API contract                                    | [service/openapi.json](service/openapi.json)                                   |
|                     | set every option                                        | [docs/configuration.md](docs/configuration.md)                                 |
| judging the results | see what the AMČR samples showed                        | [docs/evaluation/amcr-samples.md](docs/evaluation/amcr-samples.md)             |
|                     | run the document-understanding benchmark                | [docs/evaluation/benchmark.md](docs/evaluation/benchmark.md)                   |
| changing the code   | understand the text layers, `needs_ocr` and the routes  | [docs/how-it-works.md](docs/how-it-works.md)                                   |
|                     | set up, test, release; find the history                 | [docs/development.md](docs/development.md)                                     |
|                     | follow the conventions                                  | [CONTRIBUTING.md](CONTRIBUTING.md)                                             |

Terms used across the tools are explained in the
[ATRIUM glossary](https://ufal.github.io/atrium-project/glossary/). The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Licence, citation and acknowledgements

The code is MIT ([LICENSE](LICENSE)). It reads PDFs with pdfplumber/pdfminer.six and pypdfium2, office files with
python-docx and lxml (and the reader shared with atrium-ocr-postprocess), the legacy formats through
[LibreOffice](https://www.libreoffice.org/) (MPL-2.0), and, opt-in, [Docling](https://github.com/docling-project/docling);
the service runs on FastAPI and uvicorn, and [flexiconv](https://github.com/ufal/flexiconv) is an optional TEITOK
adapter (MIT). The inventory of components and their licences is [para_config.txt](para_config.txt); a run lists
the ones it used, each with its own terms, and computes no licence of its output
([docs/reference/paradata.md](docs/reference/paradata.md)). Cite with
[CITATION.cff](CITATION.cff).

Developed by [ÚFAL](https://ufal.mff.cuni.cz/), Charles University, with AMČR (the Archaeological Map of the Czech
Republic) as the pipeline partner. Funded by [ATRIUM](https://atrium-research.eu/) (GA 101132163), with the support
of the LINDAT/CLARIAH-CZ research infrastructure and the AIS CR infrastructure (LM2023031). Contact: the
[issue tracker](https://github.com/ufal/atrium-digital-convert/issues).

Formerly `atrium-llm-enrich`; its history is in [docs/development.md](docs/development.md#history-of-the-repository).
