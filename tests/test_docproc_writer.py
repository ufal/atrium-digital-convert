"""tests/test_docproc_writer.py — digital-convert's docproc and the schema it is written against.

Since ARÚP's output contract of 12 November 2026 (atrium-project#84 §1) digital-convert writes
docproc (`atr/docproc-json`), not the record. `schemas/docproc/document.actual.schema.json` is the
ATR's schema as delivered (#84 comment 1); `document.atrium-proposal.schema.json` is that schema
with asks 3 and 13 applied (`tools/docproc_proposal.py`). These tests pin the two together and
the writer to the proposal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))

import docproc_proposal  # noqa: E402

from api_util import digital_to_json as d2j  # noqa: E402
from api_util import docproc_writer  # noqa: E402

pytest.importorskip("jsonschema")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── the two schemas ────────────────────────────────────────────────────────────────────────


def test_the_actual_schema_is_the_one_delivered():
    """Kept verbatim: the ATR's `$id`, its required fields and its producer pattern."""
    actual = _load(docproc_writer.ACTUAL_SCHEMA)
    assert actual["$id"] == "https://stellarbase.ai/schemas/docproc/v1/document.actual.schema.json"
    assert actual["required"] == ["version", "pipeline", "processedAt", "pages"]
    assert actual["properties"]["pipeline"]["pattern"] == "^pipeline-e2e(/[a-z0-9-]+)?$"


def test_the_proposal_is_the_actual_schema_with_asks_3_and_13_only():
    """The committed proposal is exactly what `tools/docproc_proposal.py` derives: regenerate it
    with that script after a change to either, never by hand."""
    assert _load(docproc_writer.PROPOSAL_SCHEMA) == docproc_proposal.proposal()


def test_the_proposal_changes_only_what_the_asks_name():
    actual = _load(docproc_writer.ACTUAL_SCHEMA)
    proposal = _load(docproc_writer.PROPOSAL_SCHEMA)
    # ask 3: run data is no longer required
    assert set(actual["required"]) - set(proposal["required"]) == {"processedAt", "pipeline"}
    # ask 13: other producers and formats; the page verdict and block lines; flow documents
    assert set(proposal["properties"]["sourceFormat"]["enum"]) - set(
        actual["properties"]["sourceFormat"]["enum"]
    ) == set(docproc_proposal.FLOW_FORMATS)
    page, block = proposal["$defs"]["Page"], proposal["$defs"]["Block"]
    assert set(page["properties"]) - set(actual["$defs"]["Page"]["properties"]) == {
        "textLayer",
        "needsAtr",
        "needsAtrReason",
        "userUnit",
    }
    assert set(block["properties"]) - set(actual["$defs"]["Block"]["properties"]) == {"lines"}
    assert set(proposal["$defs"]) - set(actual["$defs"]) == {"Line"}
    # nothing else moved: every other definition is the ATR's own
    for name in set(actual["$defs"]) - {"Page", "Block"}:
        assert proposal["$defs"][name] == actual["$defs"][name], name
    assert {
        k: v for k, v in proposal["properties"].items() if k not in ("pipeline", "sourceFormat")
    } == {k: v for k, v in actual["properties"].items() if k not in ("pipeline", "sourceFormat")}


def test_the_atrs_own_output_shape_still_validates_against_the_proposal():
    """The proposal only widens: a document the ATR emits today stays valid."""
    atr = {
        "version": "1.0",
        "pipeline": "pipeline-e2e",
        "processedAt": "2026-11-10T12:00:00+00:00",
        "sourceFormat": "pdf-scanned",
        "pages": [
            {
                "pageNumber": 1,
                "width": 595.0,
                "height": 842.0,
                "renderDpi": 200,
                "blocks": [
                    {
                        "id": "p1-b0",
                        "type": "text",
                        "bbox": [10, 10, 100, 20],
                        "readingOrder": 0,
                        "content": "Zpráva",
                    }
                ],
            }
        ],
        "metadata": {"routing": {"pages": [{"page": 1, "route": "ocr", "text_layer": False}]}},
    }
    assert docproc_writer.problems(atr, docproc_writer.ACTUAL_SCHEMA) == []
    assert docproc_writer.problems(atr) == []


