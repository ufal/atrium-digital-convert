"""tests/test_docproc_coordinates.py — one coordinate space in docproc (D28, atrium-project#84).

Every box digital-convert writes is on the displayed page: origin top-left of the CropBox, after
/Rotate, in 1/72 inch x UserUnit. That is the page a viewer, pdfium and the ATR render, so the ATR's
blocks and digital-convert's lines can be matched by box when ocr-postprocess merges a mixed document
(H6). pdfplumber, the light engine's reader, applies /Rotate but measures from the MediaBox (and its
own CropBox is wrong on pages rotated 180 or 270), so `digital_ir.PageFrame` maps its boxes.

The check is independent of that code: each PDF below carries one word, the page is rendered by
pdfium at 4 px/pt, and the word's docproc box must hold the ink of the render (a glyph's box is a
little larger than its ink), and the page's width and height must be pdfium's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))

import make_fixtures as mf  # noqa: E402

from api_util import digital_to_json as d2j  # noqa: E402
from api_util import docproc_writer  # noqa: E402
from api_util.digital_ir import PageFrame  # noqa: E402

pytest.importorskip("pdfplumber")
pdfium = pytest.importorskip("pypdfium2")
pytest.importorskip("jsonschema")

SCALE = 4.0
#: How far a glyph's box may reach beyond its ink, in pt (ascender/descender room, side bearings).
SLACK = 6.0


def _pdf(mediabox, cropbox=None, rotate=0, userunit=None, x=200, y=500) -> bytes:
    stream = f"BT /F1 24 Tf {x} {y} Td (HXH) Tj ET".encode("ascii")
    extra = ""
    if cropbox:
        extra += " /CropBox [%s]" % " ".join(str(v) for v in cropbox)
    if rotate:
        extra += f" /Rotate {rotate}"
    if userunit:
        extra += f" /UserUnit {userunit}"
    page = "<< /Type /Page /Parent 2 0 R /MediaBox [%s]%s /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    return mf._serialize_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (page % (" ".join(str(v) for v in mediabox), extra)).encode("ascii"),
            4: mf._stream_obj(stream),
            5: mf.FONT_CLEAN,
        }
    )


def _ink(data: bytes):
    """pdfium's page size and the ink box of its render, in pt on the displayed page."""
    document = pdfium.PdfDocument(data)
    page = document[0]
    try:
        width, height = page.get_size()
        bitmap = page.render(scale=SCALE, grayscale=True)
        raw = bytes(bitmap.buffer)
        xs, ys = [], []
        for row in range(bitmap.height):
            start = row * bitmap.stride
            for col in range(bitmap.width):
                if raw[start + col * bitmap.n_channels] < 128:
                    xs.append(col)
                    ys.append(row)
        bitmap.close()
    finally:
        page.close()
        document.close()
    return (width, height), (
        min(xs) / SCALE,
        min(ys) / SCALE,
        (max(xs) + 1) / SCALE,
        (max(ys) + 1) / SCALE,
    )


CASES = {
    "plain": dict(mediabox=[0, 0, 612, 792]),
    "MediaBox offset": dict(mediabox=[50, 100, 662, 892], x=250, y=600),
    "CropBox": dict(mediabox=[0, 0, 612, 792], cropbox=[50, 100, 562, 742]),
    "Rotate 90": dict(mediabox=[0, 0, 612, 792], rotate=90),
    "Rotate 180": dict(mediabox=[0, 0, 612, 792], rotate=180),
    "Rotate 270": dict(mediabox=[0, 0, 612, 792], rotate=270),
    "CropBox, Rotate 90": dict(mediabox=[0, 0, 612, 792], cropbox=[50, 100, 562, 742], rotate=90),
    "CropBox, Rotate 270": dict(mediabox=[0, 0, 612, 792], cropbox=[50, 100, 562, 742], rotate=270),
    "all three, Rotate 180": dict(
        mediabox=[50, 100, 662, 892], cropbox=[80, 150, 600, 850], rotate=180, x=250, y=600
    ),
    "CropBox beyond the MediaBox": dict(mediabox=[0, 0, 612, 792], cropbox=[-20, 100, 562, 900]),
}


