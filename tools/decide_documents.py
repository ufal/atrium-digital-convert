#!/usr/bin/env python3
"""tools/decide_documents.py — the AMČR document type of PDF pages and documents (#8; ufal/atrium-project#83).

An offline experiment CLI, outside the service and its images. It renders each page of each PDF,
sends it to a decision model served at ``POST /v1/systemone`` (``decision_client.py``, vendored from
atrium-page-classification) and writes what the model chose from AMČR's ``dokument_typ`` list
(``decisions/document_type.v2.json``, its ``pdf_page`` view: ``group``, 6 options, and
``document_type``, 32 types and ``unspecified``, the abstain option).

**The input is the page image.** By default the request carries the rendered page and nothing else.
``--with-text`` adds the page's text layer (pypdfium2's, cut to ``--max-chars`` at a line break), so
the two can be compared on the same pages; the text is untrusted input and never changes anything.

Two levels, each one request per item, with the same two questions:

* **page** (``DECIDE_PAGES.csv``): one rendered page;
* **document** (``DECIDE_DOCS.csv``): the first ``--doc-pages`` pages (at most 4, a request's image
  limit), with ``--with-text`` their text layers joined.

The page answers are never merged into a document answer: which of the two is right is what the
evaluation finds out (the #8 plan, Phase C). ``--gold`` (``file,document_type``) scores the documents
and ``--page-gold`` (``file,page,document_type``) the pages; a type may be given as its id, its HES
number or its Czech label. Beside the tables: ``decide_documents.summary.json`` and a paradata record
(``decision_model``). The server is recorded by kind (``--served-on``), never by its URL. Run it from
the repository root::

    python3 tools/decide_documents.py --input <folder of PDFs> --out-dir <dir> \\
        --model clef-oQ8e --served-on cluster-job                 # URL from $DECISION_MODEL_URL
    python3 tools/decide_documents.py --input <pdf> --out-dir <dir> --with-text --level page

Which PDFs may be sent to which server is settled on ufal/atrium-project#83: the AMČR samples stay on
the cluster. Nothing reaches a record: a ``document-type`` scheme waits for AMČR's agreement.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import decision_client as dc  # noqa: E402 — vendored from atrium-page-classification

LIST = REPO_ROOT / "decisions" / "document_type.v2.json"
VIEW = "pdf_page"
PROGRAM = "digital-convert"
LEVELS = ("page", "document", "both")
SERVED_ON = ("cluster-job", "ufal-endpoint", "local", "other")
DOCUMENT_ITEM = "the attached images of the first {sent} of the {total} pages of one PDF from an archaeological archive, in order"


# ── the list ───────────────────────────────────────────────────────────────────────────────────


def load_view(path: Path = LIST, view: str = VIEW) -> Dict[str, Any]:
    """One view of the type list, its questions checked and their digest recomputed."""
    content = json.loads(path.read_text(encoding="utf-8"))
    entry = content["views"][view]
    questions = dc.questions(entry["questions"])
    if dc.schema_digest(questions) != entry["schema_digest"]:
        raise SystemExit(
            f"{path}: the {view} questions do not match their digest; regenerate the list"
        )
    return {
        "questions": questions,
        "item": entry["item"],
        "inputs": entry["inputs"],
        "schema_digest": entry["schema_digest"],
        "version": content["version"],
        "types": content["types"],
        "groups": list(content["groups"]),
        "abstain": content["abstain"],
    }


def type_lookup(types: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    """Id, HES number or Czech label (case-folded) -> type id."""
    out: Dict[str, str] = {}
    for t in types:
        for key in (t["id"], t["hes"], t["cs"]):
            out[str(key).casefold()] = t["id"]
    return out


def read_gold(
    path: Path, types: Sequence[Mapping[str, Any]], pages: bool = False
) -> Dict[Tuple[str, ...], str]:
    """``(file,) -> type`` or, with *pages*, ``(file, page) -> type`` from a gold CSV."""
    lookup = type_lookup(types)
    gold: Dict[Tuple[str, ...], str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = str(row.get("document_type", "")).strip()
            if not value:
                continue
            if value.casefold() not in lookup:
                raise SystemExit(f"{path}: {value!r} is not a type of the list")
            key = (
                (Path(row["file"]).stem, str(int(row["page"])))
                if pages
                else (Path(row["file"]).stem,)
            )
            gold[key] = lookup[value.casefold()]
    return gold


# ── the pages ──────────────────────────────────────────────────────────────────────────────────


def _text_layer(page: Any, max_chars: int) -> str:
    """The page's text layer, whole lines up to *max_chars*."""
    raw = page.get_textpage().get_text_range() or ""
    lines = [line.strip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    kept: List[str] = []
    size = 0
    for line in (x for x in lines if x):
        extra = len(line) + (1 if kept else 0)
        if size + extra > max_chars:
            break
        kept.append(line)
        size += extra
    return "\n".join(kept)


def render_pages(
    pdf: Path,
    max_side: int = 1024,
    with_text: bool = False,
    max_chars: int = 3000,
    limit: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """``{"page", "pages", "image", "text"}`` per page, rendered with pypdfium2 at *max_side* (long side)."""
    import pypdfium2 as pdfium  # noqa: PLC0415 — optional dependency (requirements_digital.txt)

    document = pdfium.PdfDocument(str(pdf))
    try:
        total = len(document)
        for index in range(total if limit is None else min(total, limit)):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = max_side / max(width, height, 1.0)
                image = page.render(scale=scale).to_pil().convert("RGB")
                text = _text_layer(page, max_chars) if with_text else None
            finally:
                page.close()
            yield {"page": index + 1, "pages": total, "image": image, "text": text}
    finally:
        document.close()


def page_state(view: Mapping[str, Any], text: Optional[str]) -> Dict[str, Any]:
    """The state of a page request: the item, and the text layer only when asked for."""
    state: Dict[str, Any] = {"item": view["item"]}
    if text is not None:
        state["text"] = text
    return state


def document_state(
    view: Mapping[str, Any], sent: int, total: int, texts: Optional[Sequence[str]]
) -> Dict[str, Any]:
    """The state of a document request: the item names how many of its pages are attached; with
    *texts*, their text layers, each under a ``[page N]`` line."""
    state: Dict[str, Any] = {"item": DOCUMENT_ITEM.format(sent=sent, total=total)}
    if texts is not None:
        state["text"] = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(texts, start=1))
    return state


# ── the answers ────────────────────────────────────────────────────────────────────────────────


def columns(view: Mapping[str, Any], level: str) -> List[str]:
    """The header of ``DECIDE_PAGES.csv`` (*level* ``page``) or ``DECIDE_DOCS.csv``: the key, the
    answers with one ``P_group_*`` column per group and one ``P_type_*`` per type, the gold, then the
    request's status and operations."""
    key = ["file", "page", "pages"] if level == "page" else ["file", "pages", "pages_sent"]
    groups = [f"P_group_{g}" for g in view["groups"]]
    types = [f"P_type_{t['id']}" for t in view["types"]]
    return (
        key
        + ["with_text", "chars", "group"]
        + groups
        + ["document_type", "type_group", "confidence", "top3", "lane", "gold_type", "gold_group"]
        + types
        + ["status", "error", "latency_ms", "input_tokens", "attempts", "schema_digest", "max_side"]
    )


def answer_fields(
    d: dc.Decision,
    view: Mapping[str, Any],
    gold_type: str = "",
    accept_at: Optional[float] = None,
    review_below: Optional[float] = None,
) -> Dict[str, Any]:
    """The answer columns of one row: the group and the type with their probabilities, the top three
    types, the lane when both thresholds are given, the gold, and the request's status. A failed
    request gives empty answers."""
    group_of = {t["id"]: t["group"] for t in view["types"]}
    group, kind = d.answers.get("group"), d.answers.get("document_type")
    row: Dict[str, Any] = {"group": group.choice if group else ""}
    for g in view["groups"]:
        row[f"P_group_{g}"] = group.probabilities.get(g, "") if group else ""
    row["document_type"] = kind.choice if kind else ""
    row["type_group"] = group_of.get(row["document_type"], "")
    row["confidence"] = kind.confidence if kind else ""
    best = sorted(kind.probabilities.items(), key=lambda item: -item[1])[:3] if kind else []
    row["top3"] = "|".join(f"{k}:{p:.4f}" for k, p in best)
    row["lane"] = (
        dc.lane(kind.confidence, accept_at, review_below)
        if kind and accept_at is not None and review_below is not None
        else ""
    )
    row["gold_type"] = gold_type
    row["gold_group"] = group_of.get(gold_type, "")
    for t in view["types"]:
        row[f"P_type_{t['id']}"] = kind.probabilities.get(t["id"], "") if kind else ""
    row.update(
        {
            "status": d.status,
            "error": d.error or "",
            "latency_ms": d.latency_ms if d.latency_ms is not None else "",
            "input_tokens": d.input_tokens if d.input_tokens is not None else "",
            "attempts": d.attempts,
            "schema_digest": d.schema_digest,
        }
    )
    return row


# ── the run ────────────────────────────────────────────────────────────────────────────────────


def input_pdfs(path: Path) -> List[Path]:
    """*path* itself if it is a file, else every PDF under it at any depth (``.pdf`` in any case), sorted."""
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.suffix.lower() == ".pdf")


