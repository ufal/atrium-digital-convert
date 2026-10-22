"""tests/test_digital_report.py — the per-page assessment's route rules (atrium-digital-convert#2).

`route_page()` decides where a page goes next — `nlp`, `ocr`, `htr`, `none` — from its text-layer
verdict, its page category and its quality. The rules are deterministic and documented in the
README; these tests pin each one, and that categories are read through the vocabulary registry's
page-category facets rather than a second hard-coded list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_util import digital_report as R
from api_util import digital_to_json as d2j

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "digital"))
import make_fixtures  # noqa: E402


def _quality(clear=0, noisy=0, trash=0):
    return R.summarize_quality(
        [{"category": "Clear", "quality_score": 0.9}] * clear
        + [{"category": "Noisy", "quality_score": 0.5}] * noisy
        + [{"category": "Trash", "quality_score": 0.1}] * trash
    )


@pytest.mark.parametrize(
    "category, route",
    [
        ("TEXT_HW", "htr"),
        ("LINE_HW", "htr"),
        ("TEXT_P", "ocr"),
        ("TEXT_T", "ocr"),
        ("LINE_P", "ocr"),
        ("LINE_T", "ocr"),
        ("TEXT", "ocr"),
        ("DRAW_L", "ocr"),
        ("PHOTO_L", "ocr"),
        ("DRAW", "none"),
        ("PHOTO", "none"),
        ("SOMETHING_NEW", "ocr"),
        (None, "ocr"),
    ],
)
def test_a_page_without_usable_text_is_routed_by_its_category(category, route):
    for layer in ("none", "garbled", "ocr"):
        assert R.route_page(layer, category, None)[0] == route, (layer, category)


def test_a_decoding_page_goes_to_nlp_unless_the_quality_model_calls_it_trash():
    assert R.route_page("digital", None, None)[0] == "nlp"
    assert R.route_page("digital", None, _quality(clear=3, trash=1))[0] == "nlp"
    assert R.route_page("digital", None, _quality(clear=1, trash=3))[0] == "ocr"
    assert (
        R.route_page("digital", None, _quality(clear=1, trash=3), trash_share_limit=0.8)[0] == "nlp"
    )


def test_the_decode_check_alone_never_reroutes_a_digital_page():
    converter_quality = {"source": "digital-convert", "lines_by_category": {"Trash": 9}}
    assert R.route_page("digital", None, converter_quality)[0] == "nlp"


def test_blank_pages_have_nothing_to_read():
    assert R.route_page("blank", None, None)[0] == "none"
    assert R.route_page("none", None, None, drew_anything=False)[0] == "none"
    assert R.route_page("none", None, None, drew_anything=True)[0] == "ocr"


def test_quality_bands_follow_ocr_postprocess_plurality_vote():
    assert R.quality_band(2, 2, 2) == "Clear"
    assert R.quality_band(1, 2, 2) == "Noisy"
    assert R.quality_band(1, 1, 2) == "Trash"
    assert _quality(clear=1, noisy=1)["band"] == "Clear"
    assert R.summarize_quality([]) is None


def test_pre_filter_placeholders_do_not_move_the_score_or_the_language():
    """`Non-text`/`Empty` lines carry a 0.0 placeholder and `N/A`: no model judged them (#5)."""
    rows = [{"category": "Clear", "quality_score": 0.9, "lang": "ces"}] * 3 + [
        {"category": "Non-text", "quality_score": 0.0, "lang": "N/A"},
        {"category": "Empty", "quality_score": 0.0, "lang": "N/A"},
        {"category": "Non-text", "quality_score": 0.0, "lang": "N/A"},
        {"category": "Non-text", "quality_score": 0.0, "lang": "N/A"},
    ]
    quality = R.summarize_quality(rows)
    assert quality["score"] == 0.9, "the placeholders' 0.0 is not averaged in"
    assert quality["lang"] == "ces", "N/A does not out-vote the judged lines"
    assert quality["lines_scored"] == 3
    assert quality["lines_by_category"] == {"Clear": 3, "Empty": 1, "Non-text": 3}
    assert quality["band"] == "Clear"


def test_a_page_of_placeholders_only_has_no_score_band_or_language():
    quality = R.summarize_quality(
        [{"category": "Non-text", "quality_score": 0.0, "lang": "N/A"}] * 2
    )
    assert (quality["score"], quality["band"], quality["lang"], quality["lines_scored"]) == (
        None,
        None,
        None,
        0,
    )


def test_the_collections_come_from_the_vocabulary_registry():
    facets = R.page_category_collections()
    assert {"handwritten", "printed", "typed", "graphical", "tabular"} <= set(facets)
    assert "TEXT_HW" in facets["handwritten"] and "DRAW" in facets["graphical"]


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("minimal.pdf", [("digital", "nlp"), ("digital", "nlp")]),
        ("garbled.pdf", [("garbled", "ocr")]),
        # atrium-digital-convert#5: page 2 decodes, but 2 of its 10 words lose a letter (U+FFFD).
        ("unmapped.pdf", [("garbled", "ocr"), ("garbled", "ocr")]),
    ],
)
def test_the_report_over_the_fixtures(tmp_path, fixture, expected):
    pytest.importorskip("pdfplumber")
    path = tmp_path / fixture
    path.write_bytes(make_fixtures.build_all()[fixture])
    document = d2j.normalize(d2j.extract(str(path)))
    report = R.build_report(document)
    assert [(p["text_layer"], p["route"]) for p in report["pages"]] == expected
    summary = report["summary"]
    assert summary["pages"] == len(expected)
    assert sum(summary["routes"].values()) == len(expected)
    first = report["pages"][0]
    assert first["layout"]["canvas"]["unit"] == "pt" and first["layout"]["lines"] > 0
    assert first["text"]


