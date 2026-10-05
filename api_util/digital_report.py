"""
api_util/digital_report.py — the per-page assessment `/describe` answers with (#2).

For every page of a converted document it says, in one place:

  * **text_layer** — is there an embedded text layer, and can it be taken as it is?
      `digital` a layer that decodes; `garbled` a layer that exists but does not decode
      (mojibake, U+FFFD/control characters); `ocr` the layer is a prior OCR run's invisible text;
      `none` no text layer (a scan, text drawn as curves, an empty page); `blank` an empty page
      of a format without page images (DOCX/ODT/sheets) — nothing to re-acquire.
  * **category** — what kind of page it is, from page-classification (when it ran for the page).
  * **quality** — does the text read as language: ocr-postprocess's common line-quality model
    when it ran (`source: ocr-postprocess`), else the converter's own decode check
    (`source: digital-convert`, which says "decodes", not "reads well").
  * **route** — where the page should go next:
      `nlp`  the text is good to go to NLP enrichment;
      `ocr`  re-acquire the page with ATR (printed or typed text, or unknown);
      `htr`  re-acquire it with a handwriting model;
      `none` there is no text to read (a blank page, a drawing or photo).
  * **layout** and **text** — what the converter found on the page.

Everything here is derived: from the converter's internal document, the record and the stages'
answers. Nothing is stored — the record keeps the persisted signals (`needs_ocr` and its
reason, `category`, the quality fields) and this view is regenerated from them on request.

The route rules are deterministic and deliberately simple (README, "The per-page report"). Page
categories are read through `atrium_vocab.COLLECTIONS["page-category"]` (handwritten / printed /
typed / graphical / tabular), so a category added to the registry is routed without an edit here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_FOOTNOTE,
    REGION_HEADER,
    TEXT_LAYER_BLANK,
    TEXT_LAYER_DIGITAL,
    TEXT_LAYER_GARBLED,
    TEXT_LAYER_NONE,
    TEXT_LAYER_OCR,
    DigitalDocument,
    DigitalPage,
    text_layer_of,
)

ROUTE_NLP = "nlp"
ROUTE_OCR = "ocr"
ROUTE_HTR = "htr"
ROUTE_NONE = "none"
ROUTES = (ROUTE_NLP, ROUTE_OCR, ROUTE_HTR, ROUTE_NONE)

#: The report's text-layer verdicts are `digital_ir.TEXT_LAYERS` (ocr-postprocess's PDF reader uses
#: the same first four), given by `digital_ir.text_layer_of()`; since v1.1.1-beta the record carries
#: the same value in `pages[].text_layer`, so the report and the record cannot disagree.

#: ocr-postprocess's line categories (its `/info` `quality_categories`).
QUALITY_CATEGORIES = ("Clear", "Noisy", "Trash", "Non-text", "Empty")

#: The converter's own decode verdicts on `lines[].categ`.
DECODE_CATEGORIES = ("Garbage", "Inverted")

#: Used when `atrium_vocab` is not importable (it is vendored; this keeps the module standalone).
_FALLBACK_COLLECTIONS: Dict[str, Sequence[str]] = {
    "graphical": ("DRAW", "DRAW_L", "PHOTO", "PHOTO_L", "TEXT"),
    "tabular": ("DRAW_L", "LINE_HW", "LINE_P", "LINE_T", "PHOTO_L"),
    "handwritten": ("LINE_HW", "TEXT", "TEXT_HW"),
    "printed": ("LINE_P", "TEXT", "TEXT_P"),
    "typed": ("LINE_T", "TEXT", "TEXT_T"),
}


def page_category_collections() -> Dict[str, frozenset]:
    """`atrium_vocab`'s page-category facets, as {facet: members}."""
    try:
        from atrium_vocab import COLLECTIONS  # noqa: PLC0415  (vendored, repo root)

        facets = {
            name: tuple(spec["members"]) for name, spec in COLLECTIONS["page-category"].items()
        }
    except (ImportError, KeyError):
        facets = dict(_FALLBACK_COLLECTIONS)
    return {name: frozenset(members) for name, members in facets.items()}


