"""tests/test_render_place.py — the native text of an office document on its rendered pages (#9).

Synthetic documents on both sides, so no LibreOffice is needed: a native reading (lines with
their groups, regions and tables) and a rendering (pages of visual lines with word boxes, as the
light PDF engine reads LibreOffice's PDF). `render_place.place()` must keep every native word,
cut native lines where the rendering breaks them, put each on its rendered page with the
rendering's boxes, and measure how much of each rendered page the native text covers.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

import pytest

from api_util import digital_to_json as d2j
from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_HEADER,
    DigitalDocument,
    DigitalLine,
    DigitalPage,
    DigitalTable,
)
from api_util.render_place import comparison_key, place

Native = Tuple[str, Optional[str]]  # (text, group_id)


def _native(lines: Sequence, tables: Sequence[DigitalTable] = ()) -> DigitalDocument:
    """One native page of `(text, group)` or `(text, group, region, heading_level)` lines."""
    page = DigitalPage(page="1", page_index=1)
    for number, spec in enumerate(lines):
        text, group, *rest = spec
        region = rest[0] if rest else None
        level = rest[1] if len(rest) > 1 else None
        page.lines.append(
            DigitalLine(
                page="1", line=number, text=text, group_id=group, region=region, heading_level=level
            )
        )
    page.tables = list(tables)
    return DigitalDocument(
        doc_id="zprava",
        origin="digital-born-docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        pages=[page],
        sha512="0" * 128,
        filename="zprava.docx",
        kind="docx",
    )


def _rendered(pages: Sequence[Sequence], images: Sequence[int] = ()) -> DigitalDocument:
    """Rendered pages of visual lines: `text` or `(text, region)`; words get boxes left to right."""
    out = DigitalDocument(doc_id="zprava", origin="digital-born-pdf", media_type="application/pdf")
    out.use("pdfplumber")
    for index, lines in enumerate(pages, 1):
        page = DigitalPage(page=str(index), page_index=index, width=595.0, height=842.0, unit="pt")
        page.images = images[index - 1] if index - 1 < len(images) else 0
        for number, spec in enumerate(lines):
            text, region = (spec, None) if isinstance(spec, str) else spec
            top = 60.0 + 14.0 * number
            words, x = [], 56.0
            for word in text.split():
                width = 5.0 * len(word)
                words.append((word, [x, top, x + width, top + 11.0]))
                x += width + 3.0
            bbox = [words[0][1][0], top, words[-1][1][2], top + 11.0] if words else None
            page.lines.append(
                DigitalLine(
                    page=str(index), line=number, text=text, bbox=bbox, region=region, words=words
                )
            )
        out.pages.append(page)
    return out


def _texts(document: DigitalDocument) -> List[List[str]]:
    return [[line.text for line in page.lines] for page in document.pages]


def _letters(texts: Sequence[str]) -> str:
    return re.sub(r"\s+", "", "".join(texts))


def test_comparison_key_folds_what_a_rendering_changes():
    assert comparison_key("ﬁnální") == comparison_key("finální") == "finální"
    assert comparison_key("„Sonda“,") == "sonda"
    assert comparison_key("archeo­logický") == "archeologický"
    assert comparison_key("•") == "" and comparison_key("–") == ""
    assert comparison_key("¹") == "1"


def test_a_paragraph_becomes_its_visual_lines():
    native = _native([("Sonda II byla položena v severní části zkoumané plochy u valu.", "p0")])
    rendered = _rendered([["Sonda II byla položena", "v severní části zkoumané", "plochy u valu."]])
    placed = place(native, rendered)
    assert _texts(placed) == [
        ["Sonda II byla položena", "v severní části zkoumané", "plochy u valu."]
    ]
    lines = placed.pages[0].lines
    assert {line.group_id for line in lines} == {"p0"}, "one paragraph, one group"
    assert [line.bbox[1] for line in lines] == [60.0, 74.0, 88.0], "each line its own box"
    assert placed.pages[0].render_words == placed.pages[0].render_matched == 11


def test_a_paragraph_across_a_page_break_is_one_block_on_each_page():
    native = _native([("Text třetí strany pokračuje na čtvrté.", "p10"), ("Závěr.", "p11")])
    rendered = _rendered([["Text třetí strany"], ["pokračuje na čtvrté.", "Závěr."]])
    placed = place(native, rendered)
    assert _texts(placed) == [["Text třetí strany"], ["pokračuje na čtvrté.", "Závěr."]]
    assert [line.page for line in placed.pages[1].lines] == ["2", "2"]
    assert [line.line for line in placed.pages[1].lines] == [0, 1], "renumbered per page"


def test_a_running_header_on_every_rendered_page_is_not_counted_against_coverage():
    native = _native(
        [
            ("Archeologický ústav – nálezová zpráva", "hdr0", REGION_HEADER),
            ("Hradiště u Horní Mezi", "p0", None, 1),
            ("Druhá strana začíná zde.", "p1"),
        ]
    )
    header = ("Archeologický ústav – nálezová zpráva", REGION_HEADER)
    rendered = _rendered(
        [
            [header, "Hradiště u Horní Mezi", ("Strana 1", REGION_FOOTER)],
            [header, "Druhá strana začíná zde."],
        ]
    )
    placed = place(native, rendered)
    assert _texts(placed) == [
        ["Archeologický ústav – nálezová zpráva", "Hradiště u Horní Mezi"],
        ["Druhá strana začíná zde."],
    ]
    assert placed.pages[0].lines[1].heading_level == 1, "the native heading level is kept"
    assert placed.pages[0].lines[0].region == REGION_HEADER
    for page in placed.pages:
        assert page.render_matched == page.render_words, "furniture does not count"


def test_list_numbers_the_rendering_adds_are_not_text_and_not_counted():
    native = _native([("První bod výčtu.", "p0"), ("Druhý bod výčtu.", "p1")])
    rendered = _rendered([["1. První bod výčtu.", "2. Druhý bod výčtu."]])
    placed = place(native, rendered)
    assert _texts(placed) == [["První bod výčtu.", "Druhý bod výčtu."]]
    assert placed.pages[0].lines[0].bbox[0] > 56.0, (
        "the box is the matched words', not the number's"
    )
    assert placed.pages[0].render_words == placed.pages[0].render_matched == 6


def test_a_text_box_drawn_elsewhere_is_found_by_the_second_pass():
    native = _native(
        [
            ("Kotva rámečku.", "p0"),
            ("Poznámka v rámečku o nálezu.", "p1"),
            ("Další odstavec textu zprávy.", "p2"),
        ]
    )
    rendered = _rendered(
        [["Kotva rámečku.", "Další odstavec textu zprávy.", "Poznámka v rámečku o nálezu."]]
    )
    placed = place(native, rendered)
    lines = {line.text: line for line in placed.pages[0].lines}
    assert lines["Poznámka v rámečku o nálezu."].bbox[1] == 88.0, "placed where it is drawn"
    assert [line.text for line in placed.pages[0].lines][1] == "Poznámka v rámečku o nálezu.", (
        "the native reading order is kept"
    )
    assert placed.pages[0].render_matched == placed.pages[0].render_words


def test_a_native_line_the_rendering_does_not_show_is_kept_without_a_box():
    native = _native(
        [("První odstavec zprávy.", "p0"), ("Skrytý text.", "p1"), ("Třetí odstavec.", "p2")]
    )
    rendered = _rendered([["První odstavec zprávy."], ["Třetí odstavec."]])
    placed = place(native, rendered)
    assert _texts(placed) == [["První odstavec zprávy.", "Skrytý text."], ["Třetí odstavec."]]
    hidden = placed.pages[0].lines[1]
    assert hidden.bbox is None, "absent, never fabricated"
    assert hidden.group_id == "p1"


def test_a_table_split_over_two_pages_becomes_one_table_on_each():
    cells = [
        {"row": r, "col": c, "is_header": r == 0, "group_id": f"tbl0-r{r}c{c}"}
        for r in range(3)
        for c in range(2)
    ]
    table = DigitalTable(table_id="t0", page="1", n_rows=3, n_cols=2, group_id="tbl0", cells=cells)
    native = _native(
        [
            ("Vrstva", "tbl0-r0c0"),
            ("Mocnost", "tbl0-r0c1"),
            ("Ornice", "tbl0-r1c0"),
            ("30 cm", "tbl0-r1c1"),
            ("Podloží", "tbl0-r2c0"),
            ("neověřeno", "tbl0-r2c1"),
        ],
        tables=[table],
    )
    rendered = _rendered([["Vrstva Mocnost", "Ornice 30 cm"], ["Podloží neověřeno"]])
    placed = place(native, rendered)
    first, second = placed.pages
    assert [t.group_id for t in first.tables] == ["tbl0"] and [
        t.group_id for t in second.tables
    ] == ["tbl0"]
    assert {c["row"] for c in first.tables[0].cells} == {0, 1}
    assert {c["row"] for c in second.tables[0].cells} == {2}
    assert [line.text for line in second.lines] == ["Podloží", "neověřeno"]


def test_a_hyphen_at_a_line_end_joins_the_word_the_native_text_has_whole():
    native = _native([("Nálezy jsou archeologicky významné.", "p0")])
    rendered = _rendered([["Nálezy jsou archeolo-", "gicky významné."]])
    placed = place(native, rendered)
    assert _texts(placed) == [["Nálezy jsou archeologicky", "významné."]]
    assert placed.pages[0].render_words == placed.pages[0].render_matched == 5


def test_ligatures_and_soft_hyphens_match():
    native = _native([("Fi­nální ﬁgurka z ohniště.", "p0")])
    rendered = _rendered([["Finální figurka z ohniště."]])
    placed = place(native, rendered)
    assert _texts(placed) == [["Fi­nální ﬁgurka z ohniště."]], "the native text, unchanged"
    assert placed.pages[0].lines[0].bbox is not None


def test_a_rendered_page_the_native_text_does_not_cover_keeps_its_own_text():
    """An office document's text is its own XML, in full: a page whose rendering shows words the
    XML does not hold (a legend drawn in a picture) is measured, never sent to the ATR
    (v2.0.0-beta; until then RENDER_TEXT_COVERAGE sent it there)."""
    native = _native([("Krátký popis.", "p0"), ("Další strana.", "p1")])
    rendered = _rendered(
        [
            ["Krátký popis."],
            ["Další strana.", "Legenda plánu sondy: hranice výkopu, profil A–B, nivelační bod"],
        ]
    )
    placed = d2j.normalize(place(native, rendered))
    first, second = placed.pages
    assert not first.needs_ocr and not second.needs_ocr
    assert second.render_matched < second.render_words
    assert _texts(placed) == [["Krátký popis."], ["Další strana."]]


def test_a_few_uncovered_words_do_not_send_a_page_to_the_atr():
    native = _native([("Krátký popis nálezu.", "p0")])
    rendered = _rendered([["a) Krátký popis nálezu."]])
    placed = d2j.normalize(place(native, rendered))
    assert not placed.pages[0].needs_ocr, "one list letter is not text the page lacks"


def test_a_page_of_one_picture_goes_to_the_atr_and_shows_a_graphic():
    """A pasted scan: the page has no text of its own and shows a picture, so it goes to the ATR,
    and the whole document with it, as a PDF page without text does (atrium-project#84 decision
    3); its picture is a figure block (decision 6). The text pages keep their own text."""
    from api_util import docproc_writer
    from api_util.digital_ir import graphics_pages, page_graphics

    native = _native([("Před plánem.", "p0"), ("Po plánu.", "p1")])
    rendered = _rendered([["Před plánem."], [], ["Po plánu."]], images=[0, 1, 0])
    rendered.pages[1].text_layer = "none"
    rendered.pages[1].image_boxes = [[40.0, 40.0, 555.0, 800.0]]
    placed = d2j.normalize(place(native, rendered))
    assert [page.needs_ocr for page in placed.pages] == [False, True, False]
    assert "no text of its own" in placed.pages[1].needs_ocr_reason
    assert [docproc_writer.route(page) for page in placed.pages] == [
        "text-layer",
        "atr",
        "text-layer",
    ]
    assert page_graphics(placed) == [0, 1, 0]
    assert graphics_pages(placed) == [2]
    blocks = docproc_writer.build_docproc(placed)["pages"][1]["blocks"]
    assert [b["type"] for b in blocks] == ["figure"]
    assert blocks[0]["bbox"] == [40.0, 40.0, 555.0, 800.0] and "imageRef" not in blocks[0]


def test_a_page_with_its_running_header_and_a_picture_goes_to_the_atr():
    """Header and footer lines are page furniture, not the page's text (the PDF furniture-only
    rule): a scan under the running header goes. A footnote is the page's text: it keeps it."""
    from api_util.digital_ir import REGION_FOOTNOTE, REGION_HEADER

    for region, forced in ((REGION_HEADER, True), (REGION_FOOTNOTE, False)):
        native = _native([("Zpráva o výzkumu", "h0"), ("Po plánu.", "p1")])
        native.pages[0].lines[0].region = region
        rendered = _rendered([["Zpráva o výzkumu"], ["Po plánu."]], images=[1, 0])
        rendered.pages[0].image_boxes = [[40.0, 80.0, 555.0, 800.0]]
        placed = d2j.normalize(place(native, rendered))
        assert placed.pages[0].needs_ocr is forced, region


