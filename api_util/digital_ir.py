"""
api_util/digital_ir.py — the internal representation shared by the digital-born adapters.

`digital_to_json.py` (Layers B–D and the CLI), `digital_pdf.py` (light PDF engine),
`digital_docx.py` (DOCX) and `digital_docling.py` (heavy PDF engine) all build or read these
dataclasses. They live in their own module rather than in `digital_to_json.py` because that
file is also run as a script: an adapter importing `api_util.digital_to_json` while it runs
as `__main__` would load a SECOND copy of it — two sets of dataclasses, and the module-level
registry check printing twice. `digital_to_json` re-exports every name here, so
`d2j.DigitalLine` and friends keep working for existing callers and tests.

No optional dependency is imported here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: `lines[].style.region` values this converter writes (Issue #18, 2026-09-25).
#:
#: `lines[].style` is an object only `digital-convert` writes, so a `region` key inside it
#: needed no new ownership grant, and was additive under the hub's versioning rule 1. The
#: canonical schema declares it since 2026-09-25 as a CLOSED enum of exactly these three
#: values (hub `docs/document_schema.md`, changelog 2026-09-25): a new value here must be
#: added there first, or the output gate refuses the record. Absent means body text. Its one
#: consumer is `api_util/json_to_md.py`.
REGION_HEADER = "page_header"
REGION_FOOTER = "page_footer"
REGION_FOOTNOTE = "footnote"
REGIONS: Tuple[str, ...] = (REGION_HEADER, REGION_FOOTER, REGION_FOOTNOTE)

#: `DigitalPage.text_layer` — the per-page census the PDF adapters fill in, mirroring
#: atrium-alto-postprocess `text_formats.classify_text_layer` (#31) so the two tools read a
#: page the same way:
#:
#:   digital — an embedded text layer that is (so far) believed;
#:   none    — a PDF page with no extractable text: a scan, curves-for-letters, or a page
#:             that draws nothing a parser can see — OCR work either way (`images` and
#:             `vector_paths` say which, in the reason);
#:   ocr     — the text layer is INVISIBLE text: a prior OCR run, whose originator is
#:             alto-postprocess (`ocr:pdf-text-layer`), not this converter;
#:   blank   — an empty DOCX page (two breaks in a row); there is nothing to re-acquire.
TEXT_LAYER_DIGITAL = "digital"
TEXT_LAYER_NONE = "none"
TEXT_LAYER_OCR = "ocr"
TEXT_LAYER_BLANK = "blank"
#: Not an adapter's census value: a `digital` layer that Layer B's decode check
#: (`digital_to_json.assess_page`) found does not decode, so the page is flagged `needs_ocr`.
TEXT_LAYER_GARBLED = "garbled"
#: The verdicts of `text_layer_of()`: docproc's page `textLayer` (the record's `pages[].text_layer`
#: until record schema 2.0) and `/describe`'s `text_layer`. `garbled`, `ocr` and `none` are exactly
#: the pages Layer B flags `needs_ocr` (docproc's `needsAtr`). A page verdict, apart from the line
#: decode verdicts (`decode:garbled`, `decode:inverted`).
TEXT_LAYERS: Tuple[str, ...] = (
    TEXT_LAYER_DIGITAL,
    TEXT_LAYER_GARBLED,
    TEXT_LAYER_OCR,
    TEXT_LAYER_NONE,
    TEXT_LAYER_BLANK,
)


class DigitalInputError(ValueError):
    """An input this converter must not turn into a record, with a stable reason code.

    A `ValueError`, so callers that already catch the "unsupported input" error keep working.
    The codes match atrium-alto-postprocess's `ingest_report.csv` reasons where the two
    overlap, so an operator sees one vocabulary across both tools.
    """

    #: reason -> CLI exit code. 2 = a dependency is missing; 3 = not something this converter
    #: takes (for this seed); 4 = broken file.
    #:
    #: `source_digest_mismatch` (atrium-digital-convert#2, accepted by AMČR 2026-09-30): the seed's
    #: `source.sha512` is not the digest of the bytes handed over, so the record would describe
    #: one file under another file's identity. `invalid_record`: the `--document-json` record
    #: cannot be opened (not UTF-8 JSON, not an object, a newer schema major), or its `pages` or
    #: `lines` is not a list of objects (atrium_document.InvalidRecordError); the service's 422.
    #: `dependency_missing`: a reader this input needs (LibreOffice for DOC/XLS) is not
    #: installed in this deployment.
    EXIT_CODES: Dict[str, int] = {
        "unsupported": 3,
        "legacy_office_unsupported": 3,
        "source_digest_mismatch": 3,
        "invalid_record": 3,
        "dependency_missing": 2,
        "encrypted": 4,
        "corrupt": 4,
        "zip_limits_exceeded": 4,
        "conversion_failed": 4,
    }

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason

    @property
    def exit_code(self) -> int:
        return self.EXIT_CODES.get(self.reason, 3)


#: What an unmapped glyph becomes in a line (#5): one replacement character per glyph.
REPLACEMENT_CHAR = "\ufffd"

#: Parsers' placeholders for a glyph without a Unicode mapping: pdfminer (under pdfplumber, the
#: light engine) writes `(cid:236)`; docling-parse writes `GLYPH<…>` (not seen here yet: the
#: Docling run of #5 failed, so its form is taken from Docling's issue tracker).
_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)|GLYPH<[^<>]{0,80}>")


def unmapped_to_replacement(text: str) -> str:
    """A parser's placeholder for a glyph without a Unicode mapping, as U+FFFD (#5).

    A font without /ToUnicode (and without an encoding the parser can map) gives no character for
    a glyph, and pdfminer writes the seven characters `(cid:236)` in its place. They are ASCII,
    so the decode check never counted them, and whole AMČR pages of them (89–100 % unmapped)
    passed as `digital` text. One glyph becomes one replacement character, which is what it is:
    a character the layer does not give. `digital_to_json.decode_sanity` counts it, and a line
    whose glyphs are more than GARBLE_CHAR_SHARE unmapped is `garbled`, and so is its page.
    """
    if "(cid:" not in text and "GLYPH<" not in text:
        return text
    return _UNMAPPED_GLYPH.sub(REPLACEMENT_CHAR, text)


def missing_dependency(
    dependency: str, module: str, requirements: str = "requirements_digital.txt"
) -> RuntimeError:
    """The one error a missing optional import turns into: advice, not an ImportError stack."""
    return RuntimeError(
        f"{dependency} is required to convert this input but is not installed. "
        f"Install the converter stack: pip install -r {requirements} "
        f"(missing import: {module})"
    )


def _digest_file(path: str, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str) -> str:
    return _digest_file(path, "sha256")


def sha512_file(path: str) -> str:
    """The digest AMČR's archive keeps for an original (the Fedora digest, lower-case hex)."""
    return _digest_file(path, "sha512")


