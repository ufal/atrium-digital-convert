"""
api_util/digital_pdf.py — the light PDF engine of `digital_to_json.py` (Layer A, Issue #18).

pdfplumber (MIT) for words, geometry, fonts and ruled tables; pypdfium2 (Apache-2.0 /
BSD-3-Clause, already a pdfplumber dependency) for the per-page census pdfplumber cannot
give: page labels and the text render mode that marks an OCR layer. Both are imported
lazily — the base install and the fast lane never pay for them.

What this module fixes, against the #10 §9 measurement (gap register G1, G2, G7):

* **Words, not characters (G2).** Lines used to be every char with the same `round(top)`,
  joined with "". Words separated by positioning rather than a space character came out
  glued (`MALASTRANA'SMFF`), and the two columns of a page merged into one line. Now words
  come from `extract_words()` (a gap wider than a fraction of the font size is a word
  break), a line is split wherever a column-sized gap opens, and a page is read band by
  band, column by column.
* **Pages without text (G1).** A page with no text layer is `text_layer = "none"`, which
  Layer B turns into `needs_ocr` with the reason "no extractable text layer" — naming what the
  page draws (images, vector paths: a scan, curves-for-letters) or that it draws nothing a
  parser can see. alto-postprocess #31 classifies such a page the same way.
* **OCR layers (G7).** Invisible text (render mode 3) over a page image is a prior OCR
  run's output: `text_layer = "ocr"`. The document-level decision (refuse or flag) is
  `digital_to_json`'s, mirroring atrium-alto-postprocess `default_source_origin` (#31).

And the layout cues the #18 mapping table promises: running headers and footers
(`style.region`), headings by font size (`style.heading_level`), ruled tables (`tables[]`,
the legacy `pdf_to_md` had them), PDF page labels (`iv`, `A-1`) as `pages[].page`.

The AMČR samples (atrium-digital-convert#5) added what born-digital reports from Word and
Acrobat do to words: a font switch inside a word, small capitals set in two sizes, and text on
a 90° baseline are joined back into words (`_merge_split_words`), and headings are judged
against the body text of `digital` pages only (`_assign_headings`).

Coordinates stay pdfplumber's `top`/`bottom` pair — top-left origin, points — exactly what
`$defs/bbox` requires; the bottom-up `y0`/`y1` are never read.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_HEADER,
    TABLE_GROUP_PREFIX,
    TEXT_LAYER_DIGITAL,
    TEXT_LAYER_NONE,
    TEXT_LAYER_OCR,
    DigitalDocument,
    DigitalInputError,
    DigitalLine,
    DigitalPage,
    DigitalTable,
    font_flags,
    in_table,
    is_inverted,
    missing_dependency,
    renumber_lines,
    sha256_file,
    unmapped_to_replacement,
)
from api_util.pdf_coverage import image_coverage

#: A gap wider than this share of the font size starts a new word. pdfplumber's default is
#: an absolute 3 pt, which glues words set with tight justification in small type and
#: splits letter-spaced headings in large type; a ratio holds across sizes.
WORD_GAP_RATIO = 0.2

#: The default of `FURNITURE_ONLY_IMAGE_COVERAGE` (tool_limits.py): the share of a page its
#: images must cover for a page whose only text is its running header or footer to be `none`.
#: Measured on the AMČR samples (#5): it catches the two pages there that are a page number over
#: a scan, and none of the 36 pages of one report that carry a running header over two figures.
FURNITURE_ONLY_IMAGE_COVERAGE = 0.85

#: Two neighbouring words on one baseline are one word set in two fonts (see
#: `_merge_split_words`) when the gap between them is at most this share of the font size —
#: the word-gap tolerance itself — and they overlap by at most `MERGE_OVERLAP_RATIO` of it.
FONT_SWITCH_GAP_RATIO = WORD_GAP_RATIO
MERGE_OVERLAP_RATIO = 0.25

#: Small capitals emulated by the producer (Word, Distiller): a full-size capital, then the rest
#: of the word in capitals of this share of its size, on its baseline, with no gap ("O" + "BRÁZEK",
#: #5). A drop cap is far larger than the word after it, a raised superscript sits above it.
SMALL_CAPS_MIN_RATIO = 0.6
SMALL_CAPS_MAX_RATIO = 0.9
#: How far the bottoms of the two seam characters may differ, as a share of the larger size: a
#: smaller size of one font differs by its descent (0.4 pt at 10 pt); a `Ts` rise by far more.
SMALL_CAPS_RISE_RATIO = 0.15

#: A gap wider than this many font sizes splits a line into segments — a column gutter, a
#: tab stop. Word gaps, even in loosely justified text, stay well under one em.
SEGMENT_GAP_RATIO = 1.5

#: A gutter is an x-strip at least this wide that (almost) no segment crosses, with at least
#: MIN_COLUMN_SEGMENTS segments wholly on each side of it.
GUTTER_MIN_WIDTH = 8.0
MIN_COLUMN_SEGMENTS = 2

#: Share of the page height treated as the header / footer band.
MARGIN_BAND = 0.10

#: A running header or footer repeats on at least this share of the pages (and on 2+).
FURNITURE_MIN_SHARE = 0.5

#: A line at least this much larger than the body size is a heading.
HEADING_SIZE_RATIO = 1.15
HEADING_MAX_CHARS = 160
MAX_HEADING_LEVEL = 3

#: A ruled grid covering this share of the page is a frame around it, not a table; a grid
#: with fewer than this share of its cells holding text is decoration, not data.
TABLE_MAX_PAGE_SHARE = 0.85
TABLE_MIN_FILL = 0.34

#: Share of a page's text objects that must be invisible for it to be an OCR layer —
#: alto-postprocess's `PDF_OCR_LAYER_MIN_RATIO` default.
OCR_LAYER_MIN_RATIO = 0.5

_PAGE_NUMBER = re.compile(
    r"^[\s\-–—]*(?:(?:page|strana|str\.|s\.)\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})"
    r"(?:\s*(?:/|z|of|ze)\s*\d{1,4})?[\s\-–—]*$",
    re.IGNORECASE,
)


@dataclass
class _Unit:
    """One placeable thing on a page: a text segment or a whole table."""

    x0: float
    top: float
    x1: float
    bottom: float
    words: List[Dict[str, Any]] = field(default_factory=list)
    table: Optional[Tuple[DigitalTable, List[DigitalLine]]] = None
    column: int = 0
    spanning: bool = False
    #: `REGION_HEADER` / `REGION_FOOTER` once `_mark_furniture` has judged it furniture.
    region: Optional[str] = None

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)


# ── words, lines, segments ────────────────────────────────────────────────────


def _extract_words(page: Any) -> List[Dict[str, Any]]:
    """The page's words, a word set in two fonts joined, each word's characters dropped.

    pdfplumber groups a page's characters by `extra_attrs` before it looks at their spacing, so
    a font switch ends a word. PDF producers switch font INSIDE words: Acrobat Distiller sets the
    Czech letters outside WinAnsi (ě č ř ů ň ť ď) in a second subset of the face, and "Jičín"
    came out "Ji č ín" (#5: 12,029 such letters in one AMČR report, nearly 24,000 in fifteen).
    Grouping by size alone splits them as well, because pdfminer derives a character's size
    from its font's descent and two fonts at one point size differ by float noise
    (12.000000000000007 against 12.0). So the grouping stays as it was — font and size still
    decide bold, italic and headings — and `_merge_split_words` joins what a font switch split,
    capitals set as small caps ("O BRÁZEK"), and characters on a 90° baseline (`_vertical_runs`).

    Each word then keeps only what the engine reads (`_finish_word`): its majority font, an
    `inverted` flag, and its text with unmapped glyphs as U+FFFD. Dropping the per-character
    dicts, and closing each page after use (`extract_pdf`), keeps the memory of a long document
    from growing with its character count.
    """
    words = page.extract_words(
        x_tolerance_ratio=WORD_GAP_RATIO,
        extra_attrs=["fontname", "size"],
        return_chars=True,
    )
    return [_finish_word(word) for word in _merge_split_words(words)]


def _baseline(word: Dict[str, Any]) -> float:
    """The word's baseline in PDF user space (its first character's text matrix)."""
    chars = word.get("chars") or [{}]
    matrix = chars[0].get("matrix")
    if matrix and len(matrix) == 6:
        return round(float(matrix[5]), 1)
    return round(float(word.get("bottom") or 0.0), 1)


def _split_by_font(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Whether `right` continues `left`: one word that a font switch split.

    Same size (to float noise) at the seam, next to each other within the word-gap tolerance,
    and the characters at the seam differ in font or in raw size — the only reason pdfplumber
    split them. A superscript (another size, another baseline), a word after a space (a gap of
    a quarter em or more) and an overprinted duplicate in the same font all stay apart. The
    seam's characters decide, not the words' first ones: a word already joined across small
    caps ("S" + "OU") goes on across a font switch ("Ř" from a second subset).
    """
    last, first = (left.get("chars") or [{}])[-1], (right.get("chars") or [{}])[0]
    size = float(last.get("size") or left.get("size") or 0.0)
    other = float(first.get("size") or right.get("size") or 0.0)
    if not size or round(size, 2) != round(other, 2):
        return False
    gap = float(right["x0"]) - float(left["x1"])
    if not -MERGE_OVERLAP_RATIO * size <= gap <= FONT_SWITCH_GAP_RATIO * size:
        return False
    return last.get("fontname") != first.get("fontname") or last.get("size") != first.get("size")