def test_a_page_of_a_table_or_object_text_alone_keeps_its_text():
    """Decision 3 is pictures only: a page whose only text is a table's cells or a chart's labels
    keeps that text, whatever it shows; a drawing there is a figure block."""
    from api_util import docproc_writer
    from api_util.digital_ir import page_graphics

    native = _native([("Objekt", "tbl0-r0c0"), ("Jáma", "tbl0-r1c0"), ("Graf nálezů", "p2")])
    native.pages[0].lines[2].graphics = 1
    rendered = _rendered([["Objekt", "Jáma"], ["Graf nálezů"]])
    placed = d2j.normalize(place(native, rendered))
    assert [page.needs_ocr for page in placed.pages] == [False, False]
    assert page_graphics(placed) == [0, 1]
    figure = docproc_writer.build_docproc(placed)["pages"][1]["blocks"][-1]
    assert figure["type"] == "figure" and "bbox" not in figure, "no size, no vector paths: no box"


def test_a_page_with_only_its_rule_and_a_small_image_stays_empty():
    """R2: vector paths (a header's rule, a page border) and an image under `GRAPHIC_MIN_SIDE`
    are not objects: a page with nothing else stays `empty`, and the document keeps its text."""
    native = _native([("Před prázdnou stranou.", "p0")])
    rendered = _rendered([["Před prázdnou stranou."], []], images=[0, 1])
    rendered.pages[1].vector_paths = 3
    rendered.pages[1].image_boxes = [[500.0, 20.0, 520.0, 40.0]]
    placed = d2j.normalize(place(native, rendered))
    assert [page.needs_ocr for page in placed.pages] == [False, False]