def font_flags(fontname: str) -> Tuple[bool, bool]:
    lowered = (fontname or "").lower()
    return ("bold" in lowered or "black" in lowered or "heavy" in lowered), (
        "italic" in lowered or "oblique" in lowered
    )


def is_inverted(matrix: Any) -> bool:
    """True when a glyph's transformation matrix mirrors or flips it.

    pdfplumber exposes the text matrix as `(a, b, c, d, e, f)`. A negative `a` mirrors
    horizontally and a negative `d` flips vertically; either makes the extracted string
    unreliable as *text* however cleanly it decodes, which is what the `inverted` verdict records.
    """
    if not matrix or len(matrix) < 4:
        return False
    try:
        return float(matrix[0]) < 0 or float(matrix[3]) < 0
    except (TypeError, ValueError):
        return False


@dataclass
class DigitalLine:
    """One text line, before it becomes a `lines[]` row."""

    page: str
    line: int
    text: str
    bbox: Optional[List[float]] = None
    font: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    heading_level: Optional[int] = None
    inverted: bool = False
    group_id: Optional[str] = None
    #: The decode verdict (`garbled`, `inverted`; the `decode-verdict` scheme), or None when the
    #: line decodes. Written as the docproc line flag `decode:<verdict>`, never a line category:
    #: line quality is the record builder's (D18 rules 2 and 3).
    decode: Optional[str] = None
    #: 0..1, how well the line's characters decode (`digital_to_json.decode_sanity`); not a
    #: measure of how the text reads.
    decode_score: Optional[float] = None
    lang: Optional[str] = None
    #: `style.region` — page furniture or a footnote; None for body text.
    region: Optional[str] = None
    #: Reading-order column on its page (PDF only). Internal: a column change is a
    #: paragraph boundary even when the vertical gap says otherwise.
    column: int = 0
    #: Set by the light engine for a line read along a 90° rotated baseline (a table header
    #: set vertically, a caption along the page edge). Internal: such a line is never a heading.
    vertical: bool = False
    #: Share of the line's characters set in a bold face (PDF light engine). Internal: `bold` is
    #: the first word's face; the bold-title heading pass reads this (`digital_pdf`).
    bold_share: float = 0.0
    #: The line's words with their boxes, `(text, [x0, top, x1, bottom])`, as the PDF light engine
    #: read them. Internal, never serialised: `render_place` aligns a rendered office file's words
    #: with the native text (atrium-digital-convert#9).
    words: List[Tuple[str, List[float]]] = field(default_factory=list)
    #: The drawings anchored in the native text just before this line that do not render as a
    #: picture: a chart, a SmartArt diagram, a shape or a group of shapes, an OLE object (DOCX,
    #: `digital_docx`). Internal: each is one of the page's pictures (`page_pictures`).
    graphics: int = 0
    #: The size of each of those drawings, `(width, height)` in pt, as the DOCX gives it
    #: (`wp:extent`, a VML shape's style, an OLE object's original size); `(0, 0)` when it gives
    #: none. Internal: `page_pictures` finds the drawing on the rendering by it.
    graphic_sizes: List[Tuple[float, float]] = field(default_factory=list)


