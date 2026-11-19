# Configuration

Every setting of the converter and the service.

Every setting is an environment variable. [`.env.example`](../.env.example) is the complete ledger
(`tests/test_env_contract.py` keeps it complete).

| Variable                                                  | Default                 | Meaning                                                                                                                                |
|-----------------------------------------------------------|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `MAX_UPLOAD_MB`, `MAX_PAGES`, `MAX_CONCURRENT_JOBS`       | 250, 2000, 2            | limits: `MAX_PAGES` on both sides; `MAX_UPLOAD_MB` in the service, and in the CLI's shared reader; `MAX_CONCURRENT_JOBS` the service's |
| `ROUTE_TRASH_SHARE`                                       | 0.5                     | `/describe` and `--describe`: a decoding page with more `Trash` prose lines goes to `ocr`                                              |
| `FURNITURE_ONLY_IMAGE_COVERAGE`                           | 0.85                    | a page with only header/footer text over images covering this much is `none`                                                           |
| `BOLD_HEADING_MAX_SHARE`                                  | 0.25                    | bold body-size titles are headings up to this share of bold text; 0 = off                                                              |
| `RENDER_TEXT_COVERAGE`                                    | 0.8                     | a rendered office page whose words the document's text covers less (3+ not covered) needs the ATR                                      |
| `MAX_LINE_CHARS`                                          | 1000                    | text-only inputs and spreadsheet rows: longer lines are wrapped, each wrap a `split` in the paradata; 0 = never                        |
| `LIBREOFFICE_BIN`, `LIBREOFFICE_TIMEOUT_S`                | `soffice`, 120 s        | DOC/XLS/XLSX conversion and the rendering of office documents                                                                          |
| `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`          | unset                   | the stages of `/describe` and of the CLI's `--describe`; unset → `not_configured`                                                      |
| `STAGE_TIMEOUT_S`                                         | 360 s                   | per stage call; over it the stage is `unavailable`                                                                                     |
| `PAGE_CLASSIFICATION_VERSION`, `PAGE_CLASSIFICATION_TOPN` | `all` (the ensemble), 3 | the model version and top-N `/describe` (`--describe`) asks page-classification for                                                    |
| `DOCLING_ARTIFACTS_PATH`                                  | unset                   | Docling's model directory (`--engine docling`)                                                                                         |

The full table of limits, with what happens over each one, is in
[service/README.md § Limits](../service/README.md#limits). `GET /info` reports the value in force.