def test_a_drawing_counts_once_on_the_first_visual_line_of_its_line():
    native = _native([("Graf ukazuje počty nálezů v jednotlivých sondách za celou sezónu.", "p0")])
    native.pages[0].lines[0].graphics = 1
    rendered = _rendered([["Graf ukazuje počty nálezů", "v jednotlivých sondách za celou sezónu."]])
    placed = place(native, rendered)
    assert [line.graphics for line in placed.pages[0].lines] == [1, 0]


def test_a_logo_repeated_on_every_page_is_not_a_graphic():
    from api_util.digital_ir import page_graphics

    native = _native([("První strana.", "p0"), ("Druhá strana.", "p1")])
    rendered = _rendered([["První strana."], ["Druhá strana."]], images=[2, 1])
    logo = [500.0, 20.0, 560.0, 60.0]
    rendered.pages[0].image_boxes = [list(logo), [60.0, 200.0, 400.0, 500.0]]
    rendered.pages[1].image_boxes = [list(logo)]
    assert page_graphics(place(native, rendered)) == [1, 0]


def test_a_scan_on_every_page_is_a_graphic_on_every_page():
    """A page-sized picture at the same place on every page is the pages' content, not furniture."""
    from api_util.digital_ir import page_graphics

    native = _native([("První strana.", "p0"), ("Druhá strana.", "p1")])
    rendered = _rendered([["První strana."], ["Druhá strana."]], images=[1, 1])
    for page in rendered.pages:
        page.image_boxes = [[0.0, 0.0, 595.0, 842.0]]
    assert page_graphics(place(native, rendered)) == [1, 1]


