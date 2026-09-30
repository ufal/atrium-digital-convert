"""
api_util/layout_md.py — visual-layout cue vocabulary for LLM-friendly Markdown.

Single source of truth for the "visual layout pieces" catalogued in issue #10
(https://github.com/ufal/atrium-llm-enrich/issues/10, comment #2): the taxonomy
of page borders, bounding boxes, fonts, alignment, headers/footers, etc. that a
PDF/DOCX front-end records so an LLM sees layout signal a plain text dump would
lose.

The strategy (from the issue) is a **hybrid encoding**: keep Markdown's minimal
syntax for structures models already parse (headings, ``**bold**``, tables,
``[^1]`` footnotes) and carry the machine-readable spatial/typographic metadata
in **HTML comments** — invisible to a Markdown renderer, plain text to the LLM,
and cheap on tokens compared with per-token coordinate injection.

Every emitter here is a pure, dependency-free function returning the *exact*
string form documented in the issue, so both converters
(``docx_to_md.py`` / ``pdf_to_md.py``) speak one canonical schema. ``CUE_SCHEMA``
lists the whole taxonomy (including cues no current tool emits yet) so the set of
"all possible visual layout pieces" stays discoverable and testable in one place.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Cue taxonomy — name -> one-line description. Mirrors issue #10 comment #2.
# "core" cues are emitted by the current converters; others are reserved so the
# full catalogue is documented in one place and can be filled in later.
# --------------------------------------------------------------------------- #
CUE_SCHEMA: dict[str, str] = {
    # Macro-structural boundaries
    "PAGE_BREAK": "Physical end of one page / start of the next.",
    "DOC_META": "Page canvas size and orientation.",
    "LAYOUT_MARGIN": "White space separating body text from the page edge.",
    "LAYOUT_COLUMN": "Position within a multi-column layout (e.g. 1_of_2).",
    # Spatial geometry
    "BBOX": "[x_min, y_min, x_max, y_max] of a block / image.",
    "INDENT": "Leading indentation applied to a block.",
    # Typographic / stylistic
    "FONT": "Point size, numeric weight and/or family of a run.",
    "STYLE": "Text colour, highlight and/or line spacing of a run.",
    # Document-specific regions
    "HEADER_START": "Opens a repeating page-header region.",
    "HEADER_END": "Closes a repeating page-header region.",
    "FOOTER_START": "Opens a repeating page-footer region.",
    "FOOTER_END": "Closes a repeating page-footer region.",
    "WATERMARK": "Faded overlay text across the page (e.g. CONFIDENTIAL).",
    # Pipeline signals (not from the issue taxonomy)
    "NEEDS_OCR": "Page has no trustworthy text layer — route to the OCR path.",
    "OCR": "OCR provenance: engine and language used to transcribe a page.",
}

# Inline Markdown forms (not HTML comments) also part of the taxonomy.
INLINE_CUES: dict[str, str] = {
    "bold": "**text** — high importance / titles.",
    "italic": "*text* — emphasis / titles.",
    "strike": "~~text~~ — deleted / superseded text.",
    "underline": "<u>text</u> — underlined text.",
    "align_div": '<div align="…"> — centred/right block alignment.',
    "image": "![alt](src) — embedded figure, optionally + BBOX.",
    "footnote": "text[^n] / [^n]: … — footnotes and endnotes.",
    "table": "GFM pipe table — tabular data.",
    "heading": "### text — a heading, two levels below the `## Page N` sections.",
}


# --------------------------------------------------------------------------- #
# Detail profiles (atrium-project#70 item 1; llm-enrich#13 §B)
#
# What the record route (json_to_md -> xml_to_md.rows_to_layout_markdown) emits
# at each ``detail`` level — the values of the schema's ``regenerable.<k>.detail``
# enum. The TEXT lines are identical in all three; only cues are dropped, and the
# cue sets nest (minimal ⊂ standard ⊂ full), so a lighter profile never says
# anything the full one does not.
#
#   full      every cue the record can support: BBOX per line, DOC_META, OCR,
#             emphasis, and LAYOUT_MARGIN on pages with a canvas and body boxes.
#   standard  one BBOX per block (a run of lines sharing ``group_id``; a table keeps
#             its own; lines with no group get none), no LAYOUT_MARGIN.
#   minimal   the page skeleton and the document structure only: no geometry, no
#             DOC_META, no OCR provenance, no emphasis. Dropping OCR deviates from
#             the 07-24 §B draft on purpose — on pilot-shaped ALTO records (no bbox,
#             no canvas) it is the only cue there is to drop.
#
# ``full`` is the default everywhere; which lighter profile is safe for the model is
# #22's bake-off to decide, not this table's.
# --------------------------------------------------------------------------- #
DETAIL_LEVELS: tuple[str, ...] = ("full", "standard", "minimal")
DEFAULT_DETAIL = "full"

_MINIMAL_CUES = frozenset(
    {
        "PAGE_BREAK",
        "NEEDS_OCR",
        "HEADER_START",
        "HEADER_END",
        "FOOTER_START",
        "FOOTER_END",
        "heading",
        "footnote",
        "table",
        "image",
    }
)
_STANDARD_CUES = _MINIMAL_CUES | {"OCR", "DOC_META", "bold", "italic", "BBOX"}

#: Cue names (``CUE_SCHEMA`` and ``INLINE_CUES`` keys) each profile emits.
PROFILE_CUES: dict[str, frozenset] = {
    "minimal": _MINIMAL_CUES,
    "standard": _STANDARD_CUES,
    "full": _STANDARD_CUES | {"LAYOUT_MARGIN"},
}

#: How much geometry each profile keeps: a box per line, a box per block, or none.
#: Figures (``image``) keep their own box wherever BBOX is kept at all.
BBOX_SCOPE: dict[str, Optional[str]] = {"full": "line", "standard": "block", "minimal": None}

#: Cues in the catalogue that the record route does not emit under ANY profile, each
#: with the reason. Every cue is in a profile or here (tests/test_layout_md.py), so a
#: cue cannot be silently forgotten. The deprecated direct converters (docx_to_md /
#: pdf_to_md, reachable with ``--legacy``) still emit some of these; they have no
#: profiles.
RESERVED: dict[str, str] = {
    "INDENT": "No consumer has asked for it, and where a line counts as indented on an "
    "OCR box is threshold guesswork.",
    "LAYOUT_COLUMN": "The digital IR's column index stays inside digital_to_json; putting it "
    "on the record is a post-freeze schema change (llm-enrich#10 W7).",
    "FONT": "The record keeps semantic style only (lines[].style, #18 option (c)): size, "
    "weight and family are dropped at conversion.",
    "STYLE": "Colour, highlight and line spacing are not in the record (#18 option (c)).",
    "WATERMARK": "No engine in the pipeline detects watermarks.",
    "strike": "lines[].style carries bold and italic only.",
    "underline": "lines[].style carries bold and italic only.",
    "align_div": "The record has no alignment field.",
}


def check_detail(detail: str) -> str:
    """Return ``detail`` if it names a profile; raise ``ValueError`` otherwise.

    Never falls back to ``full``: a caller asking for a profile that does not exist
    gets told so, rather than a rendering it did not ask for.
    """
    if detail not in DETAIL_LEVELS:
        raise ValueError(
            f"detail={detail!r} is not a detail profile (expected one of {list(DETAIL_LEVELS)})."
        )
    return detail


def emits(detail: str, cue: str) -> bool:
    """Whether profile ``detail`` emits ``cue`` (a ``CUE_SCHEMA``/``INLINE_CUES`` name)."""
    return cue in PROFILE_CUES[check_detail(detail)]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _comment(body: str) -> str:
    """Wrap a payload in an HTML comment: ``<!-- body -->``."""
    return f"<!-- {body} -->"


def _page_id(page_id) -> str:
    """Normalise a page identifier to the issue's ``pg_N`` form.

    Integers/bare digits become ``pg_12``; anything already prefixed
    (``pg_12``) or otherwise custom is passed through unchanged.
    """
    s = str(page_id).strip()
    if not s:
        return "pg_?"
    if s.startswith("pg_"):
        return s
    return f"pg_{s}"


def _fmt_num(n) -> str:
    """Render a coordinate/size compactly: whole floats lose the ``.0``."""
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    if isinstance(n, float):
        return f"{n:g}"
    return str(n)


def _kv(pairs: Iterable[tuple[str, object]]) -> str:
    """Join ``key=value`` pairs, dropping any whose value is None/blank."""
    out = []
    for key, val in pairs:
        if val is None or val == "":
            continue
        out.append(f"{key}={val}")
    return ", ".join(out)


# --------------------------------------------------------------------------- #
# Macro-structural boundaries & page borders
# --------------------------------------------------------------------------- #
def page_break(page_id) -> str:
    """``<!-- PAGE_BREAK: pg_12 -->`` — a strict page boundary for citation."""
    return _comment(f"PAGE_BREAK: {_page_id(page_id)}")


def doc_meta(size: Optional[str] = None, orientation: Optional[str] = None, **extra) -> str:
    """``<!-- DOC_META: size=8.5x11in, orientation=portrait -->``."""
    pairs = [("size", size), ("orientation", orientation), *extra.items()]
    return _comment(f"DOC_META: {_kv(pairs)}")


def layout_margin(top=None, bottom=None, left=None, right=None) -> str:
    """``<!-- LAYOUT_MARGIN: top=1in, bottom=1in, left=1in, right=1in -->``."""
    return _comment(
        "LAYOUT_MARGIN: "
        + _kv([("top", top), ("bottom", bottom), ("left", left), ("right", right)])
    )


def layout_column(index: int, total: int) -> str:
    """``<!-- LAYOUT_COLUMN: 1_of_2 -->`` — position in a multi-column layout."""
    return _comment(f"LAYOUT_COLUMN: {index}_of_{total}")


def needs_ocr(page_id, reason: str = "no extractable text layer") -> str:
    """``<!-- NEEDS_OCR: pg_3 (no extractable text layer) -->``.

    Not part of the issue taxonomy — an internal signal marking a page whose
    text layer is absent or untrustworthy, so a later OCR pass (deferred, see
    issue #10 / hub #22 benchmark) knows exactly which pages to re-transcribe.
    """
    return _comment(f"NEEDS_OCR: {_page_id(page_id)} ({reason})")


def ocr_meta(engine: str, lang: Optional[str] = None) -> str:
    """``<!-- OCR: engine=tesseract, lang=ces -->`` — page-transcription provenance.

    Not part of the issue taxonomy — records that a page's text was recovered by
    OCR (and with which engine/language), so downstream consumers can weight it
    differently from a native text layer.
    """
    return _comment(f"OCR: {_kv([('engine', engine), ('lang', lang)])}")


# --------------------------------------------------------------------------- #
# Spatial geometry
# --------------------------------------------------------------------------- #
def bbox(coords: Sequence[float]) -> str:
    """``<!-- BBOX: [120, 400, 600, 550] -->`` — [x_min, y_min, x_max, y_max]."""
    nums = ", ".join(_fmt_num(round(c) if isinstance(c, float) else c) for c in coords)
    return _comment(f"BBOX: [{nums}]")


def indent(px) -> str:
    """``<!-- INDENT: 4px -->`` — leading indentation of a block."""
    return _comment(f"INDENT: {_fmt_num(px)}px")


# --------------------------------------------------------------------------- #
# Typographic / stylistic
# --------------------------------------------------------------------------- #
def font(size=None, weight=None, family=None) -> str:
    """``<!-- FONT: size=14pt, weight=700, family="Courier New" -->``.

    Only the supplied attributes appear; ``family`` is quoted. Returns ``""``
    when nothing is supplied (nothing worth recording).
    """
    size_v = f"{_fmt_num(size)}pt" if size is not None else None
    family_v = f'"{family}"' if family else None
    body = _kv([("size", size_v), ("weight", weight), ("family", family_v)])
    return _comment(f"FONT: {body}") if body else ""


def style(color=None, highlight=None, line_spacing=None) -> str:
    """``<!-- STYLE: color=#FF0000, highlight=yellow -->`` (or line-spacing).

    Returns ``""`` when nothing is supplied.
    """
    body = _kv([("color", color), ("highlight", highlight), ("line-spacing", line_spacing)])
    return _comment(f"STYLE: {body}") if body else ""


# --------------------------------------------------------------------------- #
# Document-specific regions
# --------------------------------------------------------------------------- #
def header_start() -> str:
    return _comment("HEADER_START")


def header_end() -> str:
    return _comment("HEADER_END")


def footer_start() -> str:
    return _comment("FOOTER_START")


def footer_end() -> str:
    return _comment("FOOTER_END")


def watermark(text: str) -> str:
    """``<!-- WATERMARK: "CONFIDENTIAL DRAFT" -->``."""
    return _comment(f'WATERMARK: "{text}"')


# --------------------------------------------------------------------------- #
# Inline Markdown forms
# --------------------------------------------------------------------------- #
def bold(text: str) -> str:
    return f"**{text}**" if text and text.strip() else text


def italic(text: str) -> str:
    return f"*{text}*" if text and text.strip() else text


def strike(text: str) -> str:
    return f"~~{text}~~" if text and text.strip() else text


def underline(text: str) -> str:
    return f"<u>{text}</u>" if text and text.strip() else text


def align_div(content: str, align: str) -> str:
    """Wrap a block so its physical alignment survives: ``<div align="center">``.

    ``left`` alignment is the default flow and is returned unwrapped.
    """
    if not align or align == "left":
        return content
    return f'<div align="{align}">\n{content}\n</div>'


def image(alt: str, src: str, box: Optional[Sequence[float]] = None) -> str:
    """``![alt](src) <!-- BBOX: [x,y,w,h] -->`` — a figure placeholder for VLMs."""
    md = f"![{alt or ''}]({src})"
    return f"{md} {bbox(box)}" if box else md


def footnote_ref(n) -> str:
    """Inline footnote marker: ``[^1]``."""
    return f"[^{n}]"


def footnote_def(n, text: str) -> str:
    """Footnote definition line: ``[^1]: Note content.``."""
    return f"[^{n}]: {text}"


# --------------------------------------------------------------------------- #
# Tabular data (shared GFM renderer)
# --------------------------------------------------------------------------- #
def _escape_cell(value) -> str:
    """Make a value safe inside a GFM pipe-table cell."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>").strip()


def md_table(rows: Sequence[Sequence[object]], header: bool = True) -> str:
    """Render rows of cells as a GitHub-Flavored Markdown pipe table.

    The first row is treated as the header when ``header`` is true; otherwise a
    blank header is synthesised so the table stays valid GFM. Ragged rows are
    padded to the widest row.
    """
    rows = [list(r) for r in rows if r is not None]
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def _line(cells: Sequence[object]) -> str:
        padded = list(cells) + [""] * (width - len(cells))
        return "| " + " | ".join(_escape_cell(c) for c in padded) + " |"

    if header:
        head, body = rows[0], rows[1:]
    else:
        head, body = [""] * width, rows

    lines = [_line(head), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(_line(r) for r in body)
    return "\n".join(lines)