#: `group_id` prefix of the lines that are table cells (`tbl{n}-r{r}c{c}`), in every adapter.
TABLE_GROUP_PREFIX = "tbl"


def in_table(group_id: Optional[str]) -> bool:
    """Whether a line with this `group_id` is a table cell."""
    return bool(group_id) and str(group_id).startswith(TABLE_GROUP_PREFIX)


@dataclass
class DigitalTable:
    """One table grid. `cells[].group_id` is the join key back into `lines[]`."""

    table_id: str
    page: str
    caption: str = ""
    n_rows: int = 0
    n_cols: int = 0
    group_id: Optional[str] = None
    cells: List[Dict[str, Any]] = field(default_factory=list)


Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PageFrame:
    """A PDF page's frame (D28, atrium-project#84): its MediaBox, its CropBox (clipped to the
    MediaBox) and its /Rotate, in PDF user space, to put the reader's boxes on the displayed page.

    The light engine's boxes are pdfplumber's (`x0`, `top`, `x1`, `bottom`). pdfplumber applies
    /Rotate (through pdfminer's page matrix) but measures from the MediaBox: it keeps the MediaBox's
    x offset, flips y by the MediaBox height, and ignores the CropBox. Its own `page.cropbox` is wrong
    on a page rotated 180 or 270 with an offset box. So `to_display` undoes pdfplumber's frame back to
    user space, then measures from the CropBox's top-left corner after /Rotate, in user-space units
    (1/72 inch x UserUnit) — the page a viewer, pdfium and the ATR render.
    """

    mediabox: Box
    cropbox: Box
    rotation: int = 0

    @property
    def size(self) -> Tuple[float, float]:
        """`(width, height)` of the displayed page."""
        width = self.cropbox[2] - self.cropbox[0]
        height = self.cropbox[3] - self.cropbox[1]
        return (height, width) if self.rotation in (90, 270) else (width, height)

    @property
    def is_identity(self) -> bool:
        """True when pdfplumber's frame already is the displayed page's."""
        return (
            self.rotation == 0 and self.cropbox == self.mediabox and self.mediabox[:2] == (0.0, 0.0)
        )

    def point(self, x: float, top: float) -> Tuple[float, float]:
        mx0, my0, mx1, my1 = self.mediabox
        raw = (mx0, my0, mx1, my1) if self.rotation in (0, 180) else (my0, mx0, my1, mx1)
        height = raw[3] - raw[1]
        # pdfplumber: x = dx + raw x0, top = 2 * height - raw y1 - dy, (dx, dy) pdfminer's device point.
        dx, dy = x - raw[0], 2 * height - raw[3] - top
        if self.rotation == 90:
            ux, uy = mx1 - dy, dx + my0
        elif self.rotation == 180:
            ux, uy = mx1 - dx, my1 - dy
        elif self.rotation == 270:
            ux, uy = dy + mx0, my1 - dx
        else:
            ux, uy = dx + mx0, dy + my0
        cx0, cy0, cx1, cy1 = self.cropbox
        u, v = ux - cx0, cy1 - uy  # top-left of the unrotated CropBox
        if self.rotation == 90:
            return (cy1 - cy0) - v, u
        if self.rotation == 180:
            return (cx1 - cx0) - u, (cy1 - cy0) - v
        if self.rotation == 270:
            return v, (cx1 - cx0) - u
        return u, v

    def to_display(self, bbox) -> List[float]:
        """A box `[x0, top, x1, bottom]` in pdfplumber's frame, on the displayed page."""
        ax, ay = self.point(float(bbox[0]), float(bbox[1]))
        bx, by = self.point(float(bbox[2]), float(bbox[3]))
        return [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]


