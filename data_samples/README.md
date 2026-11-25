# Synthetic samples: born-digital conversion

> [!IMPORTANT]
> Everything here is invented: the site (Hradiště u Horní Mezí), the people, the finds, the
> photographs and the plans. These are not AMČR documents, they are no reference for any real
> site, and they say nothing about how the converter does on real reports. They share the invented
> world of the synthetic scans `CTX000000001`–`3` used by the other ATRIUM tools.

The three PDFs in [`pdf/`](pdf) were made by [`pdf/make_synthetic_pdfs.py`](pdf/make_synthetic_pdfs.py)
(reportlab, Pillow and pypdf with the DejaVu and Liberation fonts; deterministic). Each page is
built to show one thing the converter decides. The verdict column is the docproc's `pages[].textLayer`, from the
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
quality, left to ocr-postprocess's scoring of its lines. The caption on page 4 of
`CTX000000004` reads with a space between letters: letter-spaced text is not yet joined.

## The outputs

| Folder                                    | File per document     | What it is                                                                                                                         |
|-------------------------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------|
| `docproc/light/`                          | `<doc>.docproc.json`  | The CLI's docproc (`atr/docproc-json`), light engine: lines with their positions, tables, every page's verdict and its routing.    |
| `docproc/docling/`                        | `<doc>.docproc.json`  | The same with `--engine docling` (Docling's layout and table models), for comparison.                                              |
| `markdown/full/`, `standard/`, `minimal/` | `<doc>.<detail>.md`   | The document as Markdown at three levels of detail. A page without usable text carries a `<!-- NEEDS_OCR: … -->` cue.              |
| `reports/reformat/`                       | `<doc>.reformat.json` | The service's `/reformat` answer with `markdown=true`: the docproc and its Markdown.                                               |
| `reports/describe/none/`                  | `<doc>.describe.json` | `/describe`, which calls no other service: per page, its text layer and where it goes next (`route`, `route_reason`).              |

The `reports/describe/` answers come from the per-page assessment, parked during the AMČR pilot
([parked features](../docs/reference/parked.md)). Until v2.0.0-beta `/describe` could also call page-classification
and ocr-postprocess; the answers made that way (`stages/`, `all/`) are no longer committed: page-classification
1.9.10-beta and ocr-postprocess 1.12.1-beta returned a 1.0 record that carried a calculated licence, which record
schema 2.0 removed. Git history up to `v1.4.7-beta` keeps them.

## Making them again

```bash
pip install reportlab Pillow pypdf
python3 data_samples/pdf/make_synthetic_pdfs.py
```

Then, for each `X` in `data_samples/pdf/`:

```bash
python3 api_util/digital_to_json.py X.pdf --docproc-out data_samples/docproc/light/X.docproc.json
python3 api_util/digital_to_json.py X.pdf --engine docling --docproc-out data_samples/docproc/docling/X.docproc.json
python3 api_util/doc_to_visual_md.py X.pdf --detail full --output data_samples/markdown/full/X.full.md
curl -F file=@X.pdf -F markdown=true http://localhost:8000/reformat       # this service on port 8000
```

The `/describe` report:

```bash
curl -F file=@X.pdf http://localhost:8000/describe
```

`standard` and `minimal` Markdown take `--detail standard` and `--detail minimal`.