@pytest.mark.parametrize("seed", range(5))
def test_every_native_character_is_kept_once(seed):
    import random

    rng = random.Random(seed)
    vocabulary = (
        "sonda vrstva ornice keramika zlomek kost objekt výplň hlína uhlík val příkop".split()
    )
    paragraphs = [
        " ".join(rng.choice(vocabulary) for _ in range(rng.randint(3, 25))) for _ in range(12)
    ]
    native = _native([(text, f"p{n}") for n, text in enumerate(paragraphs)])
    words = " ".join(paragraphs).split()
    # a rendering of 6 words a line, 8 lines a page, with a running header and an extra word
    lines, pages = [], []
    for start in range(0, len(words), 6):
        lines.append(" ".join(words[start : start + 6]))
    for start in range(0, len(lines), 8):
        pages.append([("Zpráva", REGION_HEADER), *lines[start : start + 8], "1."])
    placed = place(native, _rendered(pages))
    out = [line.text for page in placed.pages for line in page.lines]
    assert _letters(out) == _letters(paragraphs)
    assert all(line.bbox for page in placed.pages for line in page.lines)


def test_the_textual_workflow_never_needs_the_atr():
    page = DigitalPage(page="1", page_index=1)
    page.lines.append(
        DigitalLine(
            page="1", line=0, text="Zpráva o sondì èíslo 3. Nalezeny høeby, vrstva ornice mìla"
        )
    )
    document = DigitalDocument(
        doc_id="x", origin="digital-born-txt", media_type="text/plain", pages=[page]
    )
    document.workflow = d2j.WORKFLOW_TEXTUAL
    d2j.normalize(document)
    assert page.lines[0].decode == "garbled", "the decode flag stays on the line for scoring"
    assert not page.needs_ocr, "nothing to render: no ATR (#9 point 4)"


