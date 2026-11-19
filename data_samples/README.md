# Synthetic samples: born-digital conversion

> [!IMPORTANT]
> Everything here is invented: the site (Hradiště u Horní Mezí), the people, the finds, the
> photographs and the plans. These are not AMČR documents, they are no reference for any real
> site, and they say nothing about how the converter does on real reports. They share the invented
> world of the synthetic scans `CTX000000001`–`3` used by the other ATRIUM tools.

The three PDFs in [`pdf/`](pdf) were made by [`pdf/make_synthetic_pdfs.py`](pdf/make_synthetic_pdfs.py)
(reportlab, Pillow and pypdf with the DejaVu and Liberation fonts; deterministic). Each page is
built to show one thing the converter decides. The verdict column is `pages[].text_layer` of the
light engine.

| Document       | Page (label) | What the page is                                                                     | Verdict   |
|----------------|--------------|--------------------------------------------------------------------------------------|-----------|
| `CTX000000004` | i            | title page                                                                           | `digital` |
|                | ii           | contents with dot leaders                                                            | `digital` |
|                | 1            | section titles in bold at the body size, running header, page number                 | `digital` |
|                | 2            | two columns                                                                          | `digital` |
|                | 3            | ruled table                                                                          | `digital` |
|                | 4            | photograph with a letter-spaced caption                                              | `digital` |
|                | 5            | plan whose labels are part of the image; its only text is the header and page number | `none`    |
|                | 6            | running text and a reference list                                                    | `digital` |
|                | 7            | photograph with a caption                                                            | `digital` |
| `CTX000000005` | 1, 2         | typewritten scan with an invisible OCR text layer (render mode 3)                    | `ocr`     |
|                | 3            | scanned sketch, no text                                                              | `none`    |
|                | 4            | typewritten scan whose only text is a stamped page number                            | `none`    |
| `CTX000000006` | 1            | clean text                                                                           | `digital` |
|                | 2            | a font with no Unicode for ř ě č ů: some words carry U+FFFD                          | `digital` |
|                | 3            | a font whose every glyph maps to U+FFFD                                              | `garbled` |
|                | 4            | CP1250 bytes under a WinAnsi font (mojibake)                                         | `garbled` |

Page 2 of `CTX000000006` stays `digital` because a few damaged words are a question of text
quality, left to ocr-postprocess's scoring of its lines (#9). The caption on page 4 of
`CTX000000004` reads with a space between letters: letter-spaced text is not yet joined.

## The outputs

| Folder                                    | File per document     | What it is                                                                                                                         |
|-------------------------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------|
| `docproc/light/`                          | `<doc>.docproc.json`  | The CLI's docproc (`atr/docproc-json`), light engine: lines with their positions, tables, and every page's verdict and `needsAtr`. |
| `docproc/docling/`                        | `<doc>.docproc.json`  | The same with `--engine docling` (Docling's layout and table models), for comparison.                                              |
| `markdown/full/`, `standard/`, `minimal/` | `<doc>.<detail>.md`   | The document as Markdown at three levels of detail. A page without usable text carries a `<!-- NEEDS_OCR: … -->` cue.              |
| `reports/reformat/`                       | `<doc>.reformat.json` | The service's `/reformat` answer with `markdown=true`: the docproc and its Markdown.                                               |
| `reports/describe/none/`                  | `<doc>.describe.json` | `/describe` without other services: per page, its text layer and where it goes next (`route`, `route_reason`).                     |
| `reports/describe/stages/`                | `<doc>.describe.json` | `/describe` with page-classification (on the pages without usable text) and ocr-postprocess's line-quality model (on the text).    |
| `reports/describe/all/`                   | `<doc>.describe.json` | `/describe` with page-classification on every page: the page type of each one.                                                     |

Routes, page types and quality bands come from the two services' models. Their versions are in
each report (`stages[].service_version`, `paradata`).

## Making them again

```bash
pip install reportlab Pillow pypdf
python3 data_samples/pdf/make_synthetic_pdfs.py
```

Then, for each `X` in `data_samples/pdf/`, with page-classification (`python -m service.api`) and
ocr-postprocess (`python service/text_api.py`) running, and this service started with
`PAGE_CLASSIFICATION_URL` and `OCR_POSTPROCESS_URL` pointing at them:

```bash
python3 api_util/digital_to_json.py X.pdf --docproc-out data_samples/docproc/light/X.docproc.json
python3 api_util/digital_to_json.py X.pdf --engine docling --docproc-out data_samples/docproc/docling/X.docproc.json
python3 api_util/doc_to_visual_md.py X.pdf --detail full --output data_samples/markdown/full/X.full.md
curl -F file=@X.pdf -F markdown=true http://localhost:8000/reformat
curl -F file=@X.pdf -F stages=none http://localhost:8000/describe
curl -F file=@X.pdf http://localhost:8000/describe
curl -F file=@X.pdf -F stages=page-classification -F classify_pages=all http://localhost:8000/describe
```

`standard` and `minimal` Markdown take `--detail standard` and `--detail minimal`.