def _docproc(tmp_path: Path, data: bytes) -> dict:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(data)
    document, _ = d2j.prepare(str(pdf))
    return docproc_writer.build_docproc(document)  # validated against the proposal


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_a_box_is_on_the_displayed_page(tmp_path, case):
    data = _pdf(**CASES[case])
    (width, height), ink = _ink(data)
    (page,) = _docproc(tmp_path, data)["pages"]
    assert (page["width"], page["height"]) == (
        pytest.approx(width, abs=0.01),
        pytest.approx(height, abs=0.01),
    )
    (block,) = page["blocks"]
    (line,) = block["lines"]
    box = line["bbox"]
    assert box[0] <= ink[0] + 0.5 and box[1] <= ink[1] + 0.5, (box, ink)
    assert box[2] >= ink[2] - 0.5 and box[3] >= ink[3] - 0.5, (box, ink)
    assert all(abs(b - i) <= SLACK for b, i in zip(box, ink, strict=True)), (box, ink)
    assert block["bbox"] == box
    assert "userUnit" not in page


def test_the_user_unit_is_written_and_the_numbers_stay_in_its_units(tmp_path):
    """D28: coordinates are 1/72 inch x UserUnit, so the numbers are the page's own; the factor is
    written beside them (and is the record's `canvas.user_unit`)."""
    plain = _docproc(tmp_path, _pdf(mediabox=[0, 0, 612, 792]))["pages"][0]
    scaled = _docproc(tmp_path, _pdf(mediabox=[0, 0, 612, 792], userunit=2))["pages"][0]
    assert scaled["userUnit"] == 2.0
    assert (scaled["width"], scaled["height"]) == (plain["width"], plain["height"])
    assert scaled["blocks"][0]["lines"][0]["bbox"] == plain["blocks"][0]["lines"][0]["bbox"]


def test_an_unrotated_page_at_the_origin_is_left_as_read():
    frame = PageFrame(mediabox=(0.0, 0.0, 612.0, 792.0), cropbox=(0.0, 0.0, 612.0, 792.0))
    assert frame.is_identity and frame.size == (612.0, 792.0)
    assert frame.to_display([10.0, 20.0, 30.0, 40.0]) == [10.0, 20.0, 30.0, 40.0]


def test_a_blank_page_is_routed_empty_and_keeps_the_document_off_the_atr(tmp_path):
    """D29: a page whose rendering shows nothing has no `pages[]` entry and is routed `empty`; with
    text on the other pages nothing goes to the ATR, so digital-convert's docproc is the stored one."""
    text = b"BT /F1 12 Tf 72 720 Td (Zprava o vyzkumu.) Tj ET"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        4: mf._stream_obj(text),
        5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R >>",
        6: mf._stream_obj(b"1 g 0 0 612 792 re f"),  # a white page-sized fill: drawn, but blank
        7: mf.FONT_CLEAN,
    }
    docproc = _docproc(tmp_path, mf._serialize_pdf(objects))
    assert [page["pageNumber"] for page in docproc["pages"]] == [1]
    assert [(row["page"], row["route"]) for row in docproc["metadata"]["routing"]["pages"]] == [
        (1, "text-layer"),
        (2, "empty"),
    ]
    assert not docproc_writer.needs_atr(docproc)


def test_the_blank_page_threshold_is_a_paradata_parameter(tmp_path):
    """What made a text-less page `blank` shaped the routing, so the run's paradata name it."""
    from api_util import digital_pdf
    from atrium_paradata import ParadataLogger

    data = mf.blank_page_pdf()
    pdf = tmp_path / "blank_page.pdf"
    pdf.write_bytes(data)
    document, _ = d2j.prepare(str(pdf))
    docproc = docproc_writer.build_docproc(document)
    run = ParadataLogger(program="digital-convert", config={}, paradata_dir=None)
    d2j.record_run(
        run,
        document,
        docproc,
        {"data": data, "media_type": "application/pdf"},
        d2j.docproc_bytes(docproc),
    )
    run.finalize()
    assert run.record["parameters"]["blank_page"] == {
        "render_dpi": digital_pdf.BLANK_RENDER_DPI,
        "ink_level": digital_pdf.BLANK_INK_LEVEL,
    }
    assert [(o["name"], o["stored"]) for o in run.record["outputs"]] == [("atr/docproc-json", True)]