@dataclass
class DigitalPage:
    page: str
    page_index: int
    width: float = 0.0
    height: float = 0.0
    unit: str = "pt"
    lines: List[DigitalLine] = field(default_factory=list)
    tables: List[DigitalTable] = field(default_factory=list)
    needs_ocr: bool = False
    needs_ocr_reason: str = ""
    #: The mean `decode_score` of the page's lines; None for a page without lines.
    decode_score: Optional[float] = None
    #: Per-page census (PDF). DOCX pages are always `digital`.
    text_layer: str = TEXT_LAYER_DIGITAL
    images: int = 0
    vector_paths: int = 0
    text_objects: int = 0
    invisible_text_objects: int = 0
    #: Share of the page under its images, measured only for a page whose only text is its
    #: running header or footer (`digital_pdf._mark_furniture_only`); None when not measured.
    image_coverage: Optional[float] = None
    #: A rendered office page only (`render_place`, atrium-digital-convert#9): its body words and
    #: how many of them the document's own text was placed on. None when not measured.
    render_words: Optional[int] = None
    render_matched: Optional[int] = None
    #: A PDF page's frame (D28): the docproc writer puts the boxes on the displayed page with it. The
    #: boxes and `width`/`height` above stay in the reader's frame, which every heuristic here uses.
    frame: Optional[PageFrame] = None
    #: The PDF page's /UserUnit when it is not 1 (D28): coordinates are in 1/72 inch x user_unit.
    user_unit: Optional[float] = None
    #: The boxes of the raster images drawn on the page, `[x0, top, x1, bottom]` in the reader's
    #: frame (the PDF light engine; a rendered office page's are its rendering's). Internal:
    #: `page_pictures` reads them.
    image_boxes: List[List[float]] = field(default_factory=list)
    #: The boxes of the vector paths (curves, lines, rectangles) of a rendered office page, in the
    #: reader's frame (kept only for an office document's rendering, `extract_pdf(...,
    #: keep_vectors=True)`). Internal: a chart, a SmartArt diagram or a shape is drawn with them, and
    #: `page_pictures` boxes it from them.
    vector_boxes: List[List[float]] = field(default_factory=list)

    @property
    def has_geometry(self) -> bool:
        return any(line.bbox for line in self.lines)


def count_damaged_words(texts) -> Tuple[int, int]:
    """`(damaged, words)` over lines of text: whitespace tokens, and those of them that hold
    U+FFFD and a letter — a word that lost a letter to a glyph without a Unicode mapping
    ("poh\ufffdební"). A token of U+FFFD alone is an unmapped symbol (a bullet), not a damaged word.
    """
    damaged = words = 0
    for text in texts:
        for token in str(text or "").split():
            words += 1
            if REPLACEMENT_CHAR in token and any(char.isalpha() for char in token):
                damaged += 1
    return damaged, words