# ── the writer ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    pytest.importorskip("pdfplumber")
    import make_fixtures

    out = tmp_path_factory.mktemp("digital")
    for name, data in make_fixtures.build_all().items():
        (out / name).write_bytes(data)
    return out


def _docproc(path: Path) -> dict:
    document, _ = d2j.prepare(str(path))
    return docproc_writer.build_docproc(document)


def test_blocks_are_numbered_from_zero_in_reading_order(fixtures):
    for page in _docproc(fixtures / "two_column.pdf")["pages"]:
        orders = [b["readingOrder"] for b in page["blocks"]]
        assert orders == list(range(len(orders)))
        assert [b["id"] for b in page["blocks"]] == [
            f"p{page['pageNumber']}-b{n}" for n in range(len(orders))
        ]


def test_a_block_is_its_lines_and_its_box_their_union(fixtures):
    for page in _docproc(fixtures / "two_column.pdf")["pages"]:
        for block in page["blocks"]:
            lines = block["lines"]
            assert block["content"] == "\n".join(line["text"] for line in lines)
            assert block["bbox"][0] == min(line["bbox"][0] for line in lines)
            assert block["bbox"][3] == max(line["bbox"][3] for line in lines)


def test_every_page_is_routed_and_an_empty_one_is_not_listed(fixtures):
    """H15: every page 1..N is in `pages[]` or routed `empty`; the image-only fixture's plate and
    blank page need the ATR or nothing."""
    docproc = _docproc(fixtures / "image_only.pdf")
    routing = docproc["metadata"]["routing"]["pages"]
    assert [r["page"] for r in routing] == [1, 2, 3]
    listed = {p["pageNumber"] for p in docproc["pages"]}
    assert listed == {r["page"] for r in routing if r["route"] != "empty"}
    assert routing[0] == {"page": 1, "route": "text-layer", "text_layer": True}


def test_a_page_for_the_atr_says_why(fixtures):
    for page in _docproc(fixtures / "image_only.pdf")["pages"]:
        if page.get("needsAtr"):
            assert page["needsAtrReason"] and page["textLayer"] in ("none", "garbled", "ocr")
        else:
            assert "needsAtrReason" not in page


def test_no_run_data_is_written(fixtures):
    docproc = _docproc(fixtures / "minimal.pdf")
    assert not {"processedAt", "pipeline", "pipelineVersion", "sourceFile", "sourceHash"} & set(
        docproc
    )


def test_needs_atr_says_whether_this_docproc_is_the_stored_one(fixtures):
    assert docproc_writer.needs_atr(_docproc(fixtures / "minimal.pdf")) is False
    assert docproc_writer.needs_atr(_docproc(fixtures / "image_only.pdf")) is True


# ── D13: a cap that would cut content fails ───────────────────────────────────────────────────


def test_an_ods_over_the_repeat_cap_fails_rather_than_dropping_rows(tmp_path, monkeypatch):
    """atrium-project#84 (D13): until #84 the rows past the cap were dropped with a note."""
    from atrium_limits import ContentLimitError
    from tests.test_digital_formats import _odf

    monkeypatch.setenv("ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP", "2")
    path = _odf(
        tmp_path / "sheet.ods",
        "application/vnd.oasis.opendocument.spreadsheet",
        '<office:spreadsheet><table:table table:name="Nálezy">'
        '<table:table-row table:number-rows-repeated="3">'
        "<table:table-cell><text:p>střep</text:p></table:table-cell></table:table-row>"
        "</table:table></office:spreadsheet>",
    )
    with pytest.raises(ContentLimitError) as caught:
        d2j.extract(str(path))
    assert (caught.value.limit, caught.value.effect) == ("odf_repeat_cap", "trimmed")
    assert d2j.main([str(path), "--out-dir", str(tmp_path / "out")]) == 4
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
