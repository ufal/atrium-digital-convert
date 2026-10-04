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


def test_the_collections_come_from_the_vocabulary_registry():
    facets = R.page_category_collections()
    assert {"handwritten", "printed", "typed", "graphical", "tabular"} <= set(facets)
    assert "TEXT_HW" in facets["handwritten"] and "DRAW" in facets["graphical"]


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("minimal.pdf", [("digital", "nlp"), ("digital", "nlp")]),
        ("garbled.pdf", [("garbled", "ocr")]),
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
