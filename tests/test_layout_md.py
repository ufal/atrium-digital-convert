"""
tests/test_layout_md.py
=======================
Tests for api_util/layout_md.py — the dependency-free visual-layout cue
vocabulary shared by docx_to_md.py and pdf_to_md.py. These assert the *exact*
string forms from issue #10's taxonomy so both converters stay in lock-step
with the documented schema. No heavy deps, so this module always runs.
"""

from api_util import layout_md as L

# ── HTML-comment block/page cues ─────────────────────────────────────────────


def test_page_break_normalises_int_id():
    assert L.page_break(12) == "<!-- PAGE_BREAK: pg_12 -->"


def test_page_break_passes_through_prefixed_id():
    assert L.page_break("pg_7") == "<!-- PAGE_BREAK: pg_7 -->"


def test_doc_meta_orders_and_drops_none():
    assert L.doc_meta(size="8.5x11in", orientation="portrait") == (
        "<!-- DOC_META: size=8.5x11in, orientation=portrait -->"
    )
    assert L.doc_meta(size="612x792pt") == "<!-- DOC_META: size=612x792pt -->"


def test_layout_margin_and_column():
    assert L.layout_margin(top="1in", bottom="1in", left="1in", right="1in") == (
        "<!-- LAYOUT_MARGIN: top=1in, bottom=1in, left=1in, right=1in -->"
    )
    assert L.layout_column(1, 2) == "<!-- LAYOUT_COLUMN: 1_of_2 -->"


def test_bbox_rounds_floats_to_ints():
    assert L.bbox([120, 400.0, 600, 550.7]) == "<!-- BBOX: [120, 400, 600, 551] -->"


def test_needs_ocr_default_and_custom_reason():
    assert L.needs_ocr(3) == "<!-- NEEDS_OCR: pg_3 (no extractable text layer) -->"
    assert L.needs_ocr(4, "garbled") == "<!-- NEEDS_OCR: pg_4 (garbled) -->"


# ── FONT / STYLE (only explicit attributes; empty otherwise) ─────────────────


def test_font_includes_only_supplied_attrs():
    assert L.font(size=14, weight=700, family="Courier New") == (
        '<!-- FONT: size=14pt, weight=700, family="Courier New" -->'
    )
    assert L.font(size=12) == "<!-- FONT: size=12pt -->"
    assert L.font() == ""  # nothing worth recording


def test_style_includes_only_supplied_attrs():
    assert L.style(color="#FF0000", highlight="yellow") == (
        "<!-- STYLE: color=#FF0000, highlight=yellow -->"
    )
    assert L.style(line_spacing=1.5) == "<!-- STYLE: line-spacing=1.5 -->"
    assert L.style() == ""


# ── Regions & watermark ──────────────────────────────────────────────────────


def test_header_footer_and_watermark():
    assert L.header_start() == "<!-- HEADER_START -->"
    assert L.header_end() == "<!-- HEADER_END -->"
    assert L.footer_start() == "<!-- FOOTER_START -->"
    assert L.footer_end() == "<!-- FOOTER_END -->"
    assert L.watermark("CONFIDENTIAL DRAFT") == '<!-- WATERMARK: "CONFIDENTIAL DRAFT" -->'


def test_ocr_meta():
    assert L.ocr_meta("tesseract", "ces") == "<!-- OCR: engine=tesseract, lang=ces -->"
    assert L.ocr_meta("tesseract") == "<!-- OCR: engine=tesseract -->"


# ── Inline Markdown forms ────────────────────────────────────────────────────


def test_inline_emphasis_forms():
    assert L.bold("x") == "**x**"
    assert L.italic("x") == "*x*"
    assert L.strike("x") == "~~x~~"
    assert L.underline("x") == "<u>x</u>"


def test_inline_emphasis_leaves_blank_text_unwrapped():
    assert L.bold("   ") == "   "
    assert L.strike("") == ""


def test_align_div_wraps_non_left_only():
    assert L.align_div("## Title", "center") == '<div align="center">\n## Title\n</div>'
    assert L.align_div("body", "left") == "body"
    assert L.align_div("body", None) == "body"


def test_image_with_and_without_bbox():
    assert L.image("Alt", "media/image1.png") == "![Alt](media/image1.png)"
    assert L.image("Alt", "i.png", [1, 2, 3, 4]) == "![Alt](i.png) <!-- BBOX: [1, 2, 3, 4] -->"


def test_footnote_ref_and_def():
    assert L.footnote_ref(1) == "[^1]"
    assert L.footnote_def(1, "Note content.") == "[^1]: Note content."


# ── GFM table renderer ───────────────────────────────────────────────────────


def test_md_table_header_and_escaping():
    md = L.md_table([["Col A", "Col B"], ["1", "2|3"]])
    lines = md.splitlines()
    assert lines[0] == "| Col A | Col B |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| 1 | 2\\|3 |"  # pipe escaped inside a cell


def test_md_table_pads_ragged_rows():
    md = L.md_table([["A", "B", "C"], ["1"]])
    assert md.splitlines()[-1] == "| 1 |  |  |"


def test_md_table_empty_is_empty_string():
    assert L.md_table([]) == ""


# ── Schema catalogue ─────────────────────────────────────────────────────────


def test_cue_schema_documents_core_cues():
    for name in ("PAGE_BREAK", "DOC_META", "BBOX", "FONT", "STYLE", "HEADER_START", "NEEDS_OCR"):
        assert name in L.CUE_SCHEMA


# ── document text never forges a cue (#6) ────────────────────────────────────


def test_comment_delimiters_in_text_are_escaped_and_nothing_else():
    assert L.escape_cue_delimiters("a <!-- PAGE_BREAK --> b") == "a &lt;!-- PAGE_BREAK --&gt; b"
    assert L.escape_cue_delimiters("<!-->") == "&lt;!--&gt;"
    for ordinary in ("Sonda 3 — <u>x</u> | **y** ->", "č. 12 # 3", "a -- b < c > d"):
        assert L.escape_text(ordinary) == ordinary


def test_a_line_starting_with_hashes_is_text():
    assert L.escape_line_start("## Page 7") == "\\## Page 7"
    assert L.escape_line_start("  # Title") == "  \\# Title"
    assert L.escape_line_start("Page # 7") == "Page # 7"


def test_the_escapes_are_idempotent():
    for text in ("<!-- PAGE_BREAK -->", "## Page 7", "a --> b <!-- c", "plain"):
        once = L.escape_text(text)
        assert L.escape_text(once) == once


def test_a_value_inside_a_cue_stays_on_one_line_and_inside_it():
    assert L.cue_value("7 -->\n## Page 99") == "7 --&gt; ## Page 99"
    assert L.cue_value("iv") == "iv"
    cue = L.needs_ocr("pg_1", reason="x -->\n<!-- PAGE_BREAK: pg_9 -->")
    assert cue.count("<!--") == 1 and cue.count("-->") == 1 and "\n" not in cue
    assert L.page_break("7 -->\n## Page 99").count("-->") == 1
    assert L.watermark('a --> "b"').count("-->") == 1


def test_a_table_cell_escapes_delimiters_but_keeps_a_leading_hash():
    table = L.md_table([["#", "<!-- PAGE_BREAK -->"], ["#1", "x"]], header=True)
    assert "| # | &lt;!-- PAGE_BREAK --&gt; |" in table
    assert "| #1 | x |" in table and "\\#" not in table
