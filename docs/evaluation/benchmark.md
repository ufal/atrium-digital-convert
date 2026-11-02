# Document Understanding benchmark (research tools)

**In short:** This page describes a research harness, not a result. Three scripts compare vision-language and OCR
models with the legacy ABBYY/ALTO pipeline, page by page, on a gold set. They run from a checkout and are in no
image or service, so a user of the converter never needs them. No scores are reported here, so the page does not
show how well any model reads.

The research harness that compares VLM/OCR models with the ABBYY/ALTO pipeline.

It is the evaluation harness of hub issue [#22](https://github.com/ufal/atrium-project/issues/22) (see
[#3](https://github.com/ufal/atrium-digital-convert/issues/3)). It compares out-of-the-box VLM/OCR models (VLM:
vision-language model) with the legacy ABBYY/ALTO pipeline. Scores are given per quality tier on an in-domain
gold set.

* **Scripts.** `sample_stratify.py`, `bench_compare.py` and `eval_metrics.py` are torch-free.
* **Where they run.** They are research tools, run from a checkout and left out of every image (`.dockerignore`).

```bash
python sample_stratify.py --page-stats samples_page_stats.csv --n 200 --output docu_sample_manifest.csv
python bench_compare.py --manifest docu_sample_manifest.csv --gold data/gold \
    --pred alto=../atrium-ocr-postprocess/data_samples/PAGE_TXT \
           layoutreader=../atrium-ocr-postprocess/data_samples/PAGE_TXT_LR \
    --split test --output-dir bench_results
```

* **Sampling.** `sample_stratify.py` buckets pages by OCR quality (`clean` / `degraded` / `hard` / `text_poor`).
  It writes an annotation manifest with a deterministic 80/10/10 split.
* **Gold data.** One UTF-8 transcription per manifest page (`gold/<doc>/<doc>-<page>.txt`, optionally
  `.entities.tsv`).
* **Comparison.** `bench_compare.py` writes `page_scores.csv`, `aggregate_scores.csv` and `report.md`. They hold
  the character error rate (CER), the word error rate (WER), the normalised edit distance (NED) and, optionally,
  entity precision, recall and F1. The output is byte-identical across reruns.
* **Config.** Both scripts also read an INI config (`--config config_docu.txt`, sections `[STRATIFY]` and
  `[BENCHMARK]`).
