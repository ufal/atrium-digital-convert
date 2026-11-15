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


def test_a_rendered_page_the_native_text_does_not_cover_needs_the_atr():
    native = _native([("Krátký popis.", "p0"), ("Další strana.", "p1")])
    rendered = _rendered(
        [
            ["Krátký popis."],
            ["Další strana.", "Legenda plánu sondy: hranice výkopu, profil A–B, nivelační bod"],
        ]
    )
    placed = d2j.normalize(place(native, rendered))
    first, second = placed.pages
    assert not first.needs_ocr
    assert second.needs_ocr and "does not place there" in second.needs_ocr_reason
    assert "RENDER_TEXT_COVERAGE" in second.needs_ocr_reason


def test_a_few_uncovered_words_do_not_send_a_page_to_the_atr():
    native = _native([("Krátký popis nálezu.", "p0")])
    rendered = _rendered([["a) Krátký popis nálezu."]])
    placed = d2j.normalize(place(native, rendered))
    assert not placed.pages[0].needs_ocr, "one list letter is not text the page lacks"


def test_a_page_of_one_picture_needs_the_atr():
    native = _native([("Před plánem.", "p0"), ("Po plánu.", "p1")])
    rendered = _rendered([["Před plánem."], [], ["Po plánu."]], images=[0, 1, 0])
    rendered.pages[1].text_layer = "none"
    placed = d2j.normalize(place(native, rendered))
    assert [page.needs_ocr for page in placed.pages] == [False, True, False]
    assert "no extractable text layer" in placed.pages[1].needs_ocr_reason


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
    assert page.lines[0].categ == "Garbage", "the decode flag stays on the line for scoring"
    assert not page.needs_ocr, "nothing to render: no ATR (#9 point 4)"
