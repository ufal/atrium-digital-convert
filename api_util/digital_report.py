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
    A `digital` page goes to `ocr` instead of `nlp` in two cases (atrium-digital-convert#5):
    when at least ROUTE_DAMAGED_WORD_SHARE of the words of its body text (outside its running
    header and footer) lose a letter to a glyph without a Unicode mapping (U+FFFD: the
    converter's own evidence, so it holds without any stage), and
    when the quality model calls more than ROUTE_TRASH_SHARE of its *prose* lines Trash. A prose
    line is outside a table, at least `PROSE_MIN_CHARS` long, mostly letters and not a dot
    leader, and the page needs `MIN_PROSE_LINES` of them: the model is trained on running text,
    and on born-digital pages its Trash lines are table cells, codes, coordinates and tables of
    contents, whose text is exact (612 of 1,981 AMČR pages were moved for that before #5).
    A `digital` page whose only text is its running header or footer (`furniture_only`) is
    routed by its page type when page-classification gave one: a drawing with a legend or a
    page of text carries its text in the image, which the text layer does not hold (#5: six
    excavation plans whose labels nothing read).
  * **layout** and **text** — what the converter found on the page.

Everything here is derived: from the converter's internal document, the record and the stages'
answers. Nothing is stored — the record keeps the persisted signals (`needs_ocr` and its
reason, `category`, the quality fields) and this view is regenerated from them on request.

The route rules are deterministic and deliberately simple (README, "The per-page report"). Page
categories are read through `atrium_vocab.COLLECTIONS["page-category"]` (handwritten / printed /
typed / graphical / tabular), so a category added to the registry is routed without an edit here.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_FOOTNOTE,
    REGION_HEADER,
    REPLACEMENT_CHAR,
    TEXT_LAYER_BLANK,
    TEXT_LAYER_DIGITAL,
    TEXT_LAYER_GARBLED,
    TEXT_LAYER_NONE,
    TEXT_LAYER_OCR,
    DigitalDocument,
    DigitalPage,
    in_table,
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

#: What the quality rule counts on a `digital` page: prose lines only (atrium-digital-convert#5).
#: A line shorter than this (stripped) is not prose: a table cell, a label, a page number.
PROSE_MIN_CHARS = 20
#: Letters must be at least this share of a prose line's non-space characters: coordinates,
#: find numbers and photo IDs ("DR-12982-18711;4990") are not language.
PROSE_MIN_LETTER_SHARE = 0.5
#: A table-of-contents line: dots, ellipses or underscores leading to a page number.
DOT_LEADERS = re.compile(r"(\.\s?){4,}|…{2,}|_{4,}")
#: Prose lines a page needs before the quality model's verdict on them can move it to OCR.
MIN_PROSE_LINES = 5
#: Why a line is or is not prose: the first test it fails, in this order (`line_kind`).
LINE_KINDS = ("table", "short", "few letters", "dot leaders", "prose")
#: How a quality-rule reroute's `route_reason` starts (`tools/samples_report.py` counts them).
QUALITY_ROUTE_PREFIX = "the text layer decodes, but the quality model calls"
#: How a lost-letter reroute's `route_reason` starts.
DAMAGED_ROUTE_PREFIX = "the text layer decodes, but"


def line_kind(text: str, group_id: Optional[str] = None) -> str:
    """The kind of a line for the quality rule: one of `LINE_KINDS`.

    `table` when it is a table cell (`group_id` `tbl…`), `short` under `PROSE_MIN_CHARS`,
    `few letters` when letters are under `PROSE_MIN_LETTER_SHARE` of its non-space characters,
    `dot leaders` for a table-of-contents line, else `prose`.
    """
    if in_table(group_id):
        return "table"
    stripped = str(text or "").strip()
    if len(stripped) < PROSE_MIN_CHARS:
        return "short"
    dense = "".join(stripped.split())
    if sum(char.isalpha() for char in dense) < PROSE_MIN_LETTER_SHARE * len(dense):
        return "few letters"
    if DOT_LEADERS.search(stripped):
        return "dot leaders"
    return "prose"


def is_prose_line(text: str, group_id: Optional[str] = None) -> bool:
    """Whether the quality rule counts this line (`line_kind` is `prose`)."""
    return line_kind(text, group_id) == "prose"


def count_damaged_words(texts: Iterable[str]) -> Tuple[int, int]:
    """`(damaged, words)` over lines of text: whitespace tokens, and those of them that hold
    U+FFFD and a letter — a word that lost a letter to a glyph without a Unicode mapping
    ("poh�ební"). A token of U+FFFD alone is an unmapped symbol (a bullet), not a damaged word.
    """
    damaged = words = 0
    for text in texts:
        for token in str(text or "").split():
            words += 1
            if REPLACEMENT_CHAR in token and any(char.isalpha() for char in token):
                damaged += 1
    return damaged, words


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


def summarize_quality(
    lines: Iterable[Mapping[str, Any]],
    prose: Optional[Sequence[bool]] = None,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """One page's quality from ocr-postprocess's per-line answers (`category`/`categ`,
    `quality_score`, `lang`). None when no line was scored.

    `prose`, one flag per line of `lines` (`is_prose_line` of the line the converter sent),
    adds `prose_lines_scored` and `prose_lines_by_category`: what the quality rule of
    `route_page` counts. Flags that do not match the lines one to one are ignored.
    """
    every = list(lines)
    flags = list(prose) if prose is not None and len(prose) == len(every) else None
    rows = [row for row in every if (row.get("category") or row.get("categ"))]
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
    if flags is not None:
        prose_counts = Counter(
            str(row.get("category") or row.get("categ"))
            for row, flag in zip(every, flags, strict=True)
            if flag and (row.get("category") or row.get("categ"))
        )
        summary["prose_lines_scored"] = sum(prose_counts.values())
        summary["prose_lines_by_category"] = dict(sorted(prose_counts.items()))
    summary.update(extra)
    return summary


def furniture_only(page: DigitalPage) -> bool:
    """Whether a page's only text is its running header or footer, over something it draws.

    Such a page's content is an image or a drawing: a figure page with a running header, or a
    scan the PDF's producer numbered. Below `FURNITURE_ONLY_IMAGE_COVERAGE` it stays `digital`,
    and only its page type tells whether the image holds text (`route_page`).
    """
    return (
        bool(page.lines)
        and all(line.region in (REGION_HEADER, REGION_FOOTER) for line in page.lines)
        and bool(page.images or page.vector_paths)
    )


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


def _judged(counts: Optional[Mapping[str, Any]]) -> Tuple[int, int]:
    """`(judged, trash)`: the Clear/Noisy/Trash lines of a `lines_by_category`-style count."""
    counts = counts or {}
    judged = sum(int(counts.get(name, 0) or 0) for name in ("Clear", "Noisy", "Trash"))
    return judged, int(counts.get("Trash", 0) or 0)


def _quality_route(
    quality: Mapping[str, Any], trash_share_limit: float, min_prose_lines: int
) -> Tuple[str, str]:
    """The quality rule for a `digital` page that ocr-postprocess scored (module docstring)."""
    band = quality.get("band")
    judged, trash = _judged(quality.get("lines_by_category"))
    over_all = bool(judged) and trash / judged > trash_share_limit
    if "prose_lines_by_category" not in quality:
        # A caller that does not know which lines are prose: the rule over every scored line.
        if over_all:
            return ROUTE_OCR, (
                f"{QUALITY_ROUTE_PREFIX} {trash / judged:.0%} of its scored lines Trash "
                f"(ROUTE_TRASH_SHARE {trash_share_limit:g}): re-acquire it"
            )
        return ROUTE_NLP, f"trustworthy text layer{f' (quality {band})' if band else ''}"
    prose_judged, prose_trash = _judged(quality.get("prose_lines_by_category"))
    if prose_judged >= min_prose_lines and prose_trash / prose_judged > trash_share_limit:
        return ROUTE_OCR, (
            f"{QUALITY_ROUTE_PREFIX} {prose_trash / prose_judged:.0%} of its prose lines Trash "
            f"({prose_trash} of {prose_judged}; ROUTE_TRASH_SHARE {trash_share_limit:g}): "
            "re-acquire it"
        )
    reason = f"trustworthy text layer (quality {band})" if band else "trustworthy text layer"
    if not over_all:
        return ROUTE_NLP, reason
    # The model calls most lines Trash, but not the prose ones: say why the page stays.
    if prose_judged >= min_prose_lines:
        why = f"{prose_trash} of its {prose_judged} prose lines Trash"
    else:
        why = f"{prose_judged} prose line(s), fewer than {min_prose_lines} to judge"
    return ROUTE_NLP, (
        f"{reason}; its Trash lines are table cells, short lines, numbers or dot leaders ({why})"
    )


def _route_by_category(
    category: str, collections: Optional[Mapping[str, frozenset]], lacking: str
) -> Optional[Tuple[str, str]]:
    """`(route, reason)` for a page whose text must come from its image, by its page type:
    `htr` for handwriting only, `ocr` for printed, typed or tabular text, `none` for a drawing or
    photo. None when the type is not in the registry."""
    facets = collections or page_category_collections()
    member_of = {name for name, members in facets.items() if category in members}
    if not member_of:
        return None
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


def route_page(
    text_layer: str,
    category: Optional[str],
    quality: Optional[Mapping[str, Any]],
    trash_share_limit: float = 0.5,
    collections: Optional[Mapping[str, frozenset]] = None,
    drew_anything: bool = True,
    damaged_words: Optional[Tuple[int, int]] = None,
    damaged_word_limit: float = 0.05,
    min_prose_lines: int = MIN_PROSE_LINES,
    furniture_only: bool = False,
) -> tuple:
    """`(route, reason)` for one page. Deterministic; see the module docstring.

    `damaged_words` is the page's `count_damaged_words` (`(damaged, words)`): a `digital` page
    at or over `damaged_word_limit` goes to `ocr` whatever the quality model says.
    `furniture_only` (the module's `furniture_only()`): a `digital` page whose only text is its
    running header or footer goes where its page type says, when that type carries text.
    """
    if text_layer == TEXT_LAYER_BLANK:
        return ROUTE_NONE, "empty page; nothing to read"
    if text_layer == TEXT_LAYER_DIGITAL:
        damaged, words = damaged_words or (0, 0)
        if words and damaged and damaged / words >= damaged_word_limit:
            return ROUTE_OCR, (
                f"{DAMAGED_ROUTE_PREFIX} {damaged} of its {words} words ({damaged / words:.0%}) "
                "lose a letter to a glyph without a Unicode mapping (U+FFFD; "
                f"ROUTE_DAMAGED_WORD_SHARE {damaged_word_limit:g}): re-acquire it"
            )
        if furniture_only and category:
            routed = _route_by_category(
                category,
                collections,
                "the text layer holds only the page's running header or footer",
            )
            if routed and routed[0] in (ROUTE_OCR, ROUTE_HTR):
                return routed[0], routed[1].replace(" text:", " text in its image:", 1)
        if quality and quality.get("source") == "ocr-postprocess":
            return _quality_route(quality, trash_share_limit, min_prose_lines)
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
    routed = _route_by_category(category, collections, lacking)
    if routed is None:
        return (
            ROUTE_OCR,
            f"{lacking}; page type {category!r} is not in the registry: default to ATR",
        )
    return routed


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
    # Body text only: a figure page whose only text is a running header that lost a letter has
    # nothing to re-acquire (36 such pages in one AMČR report, #5).
    damaged, words = count_damaged_words(
        line.text for line in page.lines if line.region not in (REGION_HEADER, REGION_FOOTER)
    )
    layout["words"], layout["damaged_words"] = words, damaged
    return layout


def build_report(
    document: DigitalDocument,
    categories: Optional[Mapping[int, Mapping[str, Any]]] = None,
    quality: Optional[Mapping[int, Mapping[str, Any]]] = None,
    trash_share_limit: float = 0.5,
    include_text: bool = True,
    damaged_word_limit: float = 0.05,
) -> Dict[str, Any]:
    """`{"pages": [...], "summary": {...}}` for a converted document.

    `categories` and `quality` are keyed by `page_index` (1-based): what page-classification
    and ocr-postprocess answered for that page, as `stages.py` normalises it.
    `trash_share_limit` and `damaged_word_limit` are ROUTE_TRASH_SHARE and
    ROUTE_DAMAGED_WORD_SHARE (`tool_limits.py`).
    """
    categories = categories or {}
    quality = quality or {}
    collections = page_category_collections()
    pages: List[Dict[str, Any]] = []
    for page in document.pages:
        layer = text_layer_of(page)
        category = categories.get(page.page_index)
        page_quality = quality.get(page.page_index) or _converter_quality(page)
        layout = _layout(page)
        route, reason = route_page(
            layer,
            (category or {}).get("label"),
            page_quality,
            trash_share_limit=trash_share_limit,
            collections=collections,
            drew_anything=bool(page.images or page.vector_paths or page.lines),
            damaged_words=(layout["damaged_words"], layout["words"]),
            damaged_word_limit=damaged_word_limit,
            furniture_only=furniture_only(page),
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
            "layout": layout,
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
