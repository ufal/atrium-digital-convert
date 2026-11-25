"""tests/test_text_inputs.py — the two workflows end to end (atrium-digital-convert#9).

Textual: TXT, CSV, TSV and JSON are extracted as they are — every column, every string value —
into a docproc without geometry whose pages never go to the ATR; long lines are wrapped and the
wrap recorded as a split (D13). Visual: an office document's pages are its rendering's, none of
them goes to the ATR, and the rendered PDF is handed over when a page shows graphics.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "digital"))
import make_fixtures  # noqa: E402

from api_util import digital_to_json as d2j  # noqa: E402
from api_util import docproc_writer  # noqa: E402
from tests.office_support import needs_libreoffice  # noqa: E402

pytest.importorskip("jsonschema")

FIXTURES = make_fixtures.build_all()


def _write(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(FIXTURES[name])
    return path


def _docproc(tmp_path: Path, name: str, **kwargs):
    out = tmp_path / f"{name}.docproc.json"
    d2j.convert(str(_write(tmp_path, name)), out_path=str(out), **kwargs)
    return json.loads(out.read_text(encoding="utf-8"))


def _lines(docproc):
    return [
        line["text"]
        for page in docproc["pages"]
        for block in page["blocks"]
        for line in block.get("lines", [])
    ]


# ── textual ─────────────────────────────────────────────────────────────────────────────────


def test_a_txt_keeps_its_lines_paragraphs_and_form_feed_pages(tmp_path):
    docproc = _docproc(tmp_path, "sample.txt")
    assert docproc["sourceFormat"] == "txt"
    assert [r["route"] for r in docproc["metadata"]["routing"]["pages"]] == [
        "text-layer",
        "text-layer",
    ]
    first = docproc["pages"][0]
    assert [len(block["lines"]) for block in first["blocks"]] == [3, 1], (
        "a blank line ends a paragraph"
    )
    assert _lines(docproc)[-1] == "Příloha 1: soupis nálezů."
    assert all("width" not in page and "needsAtr" not in page for page in docproc["pages"])
    assert {
        line["bboxPrecision"]
        for page in docproc["pages"]
        for b in page["blocks"]
        for line in b["lines"]
    } == {"absent"}


def test_a_csv_keeps_every_column_even_beside_a_text_column(tmp_path):
    """The shared reader keeps only a `text` column when it finds one; the textual workflow
    keeps the rows as they are, header included (#9: extracted as is)."""
    docproc = _docproc(tmp_path, "sample.csv")
    assert docproc["sourceFormat"] == "csv"
    assert _lines(docproc) == [
        "id\ttext\tdatace\tpoznamka",
        "1\tZlomek okraje hrnce\tHB\tnalezen v sondě II, vrstva 2",
        "2\tZvířecí kost",
        "3\tMazanice\tHB\ts otisky prutů",
    ]


def test_a_tsv_is_read_by_its_tabs(tmp_path):
    docproc = _docproc(tmp_path, "sample.tsv")
    assert docproc["sourceFormat"] == "tsv"
    assert _lines(docproc) == ["id\ttext\tdatace", "1\tZlomek okraje hrnce\tHB", "2\tZvířecí kost"]


def test_a_json_gives_every_string_value_in_document_order(tmp_path):
    docproc = _docproc(tmp_path, "sample.json")
    assert docproc["sourceFormat"] == "json"
    assert _lines(docproc) == [
        "C-202212345",
        "Sonda II",
        "Zlomek okraje hrnce",
        "Zvířecí kost",
        "z výplně objektu",
    ]
    blocks = docproc["pages"][0]["blocks"]
    assert [len(b["lines"]) for b in blocks] == [1, 1, 1, 2], "one block per string value"


@pytest.mark.parametrize(
    "name, body",
    [
        ("broken.json", '{"a": "b",'),
        ("records.json", '{"a": "b"}\n{"a": "c"}\n'),
        ("broken.csv", 'a,"b\n1,2\n'),
    ],
)
def test_a_reading_that_would_lose_content_fails(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(path))
    assert info.value.reason == "corrupt" and info.value.exit_code == 4


def test_mojibake_is_flagged_on_the_line_but_never_sends_a_textual_page_to_the_atr(tmp_path):
    path = tmp_path / "zprava.txt"
    path.write_text(
        "Zpráva o sondì èíslo 3. Nalezeny høeby, vrstva ornice mìla\n", encoding="utf-8"
    )
    out = tmp_path / "z.docproc.json"
    d2j.convert(str(path), out_path=str(out))
    docproc = json.loads(out.read_text(encoding="utf-8"))
    line = docproc["pages"][0]["blocks"][0]["lines"][0]
    assert line["flags"] == ["decode:garbled"], "scoring may read the decode flag"
    assert docproc["metadata"]["routing"]["pages"][0]["route"] == "text-layer"
    assert not docproc_writer.needs_atr(docproc)


def test_a_long_line_is_wrapped_and_the_split_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_LINE_CHARS", "1000")
    path = tmp_path / "odstavec.txt"
    path.write_text(("slovo " * 400).strip() + "\n", encoding="utf-8")  # 2,399 characters
    out = tmp_path / "o.docproc.json"
    d2j.convert(str(path), out_path=str(out), paradata_dir=str(tmp_path / "para"))
    docproc = json.loads(out.read_text(encoding="utf-8"))
    texts = _lines(docproc)
    assert len(texts) == 3 and all(len(t) <= 1000 for t in texts)
    assert " ".join(texts) == ("slovo " * 400).strip(), "nothing lost at the wraps"
    paradata = json.loads(next((tmp_path / "para").glob("*.json")).read_text(encoding="utf-8"))
    assert paradata["limits"] == [
        {
            "limit": "max_line_chars",
            "value": 1000,
            "effect": "split",
            "count": 1,
            "detail": "lines longer than 1000 characters wrapped at a word boundary",
        }
    ]
    assert paradata["parameters"]["workflow"] == "textual"


@pytest.mark.parametrize(
    "name, data",
    [
        ("scan.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64),
        ("scan.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 64),
        ("notes.md", b"# Nadpis\n\ntext\n"),
        (
            "page.alto.xml",
            b'<?xml version="1.0"?><alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"/>',
        ),
        ("records.jsonl", b'{"a": "b"}\n{"a": "c"}\n'),
    ],
)
def test_images_and_other_text_formats_are_not_inputs(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.sniff(str(path))
    assert info.value.reason == "unsupported" and info.value.exit_code == 3


def test_every_kind_has_a_workflow():
    assert set(d2j.WORKFLOWS) == set(d2j.KINDS)
    visual = {k for k, w in d2j.WORKFLOWS.items() if w == d2j.WORKFLOW_VISUAL}
    assert visual == {"pdf", "docx", "doc", "odt", "rtf"}


# ── visual: office documents on their rendered pages ───────────────────────────────────────


@needs_libreoffice
def test_an_office_document_never_goes_to_the_atr(tmp_path):
    """A pasted scan's page has no text of its own: it is routed `empty` and described by its
    graphics, for page-classification; the ATR never reads an office document (v2.0.0-beta)."""
    rendered = tmp_path / "scan_page.pdf"
    docproc = _docproc(tmp_path, "scan_page.docx", rendered_out=str(rendered))
    routing = docproc["metadata"]["routing"]["pages"]
    routes = [r["route"] for r in routing]
    assert routes == ["text-layer", "empty", "text-layer"]
    assert [r["graphics"] for r in routing] == [0, 1, 0]
    assert not any(page.get("needsAtr") for page in docproc["pages"])
    assert [p["pageNumber"] for p in docproc["pages"]] == [1, 3]
    assert all(page["coordinateUnit"] == "pt" and page["width"] > 0 for page in docproc["pages"])
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(rendered))
    assert len(pdf) == len(routes), "the docproc's pages are the rendered PDF's"
    pdf.close()


@needs_libreoffice
def test_a_page_with_good_text_and_a_photo_stays_on_its_text_layer(tmp_path):
    docproc = _docproc(tmp_path, "photo_text.docx")
    assert [r["route"] for r in docproc["metadata"]["routing"]["pages"]] == ["text-layer"]
    paragraph = docproc["pages"][0]["blocks"][0]
    assert len(paragraph["lines"]) >= 3, "the paragraph is cut into its visual lines"
    assert " ".join(line["text"] for line in paragraph["lines"]) == make_fixtures._LONG_PARAGRAPH
    assert {line["bboxPrecision"] for line in paragraph["lines"]} == {"measured"}
    tops = [line["bbox"][1] for line in paragraph["lines"]]
    assert tops == sorted(tops) and len(set(tops)) == len(tops)
    assert docproc["metadata"]["routing"]["pages"][0]["graphics"] == 1, "the photo"


@needs_libreoffice
def test_a_docx_table_is_a_table_block_with_its_cell_grid(tmp_path):
    docproc = _docproc(tmp_path, "rich.docx")
    tables = [
        (page["pageNumber"], block)
        for page in docproc["pages"]
        for block in page["blocks"]
        if block["type"] == "table"
    ]
    assert tables, "rich.docx has a table"
    for number, block in tables:
        rows = block["cellGrid"]["rows"]
        assert rows and all(row["cells"] for row in rows)
        entry = next(r for r in docproc["metadata"]["routing"]["pages"] if r["page"] == number)
        assert entry["tables"] >= 1
    routing = docproc["metadata"]["routing"]["pages"]
    assert all(r["graphics"] == 0 for r in routing), "a text box is text, not a graphic"


@needs_libreoffice
def test_the_paradata_name_the_rendered_pdf_not_stored(tmp_path):
    rendered = tmp_path / "r.pdf"
    _docproc(
        tmp_path, "scan_page.docx", rendered_out=str(rendered), paradata_dir=str(tmp_path / "para")
    )
    paradata = json.loads(next((tmp_path / "para").glob("*.json")).read_text(encoding="utf-8"))
    outputs = {o["name"]: o for o in paradata["outputs"]}
    assert outputs[d2j.RENDERED_DISTRIBUTION] == {
        "name": d2j.RENDERED_DISTRIBUTION,
        "sha512": hashlib.sha512(rendered.read_bytes()).hexdigest(),
        "media_type": "application/pdf",
        "stored": False,
    }
    assert outputs["atr/docproc-json"]["stored"] is True, (
        "no page needs the ATR: this docproc is stored"
    )
    assert paradata["parameters"]["render"].startswith("pdf:writer_pdf_Export:")
    assert paradata["parameters"]["graphic_min_side_pt"] == 24.0
    libreoffice = next(c for c in paradata["components"] if c["name"] == "libreoffice")
    assert libreoffice.get("version"), "the component with its version, as a fact"
    assert not {"license", "license_url", "license_detail"} & set(paradata)


# ── the service ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from service import api

    return TestClient(api.app)


def _reformat(client, name, **form):
    response = client.post(
        "/reformat", files={"file": (name, FIXTURES[name], "application/octet-stream")}, data=form
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_info_lists_the_inputs_of_each_workflow(client):
    info = client.get("/info").json()
    assert set(info["workflows"]["visual"]) == {"pdf", "docx", "odt", "rtf", "doc"}
    assert set(info["workflows"]["textual"]) == {"ods", "xlsx", "xls", "txt", "csv", "tsv", "json"}
    assert ".csv" in info["supported_inputs"] and "render_text_coverage" not in info["limits"]


def test_reformat_reads_a_csv_without_a_rendering(client):
    body = _reformat(client, "sample.csv", rendered="true")
    assert body["reader"]["workflow"] == "textual" and body["reader"]["rendering"] is None
    assert "rendered_pdf" not in body
    assert body["docproc"]["sourceFormat"] == "csv"


def test_reformat_reports_a_wrapped_line_in_limits_applied(client):
    files = {"file": ("dlouhy.txt", ("slovo " * 400).encode("utf-8"), "text/plain")}
    body = client.post("/reformat", files=files).json()
    assert [note["limit"] for note in body["limits_applied"]] == ["max_line_chars"]
    assert body["limits_applied"][0]["effect"] == "split"


@needs_libreoffice
def test_reformat_hands_over_the_rendered_pdf_when_a_page_shows_graphics(client):
    body = _reformat(client, "scan_page.docx")
    assert not any(p.get("needsAtr") for p in body["docproc"]["pages"])
    data = base64.b64decode(body["rendered_pdf"]["data"])
    assert data.startswith(b"%PDF") and body["rendered_pdf"]["media_type"] == "application/pdf"
    assert (
        hashlib.sha512(data).hexdigest()
        == body["rendered_pdf"]["sha512"]
        == body["reader"]["rendering"]["sha512"]
    )
    result = {r["name"]: r for r in body["paradata"]["result"]}
    assert result[d2j.RENDERED_DISTRIBUTION]["sha512"] == body["rendered_pdf"]["sha512"]


@needs_libreoffice
def test_reformat_returns_the_rendered_pdf_of_a_text_only_document_only_when_asked(client):
    assert "rendered_pdf" in _reformat(client, "photo_text.docx"), "its photo is a graphic"
    assert "rendered_pdf" not in _reformat(client, "rich.docx"), "text and a table only"
    assert _reformat(client, "rich.docx", rendered="true")["rendered_pdf"]["data"]