def quality_band(clear: int, noisy: int, trash: int) -> str:
    """Clear/Noisy/Trash counts → the schema's band; the plurality vote ocr-postprocess uses
    (`document_hook.quality_band`): ties favour the more optimistic band."""
    if clear >= noisy and clear >= trash:
        return "Clear"
    if noisy >= trash:
        return "Noisy"
    return "Trash"


def summarize_quality(lines: Iterable[Mapping[str, Any]], **extra: Any) -> Optional[Dict[str, Any]]:
    """One page's quality from ocr-postprocess's per-line answers (`category`/`categ`,
    `quality_score`, `lang`). None when no line was scored."""
    rows = [row for row in lines if (row.get("category") or row.get("categ"))]
    if not rows:
        return None
    counts = Counter(str(row.get("category") or row.get("categ")) for row in rows)
    scores = [
        float(row["quality_score"])
        for row in rows
        if isinstance(row.get("quality_score"), (int, float))
    ]
    langs = Counter(str(row["lang"]) for row in rows if row.get("lang"))
    summary: Dict[str, Any] = {
        "source": "ocr-postprocess",
        "score": round(sum(scores) / len(scores), 4) if scores else None,
        "band": None,
        "lines_by_category": dict(sorted(counts.items())),
        "lang": langs.most_common(1)[0][0] if langs else None,
        "lines_scored": len(rows),
    }
    clear, noisy, trash = counts.get("Clear", 0), counts.get("Noisy", 0), counts.get("Trash", 0)
    if clear or noisy or trash:
        summary["band"] = quality_band(clear, noisy, trash)
    summary.update(extra)
    return summary


def _converter_quality(page: DigitalPage) -> Optional[Dict[str, Any]]:
    if not page.lines:
        return None
    counts = Counter(line.categ for line in page.lines if line.categ in DECODE_CATEGORIES)
    flagged = sum(counts.values())
    return {
        "source": "digital-convert",
        "score": page.quality_score,
        "band": page.quality_band,
        "lines_by_category": {**dict(sorted(counts.items())), "decoded": len(page.lines) - flagged},
        "lang": None,
        "lines_scored": len(page.lines),
    }


def _trash_share(quality: Mapping[str, Any]) -> Optional[float]:
    counts = quality.get("lines_by_category") or {}
    judged = sum(int(counts.get(name, 0)) for name in ("Clear", "Noisy", "Trash"))
    if not judged:
        return None
    return int(counts.get("Trash", 0)) / judged


def route_page(
    text_layer: str,
    category: Optional[str],
    quality: Optional[Mapping[str, Any]],
    trash_share_limit: float = 0.5,
    collections: Optional[Mapping[str, frozenset]] = None,
    drew_anything: bool = True,
) -> tuple:
    """`(route, reason)` for one page. Deterministic; see the module docstring."""
    if text_layer == TEXT_LAYER_BLANK:
        return ROUTE_NONE, "empty page; nothing to read"
    if text_layer == TEXT_LAYER_DIGITAL:
        if quality and quality.get("source") == "ocr-postprocess":
            share = _trash_share(quality)
            if share is not None and share > trash_share_limit:
                return ROUTE_OCR, (
                    f"the text layer decodes, but the quality model calls {share:.0%} of its scored "
                    f"lines Trash (ROUTE_TRASH_SHARE {trash_share_limit:g}): re-acquire it"
                )
            band = quality.get("band")
            return ROUTE_NLP, f"trustworthy text layer{f' (quality {band})' if band else ''}"
        return ROUTE_NLP, "trustworthy text layer (decode check passed; quality model not run)"

    if text_layer == TEXT_LAYER_NONE and not drew_anything and not category:
        return ROUTE_NONE, "no text layer and nothing drawn: a blank page"

    lacking = {
        TEXT_LAYER_GARBLED: "the text layer does not decode",
        TEXT_LAYER_OCR: "the text layer is a prior OCR run",
        TEXT_LAYER_NONE: "no text layer",
    }.get(text_layer, "no usable text layer")
    if not category:
        return (
            ROUTE_OCR,
            f"{lacking}; page type unknown (page-classification not run): default to ATR",
        )
    facets = collections or page_category_collections()
    member_of = {name for name, members in facets.items() if category in members}
    if not member_of:
        return (
            ROUTE_OCR,
            f"{lacking}; page type {category!r} is not in the registry: default to ATR",
        )
    textual = member_of & {"printed", "typed", "handwritten", "tabular"}
    if not textual:
        return (
            ROUTE_NONE,
            f"{lacking}; page type {category} is graphical (a drawing or photo): no text to read",
        )
    if member_of & {"handwritten"} and not member_of & {"printed", "typed"}:
        return ROUTE_HTR, f"{lacking}; page type {category} is handwritten: re-acquire with HTR"
    return (
        ROUTE_OCR,
        f"{lacking}; page type {category} carries printed or typed text: re-acquire with ATR",
    )