# ── boxing a drawing on the rendering (atrium-project#84 decision 6) ──────────────────────────


def _drawing_page(index: int, vectors=(), images=(), lines=()) -> DigitalPage:
    page = DigitalPage(page=str(index), page_index=index, width=595.0, height=842.0, unit="pt")
    page.vector_boxes = [list(b) for b in vectors]
    page.image_boxes = [list(b) for b in images]
    page.lines = list(lines)
    return page


def _drawing_doc(*pages: DigitalPage) -> DigitalDocument:
    return DigitalDocument(
        doc_id="d", origin="digital-born-docx", media_type="x", pages=list(pages), kind="docx"
    )


def _anchor(top: float, size, graphics: int = 1) -> DigitalLine:
    return DigitalLine(
        page="1",
        line=0,
        text="Graf",
        bbox=[56.0, top, 120.0, top + 11.0],
        graphics=graphics,
        graphic_sizes=[size] * graphics,
    )


def test_an_ole_preview_drawn_as_an_image_is_one_picture_not_two():
    """An OLE object counts as a drawing in the DOCX and renders its preview as an image: the image
    of its size is claimed by the drawing, so the page shows one picture with that box."""
    from api_util.digital_ir import PICTURE_DRAWING, page_pictures

    page = _drawing_page(
        1, images=[[56.0, 100.0, 200.0, 172.0]], lines=[_anchor(180.0, (144.0, 72.0))]
    )
    [pictures] = page_pictures(_drawing_doc(page))
    assert [(p.kind, p.bbox) for p in pictures] == [(PICTURE_DRAWING, [56.0, 100.0, 200.0, 172.0])]


def test_a_diagrams_shapes_are_grown_into_one_box_and_borders_are_left_out():
    """SmartArt is drawn as separate shapes: the nearest cluster that fits the drawing's size is
    grown by the others while the union still fits. A full-width rule is no part of it."""
    from api_util.digital_ir import page_pictures

    shapes = [
        [60.0, 100.0, 180.0, 160.0],
        [200.0, 100.0, 320.0, 160.0],
        [340.0, 100.0, 460.0, 160.0],
    ]
    rule = [40.0, 60.0, 555.0, 60.5]
    page = _drawing_page(1, vectors=[*shapes, rule], lines=[_anchor(170.0, (432.0, 86.4))])
    [pictures] = page_pictures(_drawing_doc(page))
    assert [p.bbox for p in pictures] == [[60.0, 100.0, 460.0, 160.0]]


def test_a_drawing_counted_on_the_next_page_is_found_on_the_page_before():
    """A chart alone on its page counts on the next line written, on the next page: it is looked
    for there and on the page before, and is a picture of the page it was found on."""
    from api_util.digital_ir import page_graphics, page_pictures

    chart_page = _drawing_page(1, vectors=[[57.0, 66.0, 452.0, 281.0]])
    text_page = _drawing_page(2, lines=[_anchor(60.0, (396.0, 216.0))])
    document = _drawing_doc(chart_page, text_page)
    assert page_graphics(document) == [1, 0]
    assert page_pictures(document)[0][0].bbox == [57.0, 66.0, 452.0, 281.0]


def test_a_drawing_not_found_keeps_no_box_on_its_anchors_page():
    from api_util.digital_ir import page_pictures

    tiny = _drawing_page(
        1, vectors=[[60.0, 100.0, 70.0, 110.0]], lines=[_anchor(120.0, (396.0, 216.0))]
    )
    [pictures] = page_pictures(_drawing_doc(tiny))
    assert len(pictures) == 1 and pictures[0].bbox is None
