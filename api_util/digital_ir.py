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
from typing import Any, Dict, List, Optional, Tuple

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
    #: `digital_docx`). Internal: counted in the page's graphics (`page_graphics`).
    graphics: int = 0
    #: The line is the text of an object, not running text: a chart's or a SmartArt diagram's
    #: text, or a text box's (DOCX, `digital_docx`). Internal: a rendered office page whose only
    #: text is object text, table cells or page furniture holds no running text (`running_text`),
    #: and goes to the ATR when it shows an object (atrium-project#84 decision 3).
    object_text: bool = False


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
    #: `page_graphics` counts them.
    image_boxes: List[List[float]] = field(default_factory=list)

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


def page_graphics(document: DigitalDocument) -> List[int]:
    """The graphics on each page, in page order: what a page shows besides its text.

    A raster image drawn on the page (a photo, a scan, a map or plan saved as a picture) counts
    once, unless it is smaller than `GRAPHIC_MIN_SIDE` or it recurs at the same place on another
    page while covering less than `FURNITURE_GRAPHIC_MAX_SHARE` of it (a logo in the running header
    or footer; a scan on every page still counts on every page). A drawing that does not render as a picture
    (a chart, a SmartArt diagram, shapes, an OLE object) counts on the page of the line it is
    anchored before (`DigitalLine.graphics`, read from a DOCX's own XML). A page with none holds
    text only.
    """

    def big(box: List[float]) -> bool:
        return (
            float(box[2]) - float(box[0]) >= GRAPHIC_MIN_SIDE
            and float(box[3]) - float(box[1]) >= GRAPHIC_MIN_SIDE
        )

    def place(box: List[float]) -> Tuple[int, ...]:
        return tuple(int(round(float(v) / 4.0)) for v in box)

    def small(box: List[float], page: DigitalPage) -> bool:
        area = float(page.width) * float(page.height)
        covered = (float(box[2]) - float(box[0])) * (float(box[3]) - float(box[1]))
        return area <= 0 or covered < FURNITURE_GRAPHIC_MAX_SHARE * area

    shown = [[box for box in page.image_boxes if big(box)] for page in document.pages]
    pages_at: Dict[Tuple[int, ...], int] = {}
    for page, boxes in zip(document.pages, shown, strict=True):
        for key in {place(box) for box in boxes if small(box, page)}:
            pages_at[key] = pages_at.get(key, 0) + 1
    furniture = {key for key, n in pages_at.items() if n > 1}
    return [
        sum(1 for box in boxes if place(box) not in furniture)
        + sum(line.graphics for line in page.lines)
        for page, boxes in zip(document.pages, shown, strict=True)
    ]


def graphics_pages(document: DigitalDocument) -> List[int]:
    """The pages that show graphics (`page_graphics` > 0), by page index, in page order: with the
    pages forced to the ATR, the pages page-classification reads (atrium-project#84 decision 4).
    A run hand-over (`/reformat`'s `graphics_pages`, `--graphics-pages-out`), never written into
    docproc (decision 6). Empty for the textual workflow, which has no pages to show anything."""
    if getattr(document, "workflow", "visual") != "visual":
        return []
    return [
        page.page_index
        for page, graphics in zip(document.pages, page_graphics(document), strict=True)
        if graphics > 0
    ]


def running_text(page: DigitalPage) -> List[DigitalLine]:
    """The page's running text: its lines outside the page furniture and the footnotes
    (`region`), outside a table (`in_table`) and outside an object (`object_text`: a chart's,
    a SmartArt diagram's or a text box's text). What a rendered office page needs to keep its
    own text instead of going to the ATR (atrium-project#84 decision 3)."""
    return [
        line
        for line in page.lines
        if line.region is None and not in_table(line.group_id) and not line.object_text
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
