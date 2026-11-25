"""tests/test_describe.py — `POST /describe`, the parked per-page assessment (atrium-digital-convert#2).

Since v2.0.0-beta `/describe` calls no other tool: it describes what the converter itself read
(text layer, decode check, layout, needs_atr, a route), and leaves a page's type and the quality
of its text to page-classification and ocr-postprocess, which the pipeline runs as its own steps.
What must hold: the assessment comes from the converter alone, no request leaves the service,
and the answer carries no stage outcomes.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pdfplumber")
pytest.importorskip("jsonschema")

from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))
import make_fixtures  # noqa: E402

from service import api  # noqa: E402
from tests.office_support import needs_libreoffice  # noqa: E402

client = TestClient(api.app)
FIXTURES = make_fixtures.build_all()


def _describe(name, data=None, **form):
    files = {
        "file": (name, data if data is not None else FIXTURES[name], "application/octet-stream")
    }
    response = client.post("/describe", files=files, data=form)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound connection fails the test: `/describe` must not reach another service."""
    attempts = []

    def refuse(self, address, *args, **kwargs):
        attempts.append(address)
        raise AssertionError(f"/describe opened a connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    # The URLs the stages used to be called through, set as an old deployment may still set them.
    monkeypatch.setenv("PAGE_CLASSIFICATION_URL", "http://page-classification.test:8000")
    monkeypatch.setenv("OCR_POSTPROCESS_URL", "http://ocr-postprocess.test:8000")
    return attempts


def test_describe_calls_no_other_tool(no_network):
    body = _describe("garbled.pdf")
    assert no_network == []
    assert "stages" not in body
    [page] = body["pages"]
    assert "category" not in page and "quality" not in page
    assert (page["text_layer"], page["needs_atr"], page["route"]) == ("garbled", True, "ocr")
    assert "page type unknown" in page["route_reason"]


def test_the_old_stage_fields_are_no_longer_part_of_the_api():
    spec = api.app.openapi()
    body = spec["paths"]["/describe"]["post"]["requestBody"]["content"]["multipart/form-data"]
    form = spec["components"]["schemas"][body["schema"]["$ref"].rsplit("/", 1)[1]]["properties"]
    assert "stages" not in form and "classify_pages" not in form
    assert "stages" not in spec["components"]["schemas"]["DescribeResponse"]["properties"]
    assert "stages" not in client.get("/info").json()


def test_clean_pages_go_to_nlp_on_the_converters_own_check():
    body = _describe("minimal.pdf")
    for page in body["pages"]:
        assert page["route"] == "nlp"
        assert "quality model not run" in page["route_reason"]
        assert page["decode"]["decode_score"] is not None
    assert body["summary"]["document_route"] == "nlp"
    assert body["document"]["source"]["origin"] == "digital-born-pdf"


def test_a_page_whose_only_text_is_its_page_number_is_described_from_its_text_layer():
    """#5: stamped_scan.pdf's page 1 is a page number over a full-page image (`none`, needs_atr)
    and its page 3 a page number over a half-page image, `digital` under
    FURNITURE_ONLY_IMAGE_COVERAGE. Without a page type the half-page one stays `nlp`: whether its
    image holds text is page-classification's answer, a step of the pipeline."""
    first, second, third = _describe("stamped_scan.pdf")["pages"]
    assert (first["text_layer"], first["needs_atr"], first["route"]) == ("none", True, "ocr")
    assert second["route"] == "nlp"
    assert (third["text_layer"], third["route"]) == ("digital", "nlp")
    assert third["layout"]["regions"]["page_header"] + third["layout"]["regions"]["page_footer"]


@needs_libreoffice
def test_a_docx_is_described_on_its_rendered_pages():
    body = _describe("rich.docx")
    assert body["reader"]["kind"] == "docx" and body["reader"]["rendering"]
    assert body["pages"] and all(page["text_layer"] != "none" for page in body["pages"])


def test_the_decode_verdict_is_the_converters_own():
    """D18 rules 1-3: the decode verdict is a flag beside the line's decode score."""
    body = _describe("garbled.pdf")
    record = body["document"]
    assert all(row["flags"] == ["decode:garbled"] for row in record["lines"])
    assert all("decode_score" in row for row in record["lines"])
    assert not any("categ" in row for row in record["lines"]), "nothing scored the lines"
    [page] = body["pages"]
    assert (
        page["decode"]["lines_by_flag"] == {"decode:garbled": 3}
        and page["decode"]["lines_decoded"] == 0
    )


def test_include_text_false_leaves_the_text_out():
    body = _describe("minimal.pdf", include_text="false")
    assert all("text" not in page for page in body["pages"])


def test_lost_letters_are_counted_and_route_nothing():
    """atrium-digital-convert#9 point 3 (11-13): a page that decodes but loses letters stays
    `digital`, unflagged; the report still counts its damaged words. (#5 flagged and routed it to
    OCR from 10-22.)"""
    page = _describe("unmapped.pdf")["pages"][1]
    assert (page["text_layer"], page["needs_atr"], page["route"]) == ("digital", False, "nlp")
    assert (page["layout"]["words"], page["layout"]["damaged_words"]) == (10, 2)