def test_a_mixed_document_says_so():
    summary = R.summarize(
        [
            {"route": "nlp", "needs_ocr": False, "page_index": 1},
            {"route": "htr", "needs_ocr": True, "page_index": 2},
            {"route": "none", "needs_ocr": False, "page_index": 3},
        ]
    )
    assert summary["document_route"] == "mixed"
    assert summary["needs_ocr_pages"] == [2] and summary["reacquire_pages"] == [2]


# ── atrium-digital-convert#5: what the quality rule counts, and lost letters ─────────────────


@pytest.mark.parametrize(
    "text, group_id, kind",
    [
        ("Vrstva", "tbl0-r0c0", "table"),
        ("Výsledky záchranného archeologického výzkumu", "tbl3-r1c2", "table"),
        ("Text pod tabulkou.", "p1-0", "short"),
        ("291 749625.58 1056434.97", None, "few letters"),
        ("DR-12982-18711;4990;detail profilu", None, "few letters"),
        ("Geografický a sídelní kontext lokality ............ 12", None, "dot leaders"),
        ("Geografický a sídelní kontext lokality____________", None, "dot leaders"),
        ("Výsledky záchranného archeologického výzkumu", "p4-2", "prose"),
    ],
)
def test_a_line_is_prose_only_outside_tables_long_and_mostly_letters(text, group_id, kind):
    assert R.line_kind(text, group_id) == kind
    assert R.is_prose_line(text, group_id) == (kind == "prose")


@pytest.mark.parametrize("category", ["DRAW", "PHOTO", "TEXT_P", None])
def test_a_page_the_record_flags_for_lost_letters_goes_to_ocr_whatever_its_type(category):
    """#5 (10-22): the 29 pages of CTX202000362 are `garbled` in the record now, and 5 of them are
    drawings or photos; their text is printed, so they stay on `ocr` instead of `none`."""
    route, reason = R.route_page(
        "garbled", category, None, damaged_words=(2, 10), lost_letters=True
    )
    assert route == "ocr" and "so the record flags it" in reason
    # a page garbled for another reason is still routed by its type
    if category in ("DRAW", "PHOTO"):
        assert R.route_page("garbled", category, None, damaged_words=(2, 10))[0] == "none"


def test_a_damaged_word_holds_a_replacement_character_and_a_letter():
    assert R.count_damaged_words(["\ufffd Počet dnů", "sond\ufffd m\ufffdla"]) == (2, 5)
    assert R.count_damaged_words(["\ufffd\ufffd", ""]) == (0, 1), "an unmapped bullet is no word"
    assert R.count_damaged_words([]) == (0, 0)


def _prose_quality(prose_trash=0, prose_clear=0, other_trash=0):
    rows = (
        [{"category": "Trash", "quality_score": 0.1}] * prose_trash
        + [{"category": "Clear", "quality_score": 0.9}] * prose_clear
        + [{"category": "Trash", "quality_score": 0.1}] * other_trash
    )
    prose = [True] * (prose_trash + prose_clear) + [False] * other_trash
    return R.summarize_quality(rows, prose=prose)


def test_the_quality_rule_counts_prose_lines_only():
    quality = _prose_quality(prose_trash=5, prose_clear=1, other_trash=20)
    assert quality["prose_lines_scored"] == 6
    assert quality["prose_lines_by_category"] == {"Clear": 1, "Trash": 5}
    route, reason = R.route_page("digital", None, quality)
    assert route == "ocr" and reason.startswith(R.QUALITY_ROUTE_PREFIX)
    assert "prose lines Trash (5 of 6; ROUTE_TRASH_SHARE 0.5)" in reason

    # The AMČR case: the model calls the table cells Trash and the prose Clear.
    route, reason = R.route_page("digital", None, _prose_quality(prose_clear=6, other_trash=20))
    assert route == "nlp"
    assert "table cells" in reason and "0 of its 6 prose lines Trash" in reason

    # Fewer prose lines than MIN_PROSE_LINES: not enough to judge the page.
    route, reason = R.route_page("digital", None, _prose_quality(prose_trash=4, other_trash=20))
    assert route == "nlp" and "fewer than 5 to judge" in reason
    quality = _prose_quality(prose_trash=4)
    assert R.route_page("digital", None, quality, min_prose_lines=4)[0] == "ocr"

    quality = _prose_quality(prose_clear=6)
    assert R.route_page("digital", None, quality) == (
        "nlp",
        "trustworthy text layer (quality Clear)",
    )