def damaged_body_words(page: DigitalPage) -> Tuple[int, int]:
    """`count_damaged_words` over the page's body text, outside its running header and footer: a
    figure page whose only text is a header that lost a letter has nothing to re-acquire (36 such
    pages in one AMČR report, #5). The record's flag and `/describe`'s route use this one count."""
    return count_damaged_words(
        line.text for line in page.lines if line.region not in (REGION_HEADER, REGION_FOOTER)
    )


#: An image narrower or lower than this (pt) is not counted as a graphic: a bullet, an icon, a
#: rule drawn as an image.
GRAPHIC_MIN_SIDE = 24.0

#: An image that recurs at the same place on several pages is furniture (a logo, a letterhead
#: band) only while it covers less than this share of its page: a scan or a full-page picture on
#: every page is each page's own content.
FURNITURE_GRAPHIC_MAX_SHARE = 0.5


#: What a picture is: a raster image drawn on the page (a photo, a scan, a plan saved as a picture),
#: or a drawing from an office document's own XML that renders as vector paths (a chart, a SmartArt
#: diagram, shapes, an OLE object).
PICTURE_IMAGE = "image"
PICTURE_DRAWING = "drawing"

#: A drawing's box is found on the rendering when a cluster of vector paths (or a raster image)
#: matches its size within this many pt, or this share of the size, whichever is larger.
DRAWING_SIZE_TOLERANCE_PT = 8.0
DRAWING_SIZE_TOLERANCE_SHARE = 0.15

#: Vector paths closer than this (pt) belong to one drawing.
VECTOR_CLUSTER_GAP = 4.0

#: A vector path thinner than this (pt) is a rule or a border when it runs across most of the page
#: (`RULE_MIN_SHARE`) or lies on a table: not part of a drawing.
RULE_MAX_THICKNESS = 1.5
RULE_MIN_SHARE = 0.5


@dataclass
class PagePicture:
    """One picture a page shows (`page_pictures`): what it is, and its box in the reader's frame of
    the page it is on, when known (a drawing not found on the rendering has none)."""

    kind: str
    bbox: Optional[List[float]] = None


def _big(box: Sequence[float]) -> bool:
    return (
        float(box[2]) - float(box[0]) >= GRAPHIC_MIN_SIDE
        and float(box[3]) - float(box[1]) >= GRAPHIC_MIN_SIDE
    )


def _raster_pictures(document: DigitalDocument) -> List[List[List[float]]]:
    """Each page's raster images that are its own: at least `GRAPHIC_MIN_SIDE`, and not furniture
    (an image at the same place on several pages covering less than `FURNITURE_GRAPHIC_MAX_SHARE`
    of its page: a logo in the running header or footer)."""

    def place(box: Sequence[float]) -> Tuple[int, ...]:
        return tuple(int(round(float(v) / 4.0)) for v in box)

    def small(box: Sequence[float], page: DigitalPage) -> bool:
        area = float(page.width) * float(page.height)
        covered = (float(box[2]) - float(box[0])) * (float(box[3]) - float(box[1]))
        return area <= 0 or covered < FURNITURE_GRAPHIC_MAX_SHARE * area

    shown = [[list(box) for box in page.image_boxes if _big(box)] for page in document.pages]
    pages_at: Dict[Tuple[int, ...], int] = {}
    for page, boxes in zip(document.pages, shown, strict=True):
        for key in {place(box) for box in boxes if small(box, page)}:
            pages_at[key] = pages_at.get(key, 0) + 1
    furniture = {key for key, n in pages_at.items() if n > 1}
    return [[box for box in boxes if place(box) not in furniture] for boxes in shown]


def _union_box(boxes: Sequence[Sequence[float]]) -> List[float]:
    return [
        round(min(float(b[0]) for b in boxes), 2),
        round(min(float(b[1]) for b in boxes), 2),
        round(max(float(b[2]) for b in boxes), 2),
        round(max(float(b[3]) for b in boxes), 2),
    ]


