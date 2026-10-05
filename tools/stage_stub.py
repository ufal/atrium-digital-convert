#!/usr/bin/env python3
"""tools/stage_stub.py — a stand-in for page-classification and ocr-postprocess (#2, `/describe`).

The two stages `/describe` calls are not deployed beside this service yet. This stub answers
their endpoints with the SAME request and response shapes, deterministically and without
models, so `/describe` can be run end to end today — locally, in compose (`--profile stub`),
and in this repository's live smoke:

  * ``GET  /info``              — ``{"service": "atrium-stage-stub", "version": ...}``
  * ``POST /predict_document``  — page-classification (with the ``pages`` subset and the label
    mapping of page-classification v1.9.2-beta): every requested page gets the category
    ``STUB_PAGE_CATEGORY`` (default ``TEXT_P``), or ``STUB_NEEDS_OCR_CATEGORY`` (default
    ``TEXT_T``) when the record flags it ``needs_ocr``. Given ``document_json``, it writes
    ``page_categories`` and ``pages[].category/category_confidence`` under the record's own
    labels, stamped ``page-classification``.
  * ``POST /score_record``      — ocr-postprocess's W3 scorer: each line not carrying a decode
    verdict (``Garbage``/``Inverted``) gets a category from a crude letter-share heuristic, and
    the record gets the scoring fields only, stamped ``ocr-postprocess``.
  * ``POST /process``           — ocr-postprocess's text path (``task_type=text``), for the
    per-page fallback.

NOT a model, and not in any image: it exists to exercise the plumbing. Run it with::

    python tools/stage_stub.py --port 8090
    PAGE_CLASSIFICATION_URL=http://localhost:8090 OCR_POSTPROCESS_URL=http://localhost:8090 \\
        python -m service.api
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, File, Form, UploadFile  # noqa: E402

from atrium_document import DocumentRecord  # noqa: E402

app = FastAPI(title="ATRIUM stage stub", version="0.1.0")

_DECODE_VERDICTS = {"Garbage", "Inverted"}


def _pages_param(raw: Optional[str], total: int) -> List[int]:
    if not raw or not raw.strip():
        return list(range(1, total + 1))
    wanted: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            wanted.extend(range(lo, hi + 1))
        elif part:
            wanted.append(int(part))
    return sorted({p for p in wanted if 1 <= p <= total})


def _pdf_pages(data: bytes) -> int:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()
    except Exception:
        return 1


def _score_line(text: str) -> Dict[str, Any]:
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return {"category": "Empty", "quality_score": 0.0, "lang": "unk"}
    letters = sum(1 for c in visible if c.isalpha()) / len(visible)
    if letters >= 0.7:
        category, score = "Clear", 0.9
    elif letters >= 0.4:
        category, score = "Noisy", 0.6
    elif letters > 0:
        category, score = "Trash", 0.2
    else:
        category, score = "Non-text", 0.0
    return {"category": category, "quality_score": score, "lang": "ces" if letters else "unk"}


def _band(clear: int, noisy: int, trash: int) -> Optional[str]:
    if not (clear or noisy or trash):
        return None
    if clear >= noisy and clear >= trash:
        return "Clear"
    return "Noisy" if noisy >= trash else "Trash"


@app.get("/info")
def info() -> Dict[str, Any]:
    return {"service": "atrium-stage-stub", "version": app.version, "stub": True}


@app.post("/predict_document")
async def predict_document(
    file: UploadFile = File(...),
    document_json: Optional[UploadFile] = File(None),
    version: str = Form("all"),
    topn: int = Form(3),
    pages: Optional[str] = Form(None),
) -> Dict[str, Any]:
    content = await file.read()
    record = (
        json.loads((await document_json.read()).decode("utf-8"))
        if document_json is not None
        else None
    )
    total = _pdf_pages(content)
    by_index = {
        int(p.get("page_index")): p
        for p in (record or {}).get("pages") or []
        if p.get("page_index")
    }
    default = os.environ.get("STUB_PAGE_CATEGORY", "TEXT_P")
    flagged = os.environ.get("STUB_NEEDS_OCR_CATEGORY", "TEXT_T")
    results = []
    for index in _pages_param(pages, total):
        row = by_index.get(index) or {}
        label = flagged if row.get("needs_ocr") else default
        entry: Dict[str, Any] = {
            "page": index,
            "predictions": [{"label": label, "score": 0.91}][: max(1, topn)],
        }
        if row.get("page"):
            entry["page_label"] = row["page"]
        results.append(entry)
    response: Dict[str, Any] = {"type": "document", "pages": results, "limits_applied": []}
    if record is not None:
        doc = DocumentRecord(
            str(record.get("doc_id") or "stub"), "page-classification", baseline=record
        )
        categories = {}
        patches = []
        for entry in results:
            key = entry.get("page_label") or str(entry["page"])
            top = entry["predictions"][0]
            categories[key] = top["label"]
            patches.append(
                {"page": key, "category": top["label"], "category_confidence": top["score"]}
            )
        doc.set_block("page_categories", {**(record.get("page_categories") or {}), **categories})
        doc.merge_block(
            "pages", patches, key_fields=["page"], own_fields=["category", "category_confidence"]
        )
        response["document_json"] = doc.to_dict()
    return response


@app.post("/score_record")
async def score_record(document_json: UploadFile = File(...)) -> Dict[str, Any]:
    record = json.loads((await document_json.read()).decode("utf-8"))
    cleaned: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = {}
    empty: Dict[str, int] = {}
    order: List[str] = []
    for row in record.get("lines") or []:
        page = str(row["page"])
        if page not in order:
            order.append(page)
        if row.get("categ") in _DECODE_VERDICTS:
            skipped[page] = skipped.get(page, 0) + 1
            continue
        if not str(row.get("text") or "").strip():
            empty[page] = empty.get(page, 0) + 1
            continue
        cleaned.append(
            {
                "page": str(row["page"]),
                "line": row["line"],
                "text": row.get("text", ""),
                **_score_line(row.get("text", "")),
            }
        )
    pages_summary = []
    by_page: Dict[str, List[Dict[str, Any]]] = {}
    for row in cleaned:
        by_page.setdefault(row["page"], []).append(row)
    page_rows = []
    for page in order:
        rows = by_page.get(page) or []
        score = band = None
        if rows:
            counts = {
                c: sum(1 for r in rows if r["category"] == c) for c in ("Clear", "Noisy", "Trash")
            }
            score = round(sum(r["quality_score"] for r in rows) / len(rows), 4)
            band = _band(counts["Clear"], counts["Noisy"], counts["Trash"])
            page_row = {"page": page, "quality_score": score}
            if band:
                page_row["quality_band"] = band
            page_rows.append(page_row)
        # The shape of ocr-postprocess v1.9.0-beta's ScoredPage.
        pages_summary.append(
            {
                "page": page,
                "lines_scored": len(rows),
                "skipped_decode_verdict": skipped.get(page, 0),
                "skipped_empty": empty.get(page, 0),
                "quality_score": score,
                "quality_band": band,
            }
        )
    doc = DocumentRecord(str(record.get("doc_id") or "stub"), "ocr-postprocess", baseline=record)
    if cleaned:
        doc.merge_block(
            "lines",
            [
                {
                    "page": r["page"],
                    "line": r["line"],
                    "categ": r["category"],
                    "quality_score": r["quality_score"],
                    "lang": r["lang"],
                }
                for r in cleaned
            ],
            own_fields=["categ", "quality_score", "lang"],
        )
        doc.merge_block("pages", page_rows, own_fields=["quality_score", "quality_band"])
    return {
        "type": "record",
        "doc_id": str(record.get("doc_id") or ""),
        "cleaned_lines": cleaned,
        "pages": pages_summary,
        "limits_applied": [],
        "document_json": doc.to_dict(),
        "paradata": None,
    }


@app.post("/process")
async def process(file: UploadFile = File(...), task_type: str = Form("auto")) -> Dict[str, Any]:
    text = (await file.read()).decode("utf-8", "replace")
    lines = [line for line in text.splitlines() if line.strip()]
    cleaned = [
        {"line_num": n, "text": line, **_score_line(line)} for n, line in enumerate(lines, 1)
    ]
    return {
        "type": "plain_text",
        "filename": file.filename,
        "cleaned_lines": cleaned,
        "limits_applied": [],
        "paradata": None,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1", help="bind address (compose passes 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