def _layout(page: DigitalPage) -> Dict[str, Any]:
    regions = Counter(line.region for line in page.lines if line.region)
    body_columns = {line.column for line in page.lines if line.region is None}
    layout: Dict[str, Any] = {
        "canvas": (
            {"width": page.width, "height": page.height, "unit": page.unit}
            if page.width and page.height
            else None
        ),
        "lines": len(page.lines),
        "blocks": len({line.group_id for line in page.lines if line.group_id}),
        "tables": len(page.tables),
        "headings": sum(1 for line in page.lines if line.heading_level),
        "regions": {
            name: regions.get(name, 0) for name in (REGION_HEADER, REGION_FOOTER, REGION_FOOTNOTE)
        },
        "columns": max(len(body_columns), 1) if page.lines else 0,
        "images": page.images,
        "vector_paths": page.vector_paths,
    }
    return layout


def build_report(
    document: DigitalDocument,
    categories: Optional[Mapping[int, Mapping[str, Any]]] = None,
    quality: Optional[Mapping[int, Mapping[str, Any]]] = None,
    trash_share_limit: float = 0.5,
    include_text: bool = True,
) -> Dict[str, Any]:
    """`{"pages": [...], "summary": {...}}` for a converted document.

    `categories` and `quality` are keyed by `page_index` (1-based): what page-classification
    and ocr-postprocess answered for that page, as `stages.py` normalises it.
    """
    categories = categories or {}
    quality = quality or {}
    collections = page_category_collections()
    pages: List[Dict[str, Any]] = []
    for page in document.pages:
        layer = text_layer_of(page)
        category = categories.get(page.page_index)
        page_quality = quality.get(page.page_index) or _converter_quality(page)
        route, reason = route_page(
            layer,
            (category or {}).get("label"),
            page_quality,
            trash_share_limit=trash_share_limit,
            collections=collections,
            drew_anything=bool(page.images or page.vector_paths or page.lines),
        )
        entry: Dict[str, Any] = {
            "page": page.page,
            "page_index": page.page_index,
            "text_layer": layer,
            "needs_ocr": bool(page.needs_ocr),
            "needs_ocr_reason": page.needs_ocr_reason or None,
            "category": dict(category) if category else None,
            "quality": dict(page_quality) if page_quality else None,
            "route": route,
            "route_reason": reason,
            "layout": _layout(page),
        }
        if include_text:
            entry["text"] = "\n".join(line.text for line in page.lines)
        pages.append(entry)
    return {"pages": pages, "summary": summarize(pages)}


def summarize(pages: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Route counts, the pages that need OCR, and one word for the whole document."""
    routes = Counter(page["route"] for page in pages)
    present = {route for route in routes if route != ROUTE_NONE}
    if not present:
        document_route = ROUTE_NONE
    elif len(present) == 1:
        document_route = next(iter(present))
    else:
        document_route = "mixed"
    return {
        "pages": len(pages),
        "routes": {route: routes.get(route, 0) for route in ROUTES},
        "needs_ocr_pages": [page["page_index"] for page in pages if page.get("needs_ocr")],
        "reacquire_pages": [
            page["page_index"] for page in pages if page["route"] in (ROUTE_OCR, ROUTE_HTR)
        ],
        "document_route": document_route,
    }