def _small_caps(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Whether `right` continues `left` as small capitals: "O" + "BRÁZEK" is "OBRÁZEK".

    Word and Distiller set small caps as a full-size capital and smaller capitals, and
    pdfplumber, grouping by size, splits the word there (#5: "O BRÁZEK" 212 times in one AMČR
    report, "T ABULKA", "N EGATIV"). Joined when the seam goes from an uppercase letter to
    capitals `SMALL_CAPS_MIN_RATIO`–`SMALL_CAPS_MAX_RATIO` of its size, with no gap and no rise.
    A real space ("P VI", "V PRŮBĚHU"), a drop cap and a raised capital stay apart.
    """
    last, first = (left.get("chars") or [{}])[-1], (right.get("chars") or [{}])[0]
    big, small = float(last.get("size") or 0.0), float(first.get("size") or 0.0)
    if not big or not small or not SMALL_CAPS_MIN_RATIO <= small / big <= SMALL_CAPS_MAX_RATIO:
        return False
    gap = float(right["x0"]) - float(left["x1"])
    if not -MERGE_OVERLAP_RATIO * small <= gap <= FONT_SWITCH_GAP_RATIO * small:
        return False
    rise = abs(float(first.get("bottom") or 0.0) - float(last.get("bottom") or 0.0))
    if rise > SMALL_CAPS_RISE_RATIO * big:
        return False
    seam, text = str(last.get("text", "")), str(right.get("text", ""))
    return (
        seam.isalpha()
        and seam.isupper()
        and any(char.isalpha() for char in text)
        and all(char.isupper() for char in text if char.isalpha())
    )


def _join(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """`left` and `right` as one word: `left`'s attributes, both texts and boxes, all chars."""
    return {
        **left,
        "text": str(left["text"]) + str(right["text"]),
        "x0": min(float(left["x0"]), float(right["x0"])),
        "x1": max(float(left["x1"]), float(right["x1"])),
        "top": min(float(left["top"]), float(right["top"])),
        "bottom": max(float(left["bottom"]), float(right["bottom"])),
        "chars": list(left.get("chars") or []) + list(right.get("chars") or []),
    }


def _merge_split_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join the words a font switch (`_split_by_font`) or small caps (`_small_caps`) split, per
    baseline, left to right; then the characters on a 90° baseline (`_vertical_runs`).

    By baseline, not by pdfplumber's line clustering: a second font with another descent puts
    its letters' `top` a few points off the line (`ř` in "Přílohy"), and pdfplumber then
    clusters them into a line of their own.
    """
    rows: Dict[float, List[Dict[str, Any]]] = {}
    merged: List[Dict[str, Any]] = []
    rotated: List[Dict[str, Any]] = []
    for word in words:
        if word.get("upright", True):
            rows.setdefault(_baseline(word), []).append(word)
        else:
            rotated.append(word)
    for row in rows.values():
        row.sort(key=lambda w: float(w["x0"]))
        current = row[0]
        for word in row[1:]:
            if _split_by_font(current, word) or _small_caps(current, word):
                current = _join(current, word)
            else:
                merged.append(current)
                current = word
        merged.append(current)
    return merged + _vertical_runs(rotated)


def _run_direction(char: Dict[str, Any]) -> Optional[str]:
    """`ttb` or `btt` for a character on a 90° baseline (by its text matrix), else None."""
    matrix = char.get("matrix")
    if not matrix or len(matrix) != 6:
        return None
    a, b = float(matrix[0]), float(matrix[1])
    if abs(b) <= abs(a):
        return None
    return "ttb" if b < 0 else "btt"


def _vertical_runs(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Words of characters set on a 90° baseline, one per run, read along it.

    A column header set vertically in a table, a caption along a page edge: pdfminer gives a
    rotated character its advance as its `size`, so pdfplumber, grouping by size, made a word
    of nearly every character, and the engine a line of each ("K_číslo" read as K / _ / č / í s /
    l o, #5: 432 such lines on 77 pages of one AMČR report). The characters of such words are
    grouped by direction (`_run_direction`) and by their vertical baseline (the matrix `e`, the
    same across a second font subset), ordered along it, and split where the gap is a word or a
    segment gap. Each run becomes one word, marked `vertical`. Characters on another slant keep
    pdfplumber's words.
    """
    kept: List[Dict[str, Any]] = []
    groups: Dict[Tuple[str, float], List[Dict[str, Any]]] = {}
    for word in words:
        chars = word.get("chars") or []
        directions = {_run_direction(char) for char in chars}
        if not chars or None in directions or len(directions) != 1:
            kept.append(word)
            continue
        direction = directions.pop()
        for char in chars:
            groups.setdefault((direction, round(float(char["matrix"][4]), 1)), []).append(char)
    if not groups:
        return kept

    from pdfplumber.utils.text import LIGATURES  # noqa: PLC0415  (optional dependency)

    runs: List[Dict[str, Any]] = []
    for (direction, _), chars in groups.items():
        ttb = direction == "ttb"
        chars.sort(key=lambda c: float(c["top"]) if ttb else -float(c["bottom"]))
        parts: List[List[Dict[str, Any]]] = [[chars[0]]]
        for previous, char in zip(chars, chars[1:], strict=False):
            size = float(char["x1"]) - float(char["x0"]) or 10.0
            if ttb:
                gap = float(char["top"]) - float(previous["bottom"])
            else:
                gap = float(previous["top"]) - float(char["bottom"])
            if gap > SEGMENT_GAP_RATIO * size:
                runs.append(_vertical_word(parts, direction, LIGATURES))
                parts = [[char]]
            elif gap > WORD_GAP_RATIO * size:
                parts.append([char])
            else:
                parts[-1].append(char)
        runs.append(_vertical_word(parts, direction, LIGATURES))
    return kept + runs


def _vertical_word(
    parts: List[List[Dict[str, Any]]], direction: str, ligatures: Mapping[str, str]
) -> Dict[str, Any]:
    """One vertical run (its words, each a list of characters) as a pdfplumber-style word."""
    chars = [char for part in parts for char in part]
    text = " ".join(
        "".join(ligatures.get(str(c.get("text", "")), str(c.get("text", ""))) for c in part)
        for part in parts
    )
    fonts = Counter(str(char.get("fontname") or "") for char in chars)
    return {
        "text": text,
        "x0": min(float(c["x0"]) for c in chars),
        "x1": max(float(c["x1"]) for c in chars),
        "top": min(float(c["top"]) for c in chars),
        "bottom": max(float(c["bottom"]) for c in chars),
        # The font's size across the baseline (the characters' box width), not their advance.
        "size": max(float(c["x1"]) - float(c["x0"]) for c in chars),
        "fontname": fonts.most_common(1)[0][0],
        "upright": False,
        "direction": direction,
        "vertical": True,
        "chars": chars,
    }


def _finish_word(word: Dict[str, Any]) -> Dict[str, Any]:
    """Keep what the engine reads of a word, in place: majority font, `inverted`, the text.

    A word set in two fonts takes the font most of its characters carry ("Jičín" with its `č`
    in a second subset is in the first font, and bold only if most of it is). Its size stays
    pdfplumber's, the first character's. The characters themselves are dropped.
    """
    chars = word.pop("chars", None) or []
    fonts = Counter(str(char.get("fontname") or "") for char in chars)
    if fonts:
        word["fontname"] = fonts.most_common(1)[0][0]
    word["inverted"] = any(is_inverted(char.get("matrix")) for char in chars)
    word["text"] = unmapped_to_replacement(str(word.get("text", "")))
    return word


def _cluster_lines(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Words into visual lines: same baseline band, tolerance tied to the font size."""
    lines: List[List[Dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        tolerance = max(2.0, 0.5 * float(word.get("size") or 10.0))
        if lines and abs(float(lines[-1][0]["top"]) - float(word["top"])) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: float(w["x0"])) for line in lines]


def _segments(line: List[Dict[str, Any]]) -> List[_Unit]:
    """Split one visual line wherever a column-sized gap opens."""
    units: List[_Unit] = []
    for word in line:
        size = float(word.get("size") or 10.0)
        if units and float(word["x0"]) - units[-1].x1 <= SEGMENT_GAP_RATIO * size:
            unit = units[-1]
            unit.words.append(word)
            unit.x1 = max(unit.x1, float(word["x1"]))
            unit.top = min(unit.top, float(word["top"]))
            unit.bottom = max(unit.bottom, float(word["bottom"]))
        else:
            units.append(
                _Unit(
                    x0=float(word["x0"]),
                    top=float(word["top"]),
                    x1=float(word["x1"]),
                    bottom=float(word["bottom"]),
                    words=[word],
                )
            )
    return units


def _gutters(units: Sequence[_Unit]) -> List[Tuple[float, float]]:
    """Vertical strips almost no segment crosses, with text on both sides.

    Coverage-based rather than gap-based, so two columns whose baselines do not line up
    (the usual case once a heading shifts one column) are still found. A full-width title
    or a centred page number crosses the strip once; `allowed` tolerates that.
    """
    text_units = [u for u in units if u.table is None]
    if len(text_units) < 2 * MIN_COLUMN_SEGMENTS:
        return []
    lo = int(min(u.x0 for u in text_units))
    hi = int(max(u.x1 for u in text_units)) + 1
    coverage = [0] * (hi - lo + 1)
    for unit in text_units:
        for x in range(int(unit.x0) - lo, int(unit.x1) - lo + 1):
            coverage[x] += 1
    allowed = max(1, len(text_units) // 10)

    strips: List[Tuple[float, float]] = []
    start: Optional[int] = None
    for i, count in enumerate(coverage + [allowed + 1]):
        if count <= allowed and start is None:
            start = i
        elif count > allowed and start is not None:
            g0, g1 = lo + start, lo + i - 1
            if g1 - g0 >= GUTTER_MIN_WIDTH:
                # Sides are judged against the WHOLE strip: with a tolerance of `allowed`
                # crossings the strip can start inside the longest line of the left column,
                # so "ends before g0" would miss that column's own lines.
                left = sum(1 for u in text_units if u.x0 < g0 and u.x1 <= g1)
                right = sum(1 for u in text_units if u.x1 > g1 and u.x0 >= g0)
                if left >= MIN_COLUMN_SEGMENTS and right >= MIN_COLUMN_SEGMENTS:
                    strips.append((float(g0), float(g1)))
            start = None
    return strips


def _reading_order(units: List[_Unit], gutters: List[Tuple[float, float]]) -> List[_Unit]:
    """Band by band, then column by column inside a band, top to bottom inside a column.

    A unit that crosses a gutter is full-width: it closes the band above it and is read on
    its own — a title over two columns, a wide table, a centred footer.
    """
    for unit in units:
        crossing = [g for g in gutters if unit.x0 < g[0] + 1 and unit.x1 > g[1] - 1]
        unit.spanning = bool(crossing)
        centre = (unit.x0 + unit.x1) / 2
        unit.column = 0 if unit.spanning else sum(1 for g in gutters if centre > (g[0] + g[1]) / 2)

    ordered: List[_Unit] = []
    band: List[_Unit] = []
    for unit in sorted(units, key=lambda u: (u.top, u.x0)):
        if unit.spanning:
            ordered.extend(sorted(band, key=lambda u: (u.column, u.top, u.x0)))
            band = []
            ordered.append(unit)
        else:
            band.append(unit)
    ordered.extend(sorted(band, key=lambda u: (u.column, u.top, u.x0)))
    return ordered


def _line_from_words(words: List[Dict[str, Any]], page_label: str) -> DigitalLine:
    sizes = Counter(round(float(w.get("size") or 0.0), 1) for w in words)
    fontname = str(words[0].get("fontname", ""))
    bold, italic = font_flags(fontname)
    return DigitalLine(
        page=page_label,
        line=0,
        text=" ".join(w["text"] for w in words),
        bbox=[
            round(min(float(w["x0"]) for w in words), 3),
            round(min(float(w["top"]) for w in words), 3),
            round(max(float(w["x1"]) for w in words), 3),
            round(max(float(w["bottom"]) for w in words), 3),
        ],
        font=fontname,
        size=sizes.most_common(1)[0][0] if sizes else 0.0,
        bold=bold,
        italic=italic,
        inverted=any(w.get("inverted") for w in words),
        vertical=any(w.get("vertical") for w in words),
    )


# ── tables ────────────────────────────────────────────────────────────────────


def _inside(word: Dict[str, Any], box: Sequence[float]) -> bool:
    cx = (float(word["x0"]) + float(word["x1"])) / 2
    cy = (float(word["top"]) + float(word["bottom"])) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _covering(
    real: Dict[Tuple[int, int], Tuple[float, ...]],
    col_boxes: List[Any],
    rows: List[Any],
    r: int,
    c: int,
) -> Optional[Tuple[int, int]]:
    """The real cell whose box holds the centre of grid position (r, c) — the owner of a
    position pdfplumber reports as `None` because a merged cell covers it."""
    if c >= len(col_boxes):
        return None
    cx = (float(col_boxes[c][0]) + float(col_boxes[c][2])) / 2
    cy = (float(rows[r].bbox[1]) + float(rows[r].bbox[3])) / 2
    for key, box in real.items():
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
            return key
    return None


def _plausible_table(
    table: Any,
    real: Dict[Tuple[int, int], Tuple[float, ...]],
    words: List[Dict[str, Any]],
    page_area: float,
) -> bool:
    """Whether a ruled grid pdfplumber found is a table and not a layout frame.

    The line strategy finds any grid of rules, and designed documents draw them around
    whole pages and panels: `digital_born/sample.pdf` yields a 7x11 "table" covering 100% of
    its first page with 19 of 77 cells filled, which would put the page's prose into cells.
    A grid that is (nearly) the page, holds no text, or is mostly empty is left to the
    reading-order pass; its words stay in the flow.
    """
    x0, top, x1, bottom = (float(v) for v in table.bbox)
    if page_area and (x1 - x0) * (bottom - top) >= TABLE_MAX_PAGE_SHARE * page_area:
        return False
    filled = sum(1 for box in real.values() if any(_inside(w, box) for w in words))
    return bool(real) and filled >= TABLE_MIN_FILL * len(real)


def _extract_tables(
    page: Any, words: List[Dict[str, Any]], label: str, counter: List[int]
) -> Tuple[List[_Unit], List[Dict[str, Any]]]:
    """Ruled tables (pdfplumber's default line strategy) as units; returns the words left over.

    Cell text becomes `lines[]` rows grouped per cell (`tbl{n}-r{r}c{c}`); the grid shape
    goes to `tables[]` with each cell's bbox. A merged area is reported by pdfplumber as
    `None` at every covered position, which is resolved to the covering cell's group — the
    same "repeat the join key" convention the DOCX adapter uses for python-docx's repeats.
    """
    try:
        found = page.find_tables()
    except Exception:  # a malformed path must not take the whole page's text with it
        return [], words
    units: List[_Unit] = []
    remaining = list(words)
    page_area = float(page.width or 0.0) * float(page.height or 0.0)
    for table in found:
        rows = list(table.rows)
        n_cols = max((len(r.cells) for r in rows), default=0)
        if len(rows) < 2 or n_cols < 2:
            continue
        real: Dict[Tuple[int, int], Tuple[float, ...]] = {}
        for r, row in enumerate(rows):
            for c, box in enumerate(row.cells):
                if box is not None:
                    real[(r, c)] = tuple(float(v) for v in box)
        if not _plausible_table(table, real, remaining, page_area):
            continue
        number = counter[0]
        counter[0] += 1
        group = f"{TABLE_GROUP_PREFIX}{number}"
        grid = DigitalTable(
            table_id=f"t{number}",
            page=label,
            n_rows=len(rows),
            n_cols=n_cols,
            group_id=group,
        )
        try:
            col_boxes = [col.bbox for col in table.columns]
        except Exception:
            col_boxes = []

        covered: Dict[Tuple[int, int], set] = {}
        lines: List[DigitalLine] = []
        for r in range(len(rows)):
            for c in range(n_cols):
                owner = (r, c) if (r, c) in real else _covering(real, col_boxes, rows, r, c)
                if owner is None:
                    continue
                covered.setdefault(owner, set()).add((r, c))
                cell: Dict[str, Any] = {
                    "row": r,
                    "col": c,
                    "is_header": r == 0,
                    "group_id": f"{group}-r{owner[0]}c{owner[1]}",
                }
                if owner == (r, c):
                    box = real[owner]
                    cell["bbox"] = [round(v, 3) for v in box]
                    cell_words = [w for w in remaining if _inside(w, box)]
                    remaining = [w for w in remaining if not _inside(w, box)]
                    for line_words in _cluster_lines(cell_words):
                        line = _line_from_words(line_words, label)
                        line.group_id = cell["group_id"]
                        lines.append(line)
                grid.cells.append(cell)
        for cell in grid.cells:
            positions = covered.get((cell["row"], cell["col"]))
            if "bbox" in cell and positions and len(positions) > 1:
                rowspan = len({r for r, _ in positions})
                colspan = len({c for _, c in positions})
                if rowspan > 1:
                    cell["rowspan"] = rowspan
                if colspan > 1:
                    cell["colspan"] = colspan
        x0, top, x1, bottom = (float(v) for v in table.bbox)
        units.append(_Unit(x0=x0, top=top, x1=x1, bottom=bottom, table=(grid, lines)))
    return units, remaining


# ── the page census (pypdfium2) ───────────────────────────────────────────────


def _census(path: str, n_pages: int) -> Tuple[List[str], List[Tuple[int, int]]]:
    """Page labels and (text objects, invisible text objects) per page.

    pypdfium2 because pdfplumber (pdfminer) exposes neither the /PageLabels tree nor the
    text render mode. Ported from atrium-alto-postprocess `text_formats.read_pdf` (#31) so
    the two tools classify a page the same way. Degrades to physical numbers and "no
    invisible text" rather than failing: the census refines the record, the text does not
    depend on it.
    """
    labels = [str(i + 1) for i in range(n_pages)]
    counts = [(0, 0)] * n_pages
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415  (optional dependency, imported on use)
        import pypdfium2.raw as pdfium_c  # noqa: PLC0415
    except ImportError:
        return labels, counts
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception:
        return labels, counts
    try:
        counts = []
        for i in range(min(n_pages, len(pdf))):
            try:
                labels[i] = pdf.get_page_label(i) or labels[i]
            except Exception:
                pass
            n_text = n_invisible = 0
            page = None
            try:
                page = pdf[i]
                for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_TEXT,), max_depth=4):
                    n_text += 1
                    mode = pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
                    if mode == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE:
                        n_invisible += 1
            except Exception:  # one unreadable page degrades to "no census", not a failed PDF
                n_text = n_invisible = 0
            finally:
                if page is not None:
                    page.close()
            counts.append((n_text, n_invisible))
        counts += [(0, 0)] * (n_pages - len(counts))
    finally:
        pdf.close()
    return labels, counts


# ── document-level passes ────────────────────────────────────────────────────


def _furniture_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def _mark_furniture(pages: List[Tuple[DigitalPage, List[_Unit]]]) -> None:
    """Running headers/footers: margin-band lines that repeat across pages, and page numbers.

    Repetition is judged on the digit-normalised text, so "Strana 3" and "Strana 4" are the
    same furniture line; a lone page number in a margin band is furniture on its own.
    """
    n_pages = sum(1 for page, _ in pages if page.height)
    seen: Dict[Tuple[str, str], int] = Counter()
    for page, units in pages:
        if not page.height:
            continue
        keys = set()
        for unit in units:
            if unit.table is not None:
                continue
            band = _band(unit, page.height)
            if band:
                keys.add((band, _furniture_key(unit.text)))
        for key in keys:
            seen[key] += 1

    for page, units in pages:
        if not page.height:
            continue
        for unit in units:
            if unit.table is not None:
                continue
            band = _band(unit, page.height)
            if not band:
                continue
            repeats = seen[(band, _furniture_key(unit.text))]
            if (
                n_pages >= 2 and repeats >= 2 and repeats >= FURNITURE_MIN_SHARE * n_pages
            ) or _PAGE_NUMBER.match(unit.text):
                unit.region = band


def furniture_only_image_coverage() -> float:
    """The effective `FURNITURE_ONLY_IMAGE_COVERAGE` (the setting, else the module default)."""
    try:
        import tool_limits  # noqa: PLC0415  (repo root)
    except ImportError:  # pragma: no cover - the repo root is on sys.path when the CLI runs
        return FURNITURE_ONLY_IMAGE_COVERAGE
    return float(tool_limits.FURNITURE_ONLY_IMAGE_COVERAGE.get())


def _mark_furniture_only(path: str, pages: List[Tuple[DigitalPage, List[_Unit]]]) -> None:
    """A page whose only text is its running header or footer, over images: `none` (#5).

    A scanned page inserted into a born-digital report and then numbered or stamped by the
    PDF's producer (Acrobat's "Add Header & Footer", Bates numbers) has a text layer: the page
    number. It used to be `digital` and went to NLP with nothing to read, while its content,
    the scan, was never offered to OCR. When every text unit of a `digital` page is furniture
    (no body text, no table) and its images cover at least `FURNITURE_ONLY_IMAGE_COVERAGE` of
    the page, the page's content has no text layer: it is `none`, and Layer B flags it with a
    reason that says so (`digital_to_json._reason_furniture_only`). Its furniture lines stay.

    Coverage is measured only for such pages (`api_util/pdf_coverage.py`, the measurement
    `tools/samples_report.py` reports for every page). Without pypdfium2 nothing changes.
    """
    candidates = [
        page
        for page, units in pages
        if page.text_layer == TEXT_LAYER_DIGITAL
        and units
        and all(unit.table is None and unit.region for unit in units)
    ]
    if not candidates:
        return
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415  (optional dependency, imported on use)

        pdf = pdfium.PdfDocument(path)
    except Exception:
        return
    threshold = furniture_only_image_coverage()
    try:
        for page in candidates:
            if not 1 <= page.page_index <= len(pdf):
                continue
            handle = None
            try:
                handle = pdf[page.page_index - 1]
                _, coverage = image_coverage(handle)
            except Exception:  # one unreadable page keeps its verdict, not a failed PDF
                continue
            finally:
                if handle is not None:
                    handle.close()
            page.image_coverage = round(coverage, 4)
            if coverage >= threshold:
                page.text_layer = TEXT_LAYER_NONE
    finally:
        pdf.close()


def _band(unit: _Unit, height: float) -> Optional[str]:
    if unit.bottom <= MARGIN_BAND * height:
        return REGION_HEADER
    if unit.top >= (1 - MARGIN_BAND) * height:
        return REGION_FOOTER
    return None


def _heading_candidate(page: DigitalPage, line: DigitalLine) -> bool:
    """A line that counts for the body size and may be a heading (`_assign_headings`)."""
    return (
        page.text_layer == TEXT_LAYER_DIGITAL
        and line.region is None
        and not in_table(line.group_id)
        and not line.vertical
        and bool(line.size)
    )


def _assign_headings(document: DigitalDocument) -> None:
    """Heading levels by font size against the document's body size (char-weighted mode).

    Only the body lines of `digital` pages count, both for the body size and as headings. A
    table's small type would otherwise become the "body" and every paragraph a heading (#5: 1,927
    of 30,938 lines in a table-heavy AMČR report, Docling finding 192); a prior OCR layer's
    invisible text has sizes of its own (a lone "," became a heading), and a vertical run or the
    running header is never one. `garbled` is decided later (Layer B), so its pages still count.
    """
    candidates = [
        line for page in document.pages for line in page.lines if _heading_candidate(page, line)
    ]
    weights: Counter = Counter()
    for line in candidates:
        weights[line.size] += len(line.text)
    if not weights:
        return
    body = weights.most_common(1)[0][0]
    heading_sizes = sorted(
        {
            line.size
            for line in candidates
            if line.size >= HEADING_SIZE_RATIO * body and len(line.text) <= HEADING_MAX_CHARS
        },
        reverse=True,
    )
    levels = {size: min(i + 1, MAX_HEADING_LEVEL) for i, size in enumerate(heading_sizes)}
    for line in candidates:
        if line.size in levels and len(line.text) <= HEADING_MAX_CHARS:
            line.heading_level = levels[line.size]


# ── entry point ───────────────────────────────────────────────────────────────


def _open_error(exc: Exception) -> DigitalInputError:
    text = f"{type(exc).__name__}: {exc}"
    if "password" in text.lower() or "encrypt" in text.lower():
        return DigitalInputError("encrypted", f"password-protected PDF ({text})")
    return DigitalInputError("corrupt", f"PDF could not be read ({text})")


def extract_pdf(path: str, doc_id: str, origin: str) -> DigitalDocument:
    """Layer A for PDF. Returns pages in reading order, lines not yet normalised (Layer B)."""
    try:
        import pdfplumber  # noqa: PLC0415  (optional dependency, imported on use)
    except ImportError as exc:
        raise missing_dependency("pdfplumber", "pdfplumber") from exc

    document = DigitalDocument(
        doc_id=doc_id,
        origin=origin,
        media_type="application/pdf",
        sha256=sha256_file(path),
        filename=os.path.basename(path),
    )
    document.use("pdfplumber", "pdfminer.six")

    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:
        raise _open_error(exc) from exc

    staged: List[Tuple[DigitalPage, List[_Unit]]] = []
    table_counter = [0]
    with pdf:
        try:
            n_pages = len(pdf.pages)
        except Exception as exc:
            raise _open_error(exc) from exc
        labels, counts = _census(path, n_pages)
        if any(n for n, _ in counts):
            document.use("pypdfium2")
        for index, page in enumerate(pdf.pages):
            label = labels[index]
            current = DigitalPage(
                page=label,
                # 1-BASED: the schema types page_index as `minimum: 1` and documents it as
                # the "1-based physical position". A 0-based value fails validation.
                page_index=index + 1,
                width=float(page.width or 0.0),
                height=float(page.height or 0.0),
                unit="pt",
            )
            current.text_objects, current.invisible_text_objects = counts[index]
            try:
                words = _extract_words(page)
                current.images = len(page.images)
                current.vector_paths = len(page.curves) + len(page.lines) + len(page.rects)
            except Exception as exc:
                raise _open_error(exc) from exc
            words = [w for w in words if str(w.get("text", "")).strip()]

            if not words:
                # No text at all: `none`, whatever is drawn — alto-postprocess #31's rule
                # (`classify_text_layer`, fewer than 3 characters), and the #10 plan's G1. A
                # page that draws nothing a parser can see is flagged too: its image may sit
                # where no parser looks, and re-acquiring a truly blank page costs one OCR
                # call while missing a scanned one loses its text. The reason tells them apart.
                current.text_layer = TEXT_LAYER_NONE
                staged.append((current, []))
                page.close()
                continue
            n_text, n_invisible = counts[index]
            if n_text and n_invisible / n_text >= OCR_LAYER_MIN_RATIO:
                current.text_layer = TEXT_LAYER_OCR
            else:
                current.text_layer = TEXT_LAYER_DIGITAL

            table_units, words = _extract_tables(page, words, label, table_counter)
            flat = [w for w in words if not w.get("vertical")]
            units = table_units + [seg for line in _cluster_lines(flat) for seg in _segments(line)]
            # A vertical run is a line of its own: clustered by `top` it would join whatever
            # horizontal line its first character happens to sit beside.
            units += [
                _Unit(
                    x0=float(w["x0"]),
                    top=float(w["top"]),
                    x1=float(w["x1"]),
                    bottom=float(w["bottom"]),
                    words=[w],
                )
                for w in words
                if w.get("vertical")
            ]
            staged.append((current, units))
            # pdfplumber keeps a page's layout and character dicts until the page is closed, and
            # the words no longer point at them: a 500-page report no longer holds every page.
            page.close()

    _mark_furniture(staged)
    _mark_furniture_only(path, staged)

    for page, units in staged:
        headers = sorted(
            (u for u in units if u.region == REGION_HEADER), key=lambda u: (u.top, u.x0)
        )
        footers = sorted(
            (u for u in units if u.region == REGION_FOOTER), key=lambda u: (u.top, u.x0)
        )
        body = [u for u in units if u.region is None]
        ordered = _reading_order(body, _gutters(body))
        for region, group in ((REGION_HEADER, headers), (None, ordered), (REGION_FOOTER, footers)):
            for unit in group:
                if unit.table is not None:
                    grid, lines = unit.table
                    for line in lines:
                        line.column = unit.column
                    page.lines.extend(lines)
                    page.tables.append(grid)
                    continue
                line = _line_from_words(unit.words, page.page)
                line.region = region
                line.column = unit.column if region is None else 0
                page.lines.append(line)
        document.pages.append(page)

    _assign_headings(document)
    renumber_lines(document)
    return document
