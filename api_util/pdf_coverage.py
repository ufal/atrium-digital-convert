"""
api_util/pdf_coverage.py — how much of a PDF page its images cover (atrium-digital-convert#5).

The light engine (`api_util/digital_pdf.py`) asks this about one kind of page only: a page
whose text is nothing but its running header or footer. When images cover most of such a page,
it is a scan that the PDF's producer numbered or stamped, and its content has no text layer.
`tools/samples_report.py` measures the same signal on every page of a run.

Image coverage is the share of a page's crop box under the union of its image objects. They are
read with pypdfium2 down to `FORM_DEPTH` levels of Form XObjects (the depth of the light engine's
census); a nested object's box, which PDFium gives in its form's space, is carried to the page
through its containers' matrices. The union is rasterised on a `GRID` × `GRID` raster by cell
centres. pypdfium2 is imported by the caller (it is already a light-engine dependency); this
module imports nothing optional.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, List, Sequence, Tuple

#: Raster side for the coverage union: the error is at most half a cell along each edge.
GRID = 256
#: Form XObject depth searched for images (`digital_pdf._census` uses the same).
FORM_DEPTH = 4
#: Image objects of one page put on the raster (the reader's PDF_OBJECT_CAP default).
IMAGE_CAP = 20_000

Box = Tuple[float, float, float, float]


def union_share(boxes: Iterable[Box], frame: Box, grid: int = GRID) -> float:
    """Share of `frame` under the union of `boxes`, all `(x0, y0, x1, y1)`.

    Rasterised on `grid` × `grid` cells; a cell counts when its centre is inside a box, so the
    error is at most half a cell along each edge of the union.
    """
    fx0, fy0, fx1, fy1 = frame
    width, height = fx1 - fx0, fy1 - fy0
    if width <= 0 or height <= 0:
        return 0.0

    def span(lo: float, hi: float, origin: float, size: float) -> Tuple[int, int]:
        first = math.ceil((min(lo, hi) - origin) / size * grid - 0.5)
        last = math.ceil((max(lo, hi) - origin) / size * grid - 0.5)
        return max(first, 0), min(last, grid)

    rows = [bytearray(grid) for _ in range(grid)]
    for x0, y0, x1, y1 in boxes:
        (c0, c1), (r0, r1) = span(x0, x1, fx0, width), span(y0, y1, fy0, height)
        ones = b"\x01" * max(c1 - c0, 0)
        for r in range(r0, r1):
            rows[r][c0:c1] = ones
    return sum(row.count(1) for row in rows) / (grid * grid)


def _transform(matrix: Sequence[float], box: Box) -> Box:
    a, b, c, d, e, f = matrix
    corners = ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))
    xs = [a * x + c * y + e for x, y in corners]
    ys = [b * x + d * y + f for x, y in corners]
    return min(xs), min(ys), max(xs), max(ys)


def image_boxes(page: Any) -> List[Box]:
    """Page-space boxes of a pypdfium2 page's image objects, those inside forms included."""
    import pypdfium2.raw as pdfium_c  # noqa: PLC0415  (light-engine dependency, imported on use)

    boxes: List[Box] = []
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=FORM_DEPTH):
        if len(boxes) >= IMAGE_CAP:
            break
        try:
            bounds = getattr(obj, "get_bounds", None) or obj.get_pos  # pypdfium2 5.x / 4.x
            box: Box = tuple(bounds())  # type: ignore[assignment]
            container = obj.container
            while container is not None:
                box = _transform(container.get_matrix().get(), box)
                container = container.container
        except Exception:
            continue
        boxes.append(box)
    return boxes


def page_frame(page: Any) -> Box:
    """The page's crop box, or its media size when PDFium cannot give one."""
    try:
        return tuple(page.get_cropbox())  # type: ignore[return-value]
    except Exception:
        width, height = page.get_size()
        return 0.0, 0.0, width, height


def image_coverage(page: Any) -> Tuple[int, float]:
    """`(image objects, share of the page they cover)` for a pypdfium2 page."""
    boxes = image_boxes(page)
    return len(boxes), union_share(boxes, page_frame(page))