def test_without_prose_flags_the_rule_counts_every_scored_line():
    quality = R.summarize_quality([{"category": "Trash"}] * 3, prose=[True])
    assert "prose_lines_by_category" not in quality, "flags that do not match the rows are ignored"
    route, reason = R.route_page("digital", None, quality)
    assert route == "ocr" and "of its scored lines Trash" in reason


def test_lost_letters_send_a_decoding_page_to_ocr_whatever_its_quality():
    clear = _quality(clear=5)
    route, reason = R.route_page("digital", None, clear, damaged_words=(6, 100))
    assert route == "ocr"
    assert "6 of its 100 words (6%)" in reason and "ROUTE_DAMAGED_WORD_SHARE 0.05" in reason
    assert R.route_page("digital", None, clear, damaged_words=(4, 100))[0] == "nlp"
    assert R.route_page("digital", None, None, damaged_words=(5, 100))[0] == "ocr", "at the limit"
    assert (
        R.route_page("digital", None, None, damaged_words=(4, 100), damaged_word_limit=0.04)[0]
        == "ocr"
    )
    assert R.route_page("digital", None, None, damaged_words=(0, 0))[0] == "nlp"
    route, reason = R.route_page("garbled", "DRAW", None, damaged_words=(90, 100))
    assert route == "none", "a page without usable text is routed by its category"


def test_lost_letters_count_on_the_body_text_not_the_running_header():
    """#5: 36 figure pages of one AMČR report lose letters only in their running header; the
    header is not re-acquired for that, a body that loses them is."""
    from api_util.digital_ir import REGION_HEADER, DigitalDocument, DigitalLine, DigitalPage

    header = DigitalLine(page="1", line=0, text="Praha � Nov� M�sto", region=REGION_HEADER)
    figure = DigitalPage(page="1", page_index=1, lines=[header], images=2)
    body = [
        DigitalLine(page="2", line=n, text="poh�ební ritus a nálezy z hrob�", group_id="p2-0")
        for n in (1, 2)
    ]
    text = DigitalPage(page="2", page_index=2, lines=[header, *body])
    document = DigitalDocument(doc_id="d", origin="digital-born-pdf", media_type="application/pdf")
    document.pages = [figure, text]
    first, second = R.build_report(document)["pages"]
    assert (first["layout"]["words"], first["layout"]["damaged_words"]) == (0, 0)
    assert first["route"] == "nlp"
    assert (second["layout"]["words"], second["layout"]["damaged_words"]) == (12, 4)
    assert second["route"] == "ocr" and "4 of its 12 words" in second["route_reason"]


def _furniture_page(index=1, images=1, vector_paths=0, body=False):
    from api_util.digital_ir import REGION_HEADER, DigitalLine, DigitalPage

    lines = [DigitalLine(page=str(index), line=0, text="Kolín 2012", region=REGION_HEADER)]
    if body:
        lines.append(DigitalLine(page=str(index), line=1, text="Objekt 22, sonda 3"))
    return DigitalPage(
        page=str(index), page_index=index, lines=lines, images=images, vector_paths=vector_paths
    )


def test_a_page_is_furniture_only_when_its_only_text_is_a_running_header_over_a_drawing():
    assert R.furniture_only(_furniture_page())
    assert R.furniture_only(_furniture_page(images=0, vector_paths=40))
    assert not R.furniture_only(_furniture_page(images=0)), "nothing drawn: nothing to read"
    assert not R.furniture_only(_furniture_page(body=True)), "body text is the page's own"


@pytest.mark.parametrize(
    "category, route",
    [
        ("DRAW_L", "ocr"),
        ("PHOTO_L", "ocr"),
        ("TEXT_P", "ocr"),
        ("TEXT_HW", "htr"),
        ("DRAW", "nlp"),
        ("PHOTO", "nlp"),
        (None, "nlp"),
    ],
)
def test_a_furniture_only_digital_page_is_routed_by_a_page_type_that_carries_text(category, route):
    """#5: excavation plans whose labels and title block are part of the image, under a running
    header: page-classification's DRAW_L says the image holds text the text layer lacks."""
    got, reason = R.route_page("digital", category, None, furniture_only=True)
    assert got == route, reason
    if route != "nlp":
        assert "running header or footer" in reason and category in reason
    # a page with body text of its own keeps it, whatever its type
    assert R.route_page("digital", category, None, furniture_only=False)[0] == "nlp"


def test_the_report_routes_a_furniture_only_page_by_its_page_type():
    from api_util.digital_ir import DigitalDocument

    document = DigitalDocument(doc_id="d", origin="digital-born-pdf", media_type="application/pdf")
    document.pages = [_furniture_page(1), _furniture_page(2), _furniture_page(3, body=True)]
    categories = {1: {"label": "DRAW_L"}, 2: {"label": "PHOTO"}, 3: {"label": "DRAW_L"}}
    pages = R.build_report(document, categories=categories)["pages"]
    assert [p["route"] for p in pages] == ["ocr", "nlp", "nlp"]
    assert "in its image" in pages[0]["route_reason"]
