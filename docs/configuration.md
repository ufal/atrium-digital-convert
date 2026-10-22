# Configuration

Every setting of the converter and the service.

Every setting is an environment variable. [`.env.example`](../.env.example) is the complete ledger
(`tests/test_env_contract.py` keeps it complete).

| Variable                                                  | Default                 | Meaning                                                                       |
|-----------------------------------------------------------|-------------------------|-------------------------------------------------------------------------------|
| `MAX_UPLOAD_MB`, `MAX_PAGES`, `MAX_CONCURRENT_JOBS`       | 250, 2000, 2            | service limits                                                                |
| `OCR_LAYER_DOCUMENT_SHARE`                                | 0.5                     | at or above this share of prior-OCR pages a PDF is refused (`ocr_text_layer`) |
| `ROUTE_TRASH_SHARE`                                       | 0.5                     | `/describe`: a decoding page with more `Trash` prose lines goes to `ocr`      |
| `ROUTE_DAMAGED_WORD_SHARE`                                | 0.05                    | `/describe`: a decoding page with this share of words losing a letter → `ocr` |
| `FURNITURE_ONLY_IMAGE_COVERAGE`                           | 0.85                    | a page with only header/footer text over images covering this much is `none`  |
| `BOLD_HEADING_MAX_SHARE`                                  | 0.25                    | bold body-size titles are headings up to this share of bold text; 0 = off     |
| `LIBREOFFICE_BIN`, `LIBREOFFICE_TIMEOUT_S`                | `soffice`, 120 s        | DOC/XLS conversion                                                            |
| `PAGE_CLASSIFICATION_URL`, `OCR_POSTPROCESS_URL`          | unset                   | `/describe`'s stages; unset → `not_configured`                                |
| `STAGE_TIMEOUT_S`                                         | 360 s                   | per stage call; over it the stage is `unavailable`                            |
| `PAGE_CLASSIFICATION_VERSION`, `PAGE_CLASSIFICATION_TOPN` | `all` (the ensemble), 3 | the model version and top-N `/describe` asks page-classification for          |
| `DOCLING_ARTIFACTS_PATH`                                  | unset                   | Docling's model directory (`--engine docling`)                                |

The full table of limits, with what happens over each one, is in
[service/README.md § Limits](../service/README.md#limits). `GET /info` reports the value in force.