def _table_areas(page: DigitalPage) -> List[List[float]]:
    """The areas of the page's tables, from their cells' line boxes, widened to reach the borders."""
    cells: Dict[str, List[List[float]]] = {}
    for line in page.lines:
        if in_table(line.group_id) and line.bbox:
            cells.setdefault(str(line.group_id).split("-", 1)[0], []).append(line.bbox)
    areas = []
    for boxes in cells.values():
        x0, top, x1, bottom = _union_box(boxes)
        areas.append([x0 - 18.0, top - 6.0, x1 + 18.0, bottom + 6.0])
    return areas


def _vector_clusters(page: DigitalPage) -> List[List[float]]:
    """The page's vector paths grouped into drawings: paths within `VECTOR_CLUSTER_GAP` of each other
    are one drawing, its box their union. Rules and borders are left out: a thin path across most of
    the page (a header's rule, a page border), a path on a table (its cell borders), a path covering
    most of the page (a background)."""
    width, height = float(page.width or 0.0), float(page.height or 0.0)
    tables = _table_areas(page)
    kept: List[List[float]] = []
    for box in page.vector_boxes:
        x0, top, x1, bottom = (float(v) for v in box)
        w, h = x1 - x0, bottom - top
        if width and height and w * h >= 0.9 * width * height:
            continue
        if min(w, h) < RULE_MAX_THICKNESS:
            if (width and w >= RULE_MIN_SHARE * width) or (height and h >= RULE_MIN_SHARE * height):
                continue
            cx, cy = (x0 + x1) / 2, (top + bottom) / 2
            if any(a[0] <= cx <= a[2] and a[1] <= cy <= a[3] for a in tables):
                continue
        kept.append([x0, top, x1, bottom])
    if not kept:
        return []
    # Union-find over a grid, so a page of many paths is not compared pairwise.
    parent = list(range(len(kept)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = 32.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    gap = VECTOR_CLUSTER_GAP
    for i, (x0, top, x1, bottom) in enumerate(kept):
        for gx in range(int((x0 - gap) // cell), int((x1 + gap) // cell) + 1):
            for gy in range(int((top - gap) // cell), int((bottom + gap) // cell) + 1):
                for j in grid.setdefault((gx, gy), []):
                    a, b = kept[i], kept[j]
                    if (
                        a[0] - gap <= b[2]
                        and b[0] - gap <= a[2]
                        and a[1] - gap <= b[3]
                        and b[1] - gap <= a[3]
                    ):
                        parent[find(i)] = find(j)
                grid[(gx, gy)].append(i)
    groups: Dict[int, List[List[float]]] = {}
    for i, box in enumerate(kept):
        groups.setdefault(find(i), []).append(box)
    return [_union_box(boxes) for _, boxes in sorted(groups.items())]


def _fits(box: Sequence[float], size: Tuple[float, float]) -> bool:
    """Whether a box found on the rendering is a drawing of `size`: no larger than the size (with the
    tolerance) and filling at least half of it one way (a chart's frame, a diagram's shapes)."""
    w, h = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
    sw, sh = size
    tw = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sw)
    th = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sh)
    return w <= sw + tw and h <= sh + th and (w >= 0.5 * sw or h >= 0.5 * sh)


def _same_size(box: Sequence[float], size: Tuple[float, float]) -> bool:
    w, h = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
    sw, sh = size
    tw = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sw)
    th = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sh)
    return abs(w - sw) <= tw and abs(h - sh) <= th


def _distance(box: Sequence[float], anchor: Optional[Sequence[float]]) -> float:
    """How far a candidate box is from the drawing's anchor line (vertically); 0 without an anchor."""
    if anchor is None:
        return 0.0
    return max(0.0, float(anchor[1]) - float(box[3]), float(box[1]) - float(anchor[3]))


def _grow(
    box: Sequence[float],
    size: Tuple[float, float],
    where: int,
    clusters: List[List[float]],
    used: set,
) -> List[float]:
    """A drawing drawn as several clusters (a SmartArt diagram's shapes, a chart's legend apart from
    its plot): the matched cluster grown by the unclaimed clusters of its page, nearest first, while
    the union still fits the drawing's size. The clusters taken are claimed."""
    sw, sh = size
    tw = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sw)
    th = max(DRAWING_SIZE_TOLERANCE_PT, DRAWING_SIZE_TOLERANCE_SHARE * sh)
    grown = list(box)
    order = sorted(
        (c for c in range(len(clusters)) if (where, c) not in used),
        key=lambda c: (
            _distance(clusters[c], grown) + abs(float(clusters[c][0]) - float(grown[0])) / 1000.0
        ),
    )
    for c in order:
        union = _union_box([grown, clusters[c]])
        if union[2] - union[0] <= sw + tw and union[3] - union[1] <= sh + th:
            grown = union
            used.add((where, c))
    return grown


def page_pictures(document: DigitalDocument) -> List[List[PagePicture]]:
    """The pictures on each page, in page order: what a page shows besides its text
    (atrium-project#84 decisions 3, 6).

    A raster image drawn on the page (a photo, a scan, a map or plan saved as a picture) is one
    picture, unless it is smaller than `GRAPHIC_MIN_SIDE` or it recurs at the same place on another
    page while covering less than `FURNITURE_GRAPHIC_MAX_SHARE` of it (a logo in the running header
    or footer; a scan on every page still counts on every page). A drawing of an office document that
    does not render as a picture (a chart, a SmartArt diagram, shapes, an OLE object; read from the
    DOCX's own XML, `DigitalLine.graphics`) is one picture too. It is anchored before a line, and
    counted on the next line written, so it is looked for on that line's page and the page before:
    first a raster image of its size not claimed yet (an OLE object's preview renders as an image, so
    one object never makes two pictures), else the cluster of vector paths nearest the line that its
    size fits (`_fits`), grown by the clusters around it while the union still fits (`_grow`: a
    diagram's shapes). Found, it is a picture of the page it was found on, with that box; not found,
    a picture of the line's page without a box. Pictures are in reading order: by their top edge,
    those without a box last. A page with none holds text only.
    """
    rasters = _raster_pictures(document)
    claimed: set = set()
    pictures: List[List[PagePicture]] = [[] for _ in document.pages]
    clusters: Dict[int, List[List[float]]] = {}
    used_clusters: set = set()
    for index, page in enumerate(document.pages):
        for line in page.lines:
            for k in range(int(line.graphics or 0)):
                size = line.graphic_sizes[k] if k < len(line.graphic_sizes) else (0.0, 0.0)
                known = bool(size[0] > 0 and size[1] > 0)
                found = None
                candidates = [index] + ([index - 1] if index > 0 else [])
                if known:
                    best = None
                    for where in candidates:
                        anchor = line.bbox if where == index else None
                        for r, box in enumerate(rasters[where]):
                            if (where, r) in claimed or not _same_size(box, size):
                                continue
                            key = (where != index, _distance(box, anchor))
                            if best is None or key < best[0]:
                                best = (key, where, ("raster", r), box)
                    if best is None:
                        for where in candidates:
                            anchor = line.bbox if where == index else None
                            if where not in clusters:
                                clusters[where] = _vector_clusters(document.pages[where])
                            for c, box in enumerate(clusters[where]):
                                if (where, c) in used_clusters or not _fits(box, size):
                                    continue
                                key = (where != index, _distance(box, anchor))
                                if best is None or key < best[0]:
                                    best = (key, where, ("vector", c), box)
                    if best is not None:
                        _, where, (source, n), box = best
                        (claimed if source == "raster" else used_clusters).add((where, n))
                        if source == "vector":
                            box = _grow(box, size, where, clusters[where], used_clusters)
                        found = (where, list(box))
                if found is None:
                    pictures[index].append(PagePicture(PICTURE_DRAWING))
                else:
                    pictures[found[0]].append(PagePicture(PICTURE_DRAWING, found[1]))
    for index, boxes in enumerate(rasters):
        for r, box in enumerate(boxes):
            if (index, r) not in claimed:
                pictures[index].append(PagePicture(PICTURE_IMAGE, list(box)))
    for page_pictures_ in pictures:
        page_pictures_.sort(
            key=lambda p: (
                p.bbox is None,
                float(p.bbox[1]) if p.bbox else 0.0,
                float(p.bbox[0]) if p.bbox else 0.0,
            )
        )
    return pictures


def page_graphics(document: DigitalDocument) -> List[int]:
    """How many pictures each page shows (`page_pictures`), in page order."""
    return [len(found) for found in page_pictures(document)]


def graphics_pages(document: DigitalDocument) -> List[int]:
    """The pages that show a picture, by page index, in page order: each holds a `figure` block in
    the docproc (atrium-project#84 decision 6), and with the pages forced to the ATR they are the
    pages page-classification reads (decision 4). Empty for the textual workflow, which has no pages
    to show anything."""
    if getattr(document, "workflow", "visual") != "visual":
        return []
    return [
        page.page_index
        for page, found in zip(document.pages, page_pictures(document), strict=True)
        if found
    ]


def text_layer_of(page: DigitalPage) -> str:
    """The page's text-layer verdict, as the record (`pages[].text_layer`) and `/describe` give it:
    the adapter's census, with a `digital` layer that does not decode (`needs_ocr`) as `garbled`.
    A rendered office page (`render_words` measured) keeps its census: when it goes to the ATR, it is
    for what its objects show (atrium-project#84 decision 3), not for a layer that does not decode."""
    if page.text_layer == TEXT_LAYER_DIGITAL and page.needs_ocr and page.render_words is None:
        return TEXT_LAYER_GARBLED
    if page.text_layer in TEXT_LAYERS:
        return page.text_layer
    return TEXT_LAYER_DIGITAL


@dataclass
class DigitalDocument:
    doc_id: str
    origin: str
    media_type: str
    pages: List[DigitalPage] = field(default_factory=list)
    #: SHA-512 of the original's bytes (atrium-project#84: the original is named by its SHA-512
    #: only, in the paradata; for DOC/XLS the uploaded file, not the converted one).
    sha512: str = ""
    filename: str = ""
    reading_order: str = "layout"
    #: Which engine produced the structure: `light` (pdfplumber / python-docx) or `docling`.
    engine: str = "light"
    #: `para_config.txt` component names this run actually used — the licence union's input.
    components: List[str] = field(default_factory=list)
    #: The input kind `sniff()` decided (`pdf`, `docx`, `odt`, `rtf`, `doc`; `ods`, `xlsx`, `xls`,
    #: `txt`, `csv`, `tsv`, `json`).
    kind: str = ""
    #: Reader notes worth reporting (the vendored reader's lossy/limit notes, a conversion).
    notes: List[str] = field(default_factory=list)
    #: For DOC/XLS: how the original was converted before reading (tool, target format,
    #: seconds). The record keeps the ORIGINAL's identity; this goes to the paradata.
    conversion: Optional[Dict[str, Any]] = None
    #: For an office file read with native text on rendered pages (DOCX, DOC, ODT, RTF;
    #: atrium-digital-convert#9): how LibreOffice rendered it (tool, version, seconds, pages, the
    #: PDF's SHA-512). The paradata name the PDF by SHA-512 as an output that is not stored (D23);
    #: the docproc's pages are its pages.
    rendering: Optional[Dict[str, Any]] = None
    #: The rendered PDF's bytes, while the run lasts: what `--rendered-out` writes and `/reformat`
    #: returns for the visual steps (page-classification, the ATR).
    rendered_pdf: Optional[bytes] = field(default=None, repr=False)
    #: Which workflow read the document: `visual` (pages with a verdict each; PDF, and office
    #: files on their rendered pages) or `textual` (text extracted as is, no pages to render).
    workflow: str = "visual"
    #: Splits the reading made and records (D13): `(limit, value, count, detail)`; the run's
    #: paradata note each one with the effect `split`.
    splits: List[Tuple[str, Any, int, str]] = field(default_factory=list)

    def all_lines(self) -> List[DigitalLine]:
        return [line for page in self.pages for line in page.lines]

    def use(self, *names: str) -> None:
        for name in names:
            if name not in self.components:
                self.components.append(name)


def renumber_lines(document: DigitalDocument) -> None:
    """Number every page's lines 0..n-1 in their final reading order, in place.

    `(page, line)` is the key `merge_block()` and every downstream consumer address a line
    by, so it has to be unique per page and follow reading order — which only the adapter's
    final assembly (columns, furniture, footnotes) knows.
    """
    for page in document.pages:
        for number, line in enumerate(page.lines):
            line.page = page.page
            line.line = number
