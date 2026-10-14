"""
tests/test_pdf_to_md.py
=======================
Tests for api_util/pdf_to_md.py — digital-born PDF → visually-rich Markdown.

Minimal PDFs are hand-built in-process (no committed binaries, no reportlab):
one with a real text layer (digital-born) and one with none (the scanned/curve
class). The decode-sanity helpers are pure and always tested; the end-to-end
conversion tests are skipped cleanly where pdfplumber isn't installed.
"""

import pytest

from api_util import pdf_to_md
from api_util.pdf_to_md import _looks_garbled, _ocr_words_to_blocks, _page_ocr_reason

requires_pdfplumber = pytest.mark.skipif(
    not pdf_to_md.pdfplumber_available(), reason="pdfplumber not installed"
)
requires_ocr = pytest.mark.skipif(
    not pdf_to_md.ocr_available(), reason="Tesseract/pypdfium2 OCR stack not available"
)


# --------------------------------------------------------------------------- #
# Hermetic minimal-PDF builder
# --------------------------------------------------------------------------- #
def _write_min_pdf(path, content_stream: str, with_font: bool = True) -> None:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << %s >> /Contents 4 0 R >>"
        % (b"/Font << /F1 5 0 R >>" if with_font else b""),
    ]
    stream = content_stream.encode("latin-1")
    objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objs) + 1,
        xref_pos,
    )
    path.write_bytes(out)


@pytest.fixture
def digital_pdf(tmp_path):
    content = (
        "BT /F1 18 Tf 72 720 Td (Vyzkum lokality) Tj ET\n"
        "BT /F1 12 Tf 72 690 Td (Sonda odhalila zaklady kostela.) Tj ET\n"
        "BT /F1 12 Tf 72 672 Td (Druhy radek textu na strance.) Tj ET"
    )
    dest = tmp_path / "CTX100.pdf"
    _write_min_pdf(dest, content, with_font=True)
    return dest


@pytest.fixture
def scan_pdf(tmp_path):
    dest = tmp_path / "CTX200.pdf"
    _write_min_pdf(dest, "0 0 0 RG 72 72 400 200 re S", with_font=False)
    return dest


# --------------------------------------------------------------------------- #
# Decode-sanity helpers (pure — always run)
# --------------------------------------------------------------------------- #
def test_looks_garbled_flags_replacement_and_control_chars():
    assert _looks_garbled("���� normal") is True
    assert _looks_garbled("Sonda odhalila zaklady kostela.") is False
    assert _looks_garbled("   ") is False  # empty/whitespace is not "garbled"


def test_page_ocr_reason_thresholds():
    assert _page_ocr_reason("") == "no extractable text layer"
    assert _page_ocr_reason("ab") == "no extractable text layer"
    assert "garbled" in (_page_ocr_reason("������") or "")
    assert _page_ocr_reason("A clean sentence of real text.") is None


# --------------------------------------------------------------------------- #
# End-to-end conversion (needs pdfplumber)
# --------------------------------------------------------------------------- #
@requires_pdfplumber
def test_convert_digital_born_emits_cues(digital_pdf):
    md = pdf_to_md.convert(digital_pdf)
    assert md.startswith("# CTX100")
    assert "## Page 1" in md
    assert "<!-- DOC_META: size=612x792pt, orientation=portrait -->" in md
    assert "<!-- BBOX: [" in md
    assert "<!-- FONT: size=18pt" in md  # heading kept its own size cue
    assert "Vyzkum lokality" in md
    assert "Sonda odhalila zaklady kostela." in md


@requires_pdfplumber
def test_convert_textless_page_marks_needs_ocr(scan_pdf):
    md = pdf_to_md.convert(scan_pdf)
    assert "## Page 1" in md
    assert "<!-- NEEDS_OCR: pg_1 (no extractable text layer) -->" in md
    # a text-less page emits no fabricated body text
    assert "BBOX" not in md


@requires_pdfplumber
def test_convert_missing_lib_raises(monkeypatch, digital_pdf):
    monkeypatch.setattr(pdf_to_md, "pdfplumber_available", lambda: False)
    with pytest.raises(pdf_to_md.PdfPlumberNotInstalled):
        pdf_to_md.convert(digital_pdf)


# --------------------------------------------------------------------------- #
# OCR path
# --------------------------------------------------------------------------- #
def test_ocr_words_to_blocks_groups_and_scales():
    # Two paragraphs (block/par 1,1 and 2,1); a blank + low-conf word are dropped.
    data = {
        "text": ["Vyzkum", "lokality", "", "Sonda"],
        "conf": [95, 96, -1, 90],
        "left": [72, 200, 0, 72],
        "top": [59, 59, 0, 93],
        "width": [100, 120, 0, 100],
        "height": [17, 17, 0, 17],
        "block_num": [1, 1, 1, 2],
        "par_num": [1, 1, 1, 1],
    }
    blocks = _ocr_words_to_blocks(data, scale=1.0)
    assert [b["text"] for b in blocks] == ["Vyzkum lokality", "Sonda"]
    assert blocks[0]["bbox"] == [72, 59, 320, 76]  # union of the two word boxes
    # scale converts rendered pixels back to points (e.g. 72/300)
    halved = _ocr_words_to_blocks(data, scale=0.5)
    assert halved[0]["bbox"] == [36, 30, 160, 38]


def test_ocr_words_to_blocks_empty():
    assert _ocr_words_to_blocks({"text": []}) == []


@requires_ocr
def test_convert_ocr_recovers_scanned_text(tmp_path):
    """A rasterised (image-only) PDF has no text layer; --ocr recovers it."""
    import pypdfium2 as pdfium

    src = tmp_path / "born.pdf"
    _write_min_pdf(
        src,
        "BT /F1 18 Tf 72 720 Td (Vyzkum lokality) Tj ET\n"
        "BT /F1 12 Tf 72 690 Td (Sonda odhalila kostela.) Tj ET",
        with_font=True,
    )
    pil = pdfium.PdfDocument(str(src))[0].render(scale=200 / 72).to_pil().convert("RGB")
    scan = tmp_path / "CTX300.pdf"
    pil.save(str(scan), "PDF", resolution=200)

    # Without OCR: flagged; with OCR: transcribed with a provenance cue.
    assert "<!-- NEEDS_OCR:" in pdf_to_md.convert(scan)
    md = pdf_to_md.convert(scan, ocr=True)
    assert "<!-- OCR: engine=tesseract, lang=ces -->" in md
    # Tesseract may normalise diacritics (lang=ces), so assert on stable substrings.
    assert "lokality" in md
    assert "kostela" in md
    assert "<!-- BBOX: [" in md
    assert "NEEDS_OCR" not in md


def test_block_text_never_reads_as_a_cue_or_a_page_heading():
    """#6: a block reading `## Page 7` or holding a comment delimiter stays text."""
    box = {"x0": 1, "top": 2, "x1": 30, "bottom": 9, "chars": []}
    md = pdf_to_md._block_md([dict(box, text="## Page 7"), dict(box, text="a <!-- b -->")])
    assert md == "<!-- BBOX: [1, 2, 30, 9] -->\n\\## Page 7 a &lt;!-- b --&gt;"