def _done(path: Path, key: Sequence[str]) -> set:
    if not path.is_file():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {tuple(row[k] for k in key) for row in csv.DictReader(handle)}


def _writer(path: Path, fields: List[str], resume: bool):
    if path.exists() and not resume:
        raise SystemExit(f"{path} exists; pass --resume to continue it or choose another --out-dir")
    new = not path.exists()
    handle = path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
    if new:
        writer.writeheader()
    return handle, writer


def run(
    client: dc.DecisionClient,
    pdfs: Sequence[Path],
    out_dir: Path,
    view: Mapping[str, Any],
    level: str = "both",
    with_text: bool = False,
    max_side: int = 1024,
    max_chars: int = 3000,
    doc_pages: int = dc.MAX_IMAGES,
    gold: Optional[Mapping[Tuple[str, ...], str]] = None,
    page_gold: Optional[Mapping[Tuple[str, ...], str]] = None,
    accept_at: Optional[float] = None,
    review_below: Optional[float] = None,
    resume: bool = False,
    progress: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """Decide every page and/or document; rows are appended to the two tables as they come."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # main() refuses a --doc-pages outside 1..MAX_IMAGES; a programmatic caller is held to what one
    # request takes instead.
    doc_pages = max(1, min(doc_pages, dc.MAX_IMAGES))
    pages_path, docs_path = out_dir / "DECIDE_PAGES.csv", out_dir / "DECIDE_DOCS.csv"
    want_pages, want_docs = level in ("page", "both"), level in ("document", "both")
    done_pages = _done(pages_path, ("file", "page")) if resume else set()
    done_docs = _done(docs_path, ("file",)) if resume else set()
    new: Dict[str, List[Dict[str, Any]]] = {"page": [], "document": []}
    handles = []
    try:
        if want_pages:
            page_handle, page_writer = _writer(pages_path, columns(view, "page"), resume)
            handles.append(page_handle)
        if want_docs:
            doc_handle, doc_writer = _writer(docs_path, columns(view, "document"), resume)
            handles.append(doc_handle)
        for number, pdf in enumerate(pdfs, start=1):
            stem = pdf.stem
            sent_images: List[Any] = []
            sent_texts: List[str] = []
            total = 0
            need_doc = want_docs and (stem,) not in done_docs
            limit = None if want_pages else doc_pages
            try:
                rendered = list(render_pages(pdf, max_side, with_text, max_chars, limit))
            except (
                Exception
            ) as exc:  # a PDF pdfium cannot open: one rejected row per level, the run goes on
                failed = dc.Decision(
                    "rejected", view["schema_digest"], error=f"render: {type(exc).__name__}: {exc}"
                )
                if want_docs and need_doc:
                    row = {
                        "file": stem,
                        "pages": "",
                        "pages_sent": 0,
                        "with_text": int(with_text),
                        "chars": "",
                    }
                    row.update(answer_fields(failed, view, (gold or {}).get((stem,), "")))
                    row["max_side"] = max_side
                    doc_writer.writerow(row)
                    new["document"].append(row)
                continue
            for item in rendered:
                total = item["pages"]
                if len(sent_images) < doc_pages:
                    sent_images.append(item["image"])
                    if with_text:
                        sent_texts.append(item["text"] or "")
                if not want_pages or (stem, str(item["page"])) in done_pages:
                    continue
                d = client.decide(
                    page_state(view, item["text"]), view["questions"], [item["image"]], max_side
                )
                row = {
                    "file": stem,
                    "page": item["page"],
                    "pages": item["pages"],
                    "with_text": int(with_text),
                    "chars": len(item["text"]) if item["text"] is not None else "",
                }
                row.update(
                    answer_fields(
                        d,
                        view,
                        (page_gold or {}).get((stem, str(item["page"])), ""),
                        accept_at,
                        review_below,
                    )
                )
                row["max_side"] = max_side
                page_writer.writerow(row)
                page_handle.flush()
                new["page"].append(row)
            if need_doc and sent_images:
                texts = sent_texts if with_text else None
                state = document_state(view, len(sent_images), total, texts)
                d = client.decide(state, view["questions"], sent_images, max_side)
                row = {
                    "file": stem,
                    "pages": total,
                    "pages_sent": len(sent_images),
                    "with_text": int(with_text),
                    "chars": len(state["text"]) if texts is not None else "",
                }
                row.update(
                    answer_fields(d, view, (gold or {}).get((stem,), ""), accept_at, review_below)
                )
                row["max_side"] = max_side
                doc_writer.writerow(row)
                doc_handle.flush()
                new["document"].append(row)
            if progress:
                print(f"[{number}/{len(pdfs)}] {pdf.name}: {total} pages", file=sys.stderr)
    finally:
        for handle in handles:
            handle.close()
    return new


# ── the summary ────────────────────────────────────────────────────────────────────────────────


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def summarise(rows: Sequence[Mapping[str, Any]], abstain: str) -> Dict[str, Any]:
    """Operations, the answers' spread and, where gold is given, type and group accuracy."""
    ok = [r for r in rows if r.get("status") == "ok"]
    latencies = [float(r["latency_ms"]) for r in ok if str(r.get("latency_ms", "")) != ""]
    tokens = [float(r["input_tokens"]) for r in ok if str(r.get("input_tokens", "")) != ""]
    out: Dict[str, Any] = {
        "rows": len(rows),
        "statuses": dict(Counter(str(r.get("status")) for r in rows)),
        "latency_ms_p50": _pct(latencies, 0.5),
        "latency_ms_p95": _pct(latencies, 0.95),
        "input_tokens_mean": round(statistics.fmean(tokens), 1) if tokens else None,
        "groups": dict(Counter(r["group"] for r in ok)),
        "types": dict(Counter(r["document_type"] for r in ok)),
        "abstained": round(sum(r["document_type"] == abstain for r in ok) / len(ok), 4)
        if ok
        else None,
        "group_disagrees_with_type": sum(r["group"] != r["type_group"] for r in ok),
    }
    graded = [r for r in ok if r.get("gold_type")]
    if graded:
        out["gold"] = {
            "rows": len(graded),
            "type_accuracy": round(
                sum(r["document_type"] == r["gold_type"] for r in graded) / len(graded), 4
            ),
            "group_accuracy": round(
                sum(r["group"] == r["gold_group"] for r in graded) / len(graded), 4
            ),
            "type_group_accuracy": round(
                sum(r["type_group"] == r["gold_group"] for r in graded) / len(graded), 4
            ),
        }
    return out


def read_rows(path: Path) -> List[Dict[str, str]]:
    """The rows of a table this tool wrote, earlier runs' included; none when it does not exist."""
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def served_on(url: Optional[str], given: Optional[str] = None) -> str:
    """Where the pages went, for the paradata, never the URL: the internal endpoint is not published."""
    if given:
        return given
    from urllib.parse import urlsplit  # noqa: PLC0415

    host = urlsplit(url or "").hostname or ""
    return "local" if host in ("127.0.0.1", "localhost", "::1") else "other"


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", type=Path, required=True, help="a PDF or a folder of PDFs")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--level", choices=LEVELS, default="both")
    parser.add_argument(
        "--with-text", action="store_true", help="add each page's text layer (off: the image alone)"
    )
    parser.add_argument(
        "--max-side", type=int, default=1024, help="the rendered page's long side, px"
    )
    parser.add_argument(
        "--max-chars", type=int, default=3000, help="the text layer per page, with --with-text"
    )
    parser.add_argument(
        "--doc-pages", type=int, default=dc.MAX_IMAGES, help="pages per document request (1 to 4)"
    )
    parser.add_argument("--gold", type=Path, help="documents: file,document_type")
    parser.add_argument("--page-gold", type=Path, help="pages: file,page,document_type")
    parser.add_argument(
        "--accept-at", type=float, help="lane threshold, 0 to 1 (set on dev; with --review-below)"
    )
    parser.add_argument(
        "--review-below", type=float, help="lane threshold, 0 to --accept-at (set on dev)"
    )
    parser.add_argument("--resume", action="store_true", help="continue the tables in --out-dir")
    server = parser.add_argument_group("server")
    server.add_argument(
        "--url", default=os.environ.get("DECISION_MODEL_URL"), help="($DECISION_MODEL_URL)"
    )
    server.add_argument(
        "--model", default=os.environ.get("DECISION_MODEL_NAME"), help="($DECISION_MODEL_NAME)"
    )
    server.add_argument(
        "--model-revision", default="", help="the served build or HF revision, for the paradata"
    )
    server.add_argument(
        "--served-on", choices=SERVED_ON, help="where the pages go (default: local for loopback)"
    )
    server.add_argument("--timeout", type=float, default=_env_float("DECISION_TIMEOUT_S", 120.0))
    server.add_argument("--retries", type=int, default=2)
    server.add_argument("--image-style", default="data_uri", choices=dc.IMAGE_STYLES)
    server.add_argument("--no-proxy", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Refused here, before any request: decision_client.lane() raises on a bad pair only at the
    # first answer, after a paid model call, and a clamped --doc-pages would leave the paradata
    # recording a value the run did not use.
    if (args.accept_at is None) != (args.review_below is None):
        parser.error("--accept-at and --review-below go together")
    if args.accept_at is not None:
        for flag, value in (("--accept-at", args.accept_at), ("--review-below", args.review_below)):
            if not 0.0 <= value <= 1.0:
                parser.error(f"{flag} is a probability, between 0 and 1 (got {value})")
        if args.review_below > args.accept_at:
            parser.error(
                f"--review-below ({args.review_below}) must not exceed --accept-at ({args.accept_at})"
            )
    if not 1 <= args.doc_pages <= dc.MAX_IMAGES:
        parser.error(
            f"--doc-pages is 1 to {dc.MAX_IMAGES}, a request's image limit (got {args.doc_pages})"
        )
    pdfs = input_pdfs(args.input)
    if not pdfs:
        parser.error(f"no PDF at {args.input}")
    client = dc.DecisionClient(
        args.url,
        args.model,
        timeout_s=args.timeout,
        retries=args.retries,
        image_style=args.image_style,
        use_env_proxy=not args.no_proxy,
    )
    if not client.endpoint or not client.model:
        parser.error("set --url/--model (or DECISION_MODEL_URL/DECISION_MODEL_NAME)")
    view = load_view()
    gold = read_gold(args.gold, view["types"]) if args.gold else None
    page_gold = read_gold(args.page_gold, view["types"], pages=True) if args.page_gold else None
    where = served_on(args.url, args.served_on)

    from atrium_paradata import ParadataLogger  # noqa: PLC0415

    paradata = ParadataLogger(
        PROGRAM,
        {
            "script": "tools/decide_documents.py",
            "decision_model": args.model,
            "decision_revision": args.model_revision,
            "served_on": where,
            "decision_schema_digest": view["schema_digest"],
            "list": f"decisions/document_type.v{view['version']}.json ({VIEW})",
            "level": args.level,
            "with_text": args.with_text,
            "max_side": args.max_side,
            "max_chars": args.max_chars if args.with_text else None,
            "doc_pages": args.doc_pages,
            "documents": len(pdfs),
        },
        paradata_dir=str(args.out_dir / "paradata"),
        config_dir=REPO_ROOT,
    )
    paradata.log_component("decision_model")
    paradata.log_component("pypdfium2")
    new = run(
        client,
        pdfs,
        args.out_dir,
        view,
        args.level,
        args.with_text,
        args.max_side,
        args.max_chars,
        args.doc_pages,
        gold,
        page_gold,
        args.accept_at,
        args.review_below,
        args.resume,
        progress=True,
    )
    summary: Dict[str, Any] = {
        "tool": "tools/decide_documents.py",
        "model": args.model,
        "model_revision": args.model_revision,
        "served_on": where,
        "schema_digest": view["schema_digest"],
        "with_text": args.with_text,
        "max_side": args.max_side,
    }
    if args.level in ("page", "both"):
        summary["pages"] = summarise(read_rows(args.out_dir / "DECIDE_PAGES.csv"), view["abstain"])
    if args.level in ("document", "both"):
        summary["documents"] = summarise(
            read_rows(args.out_dir / "DECIDE_DOCS.csv"), view["abstain"]
        )
    (args.out_dir / "decide_documents.summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    paradata.log_success("csv", len(new["page"]) + len(new["document"]))
    ok = sum(r["status"] == "ok" for rows in new.values() for r in rows)
    paradata.finalize(input_total=len(pdfs), processed_total=ok)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k in ("pages", "documents")},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
