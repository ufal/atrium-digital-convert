# ATRIUM digital-convert: born-digital documents into docproc layout

digital-convert reads a [born-digital](https://ufal.github.io/atrium-project/glossary/) document, one made on a
computer, such as a PDF, an office file, a spreadsheet or a text file. It writes the document's layout as docproc
(`atr/docproc-json`), the layout format that the ATR, AIS CR's text-recognition service, writes for scanned pages:
pages, blocks and lines in reading order, headings, tables and, for a PDF or an office document, exact positions. It
gives every page a verdict on its text layer, and marks a page whose text cannot be used for the ATR.
ocr-postprocess builds the ATRIUM document record from the docproc. digital-convert needs no OCR, no model and no
network.

[Releases](https://github.com/ufal/atrium-digital-convert/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/digital-convert/)

## Inputs and outputs

| Input                                                                                        | Output                                                                                                                                |
|----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| **visual**: PDF; DOCX, DOC, ODT, RTF (native text on the pages of a LibreOffice rendering)   | docproc: pages, blocks with their `lines[]`, tables as `cellGrid`; validated against [its schema](schemas/docproc/README.md)          |
| **textual**: ODS, XLSX, XLS; TXT, CSV, TSV, JSON (extracted as they are; no pages to render) | per page, `textLayer` and, for a PDF page whose text cannot be used, `needsAtr`; routing: every page, its `graphics` and `tables`     |
| recognised by content; images are not inputs (they go to the ATR)                            | an office document's rendered PDF, for page-classification of the pages that show graphics (`rendered_pdf`, `--rendered-out`)         |
| optionally, AMČR's seed (`doc_id`, `source.sha512`), checked against the file                | on request, the document as annotated Markdown; the run's paradata: what it read and wrote, by distribution name and SHA-512          |

Two workflows:

- **Visual.** A PDF page whose text layer does not decode, is an earlier OCR run, or has no text needs the ATR,
  and the whole document goes through the ATR once, with those pages forced. An office document never goes to the
  ATR: it is read as its own text, in full from its XML (tables, notes, text boxes, equations, SmartArt and chart
  text included), and placed on the pages LibreOffice renders, so it gets page numbers and boxes in pt like a PDF.
  Every page says what it shows besides its text: `graphics` (pictures, charts, diagrams, drawings) and `tables`
  (each a `table` block with its `cellGrid`). A page with neither holds running text only; the pages with graphics
  are the ones for page-classification, which reads them from the PDF, or from an office document's rendered PDF.
- **Textual.** Text with nothing to render: a spreadsheet's rows, a text file's lines, every CSV column, every JSON
  string. A sheet keeps every cell, numbers and dates as it displays them: an ODS as its file stored them, an XLSX
  or XLS as LibreOffice shows it in cs-CZ (`1,50`, `15.3.2021`). It has no geometry and never goes to the ATR, only
  straight to scoring. A line over 1000 characters is wrapped, and the wrap is recorded.

digital-convert writes no record, and it refuses no document for its pages: every page gets a verdict. Each format,
how it is read and what reaches the docproc: [docs/reference/formats.md](docs/reference/formats.md).

The service and the command line take the same inputs and give the same results:

| Result                             | Service                                                   | Command line (`api_util/digital_to_json.py`)          |
|------------------------------------|-----------------------------------------------------------|-------------------------------------------------------|
| the docproc                        | `POST /reformat` → `docproc`                              | `<doc_id>.docproc.json` (or `--docproc-out PATH`)     |
| an office document's rendered PDF  | `rendered_pdf` (a page with graphics, or `rendered=true`) | `--rendered-out PATH`                                 |
| the document as annotated Markdown | `markdown=true` → `markdown`                              | `--markdown` → `<doc_id>.md`                          |
| the seed (read only)               | the `document_json` part                                  | `--document-json`                                     |
| the run's paradata                 | `paradata`, a `CreateAction` in the answer                | a file, with `--paradata-dir`                         |

Only the command line has the opt-in Docling engine (`--engine docling`, a local build); the service image runs
the light engine. Every difference, and why: [docs/usage.md](docs/usage.md#the-command-line-and-the-service).

## Quick start

**What you need:** Python 3.11 and a checkout, or Docker. No network and no GPU. Office documents (DOCX, DOC, ODT,
RTF) and XLSX and XLS files need LibreOffice, which the images carry; the sample below is a PDF and needs none.

Convert a small PDF generated from this repository's test fixtures:

```bash
git clone https://github.com/ufal/atrium-digital-convert.git && cd atrium-digital-convert
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements_digital.txt
python3 tests/fixtures/digital/make_fixtures.py --outdir fixtures    # 25 small samples: 15 PDFs, 5 DOCX, CSV, TSV, …
python3 api_util/digital_to_json.py fixtures/minimal.pdf --out-dir out --markdown --paradata-dir out/paradata
```

The same as a service, from the published image. Start it in the background, wait until it is ready, call it, and
stop it:

```bash
docker run -d --name atrium-digital-convert -p 8000:8000 ghcr.io/ufal/atrium-digital-convert-api:<version>   # 2.0.0-beta or later, without its v
until curl -fsS localhost:8000/ready >/dev/null; do sleep 2; done
curl --fail-with-body -sS -X POST localhost:8000/reformat -F "file=@fixtures/minimal.pdf" --create-dirs -o out/minimal.json
docker rm -f atrium-digital-convert
```

Expected result (abridged): `out/minimal.docproc.json` from the command line, with `out/minimal.md` and the run's
paradata in `out/paradata/`; the same docproc under `docproc` in `out/minimal.json` from the service, with
`"limits_applied": []` and the run's `paradata`:

```json
{
  "version": "1.1",
  "sourceFormat": "pdf-digital",
  "pages": [{"pageNumber": 1, "width": 612.0, "height": 792.0, "coordinateUnit": "pt", "textLayer": "digital",
             "blocks": [{"id": "p1-b0", "type": "paragraph",
                         "lines": [{"text": "Excavation report, block one, line one.",
                                    "bbox": [72.0, 62.484, 275.436, 74.484], "bboxPrecision": "measured"}, …]}, …]}, …],
  "metadata": {"routing": {"pages": [{"page": 1, "route": "text-layer", "text_layer": true},
                                     {"page": 2, "route": "text-layer", "text_layer": true}]}}
}
```

Both pages have a text layer that decodes, so neither needs the ATR. Releases before 2.0.0-beta return a record
instead of docproc and are described by the README at their tag.

## How it works

Each page's text layer is checked before anything is trusted:

- `digital`: the layer decodes;
- `garbled`: it does not decode (broken font encodings, glyphs without a Unicode mapping). A layer that decodes but
  whose words lose some letters stays `digital`: whether its text reads is ocr-postprocess's scoring;
- `ocr`: it is an earlier OCR run;
- `none`: the page has no text, or its only text is a page number over a scan.

Every page that is not `digital` carries `needsAtr` with a reason and is routed `atr`. An empty page, with nothing on
it, is routed `empty`: it is listed in `metadata.routing` and left out of `pages[]`. A line whose text layer decodes
to wrong characters keeps the flag `decode:garbled` (`decode:inverted` for mirrored text): a verdict on its
characters, apart from the line quality ocr-postprocess scores. The rules, with an example:
[docs/how-it-works.md](docs/how-it-works.md).

## Where it fits in ATRIUM

For an AMČR upload that is born-digital, digital-convert is the first step. When no page needs the ATR, its docproc
is the one stored for the document, and ocr-postprocess builds the record from it. When a page needs the ATR, the
ATR's docproc is the stored one, and this one is an intermediate (`stored: false` in the paradata): ocr-postprocess
merges the two and keeps this docproc's exact lines on the pages that did not need the ATR. The record then goes on
to nlp-enrich and keyword-extract. In the AMČR deployment the pipeline's orchestrator (Temporal) chains the steps,
and `/reformat` calls no other service. The whole route: the hub's
[Pipelines](https://ufal.github.io/atrium-project/pipelines/#the-route-through-the-pipeline) page and this tool's
[workflow page](https://ufal.github.io/atrium-project/workflows/digital-convert/). Its SSH Open Marketplace record:
[Born-digital document converter: PDF and office files into structured text without OCR -
ATRIUM](https://marketplace.sshopencloud.eu/tool-or-service/jGxgQI).

## Limits and maturity

- **The docproc schema is ARÚP's.** digital-convert validates its output against its copy in
  [schemas/docproc/](schemas/docproc/README.md): the ATR's schema with block `lines[]` and the page verdict added. ARÚP
  versions the schema in its aiscr-docs-pipeline repository; a new version is copied here.
- **Uploads up to 250 MB by default.** AMČR's originals reach about 1.5 GB, so a deployment must raise
  `MAX_UPLOAD_MB` for them. Above 250 MB, memory, concurrency and timeouts are not yet tested, and the upload
  is held in memory ([Limits](service/README.md#limits)).
- **The textual workflow has no geometry.** PDFs and office documents have boxes; spreadsheets and text files give
  pages and lines without positions ([formats](docs/reference/formats.md)). Boxes are on the displayed page:
  origin top-left of the CropBox, after /Rotate, in 1/72 inch × UserUnit, with `userUnit` written when it is not 1,
  checked against pdfium's rendering of rotated, cropped and offset pages. The opt-in Docling engine lays its
  structure over the same lines; text it reads that no line holds keeps Docling's own box, which is not checked on a
  page whose CropBox or MediaBox is offset.
- **An office document's pages are LibreOffice's.** They follow LibreOffice's layout with metric-compatible fonts,
  which can break pages differently from Word. The docproc and the rendered PDF use the same pages. Tracked
  changes are accepted in a DOCX, DOC and ODT before rendering, but not in an RTF. An office document never goes to
  the ATR, so text inside a picture pasted into it (a scanned page, a photo of a table) is not read.
- **No partial output.** A step that cannot finish fails the job and writes nothing: a limit that would cut content
  (the ODF repeat cap) fails the conversion. On the command line the docproc is written before the rendered PDF and
  the Markdown, so a failure in those leaves it on disk, with paradata that say the run failed.
- **Tried on 15 AMČR excavation reports, with no accuracy figure.** The defects they showed were fixed or are
  listed below ([what they showed](docs/evaluation/amcr-samples.md)).
- **Known, not handled.** Unruled tables are read by column, and letter-spaced captions and map labels split
  into pieces ([the AMČR samples](docs/evaluation/amcr-samples.md)).
- **Office documents, XLSX and XLS need LibreOffice.** The images carry it. Without it a DOCX, DOC, ODT, RTF, XLSX or
  XLS fails with `dependency_missing`, so use the image ([errors](service/README.md#errors)).
- **A sheet displays in cs-CZ.** An XLSX does not say how its numbers and default dates read, so LibreOffice shows
  it as a Czech sheet does (decimal comma, `15.3.2021`, `PRAVDA`), and the paradata name the locale
  (`spreadsheet_locale`).
- **The Docling engine is opt-in.** It reads PDF only and needs a GPU and its models (`--engine docling`); the
  default light engine needs neither ([usage](docs/usage.md#convert-a-file-on-the-command-line)).

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
| changing the code   | understand the text layers, `needsAtr` and the routes   | [docs/how-it-works.md](docs/how-it-works.md)                                   |
|                     | see what is parked during the pilot                     | [docs/reference/parked.md](docs/reference/parked.md)                           |
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
adapter (MIT). The inventory of components and their licences is [para_config.txt](para_config.txt). Each run lists
the components it used, each with its own licence or terms. No licence of an output is computed: the rights in an
output follow the source document ([docs/reference/paradata.md](docs/reference/paradata.md)). Cite with
[CITATION.cff](CITATION.cff).

Developed by [ÚFAL](https://ufal.mff.cuni.cz/), Charles University, with AMČR (the Archaeological Map of the Czech
Republic) as the pipeline partner. Funded by the European Union under Horizon Europe
([ATRIUM](https://atrium-research.eu/), GA 101132163), with the support of the LINDAT/CLARIAH-CZ (LM2023062) and AIS
CR (LM2023031) research infrastructures, funded by the Ministry of Education, Youth and Sports of the Czech Republic.
Contact: the [issue tracker](https://github.com/ufal/atrium-digital-convert/issues).

Formerly `atrium-llm-enrich`; its history is in [docs/development.md](docs/development.md#history-of-the-repository).
