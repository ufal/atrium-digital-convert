"""tests/test_text_inputs.py — the two workflows end to end (atrium-digital-convert#9).

Textual: TXT, CSV, TSV and JSON are extracted as they are — every column, every string value —
into a docproc without geometry whose pages never go to the ATR; long lines are wrapped and the
wrap recorded as a split (D13). Visual: an office document's pages are its rendering's; a page
with no running text that shows an object goes to the ATR (atrium-project#84 decision 3), and the
rendered PDF is handed over, stored, when the ATR or page-classification reads it (decision 5).
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
def test_a_pasted_scan_sends_the_office_document_to_the_atr(tmp_path):
    """atrium-project#84 decision 3: scan_page.docx's page 2 holds a scan and its caption in a text
    box, no running text, so it goes to the ATR, and the whole document with it, as the rendered
    PDF (decision 5), whose pages and boxes the docproc's are. The caption stays a native line,
    measured on the rendering (R8)."""
    rendered = tmp_path / "scan_page.pdf"
    docproc = _docproc(tmp_path, "scan_page.docx", rendered_out=str(rendered))
    routing = docproc["metadata"]["routing"]["pages"]
    routes = [r["route"] for r in routing]
    assert routes == ["text-layer", "atr", "text-layer"]
    assert all(set(r) == {"page", "route", "text_layer"} for r in routing), "decision 6"
    forced = [page for page in docproc["pages"] if page.get("needsAtr")]
    assert [page["pageNumber"] for page in forced] == [2]
    assert "without running text" in forced[0]["needsAtrReason"]
    assert forced[0]["textLayer"] == "digital", "the rendering's census, not a layer that fails"
    caption = forced[0]["blocks"][0]["lines"]
    assert [line["text"] for line in caption] == [make_fixtures.SCAN_CAPTION]
    assert caption[0]["bboxPrecision"] == "measured"
    assert [p["pageNumber"] for p in docproc["pages"]] == [1, 2, 3]
    assert all(page["coordinateUnit"] == "pt" and page["width"] > 0 for page in docproc["pages"])
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(rendered))
    assert len(pdf) == len(routes), "the docproc's pages are the rendered PDF's"
    pdf.close()


@needs_libreoffice
def test_an_office_page_goes_to_the_atr_for_its_objects_not_for_its_furniture(tmp_path):
    """Decision 3 with R2: a page with no running text goes to the ATR when it shows an object
    (a picture under the running header, a table, a chart), never for its rule, its repeated logo
    or its header alone; a page with nothing on it stays `empty`; running text keeps its page."""
    docproc = _docproc(tmp_path, "objects_pages.docx")
    routes = [r["route"] for r in docproc["metadata"]["routing"]["pages"]]
    assert routes == ["atr", "atr", "atr", "empty", "text-layer"]
    reasons = {p["pageNumber"]: p["needsAtrReason"] for p in docproc["pages"] if p.get("needsAtr")}
    assert "1 graphic(s)" in reasons[1], "the picture; the header's logo is furniture"
    assert "1 table(s)" in reasons[2]
    assert "chart, diagram or text-box text" in reasons[3]
    document, _ = d2j.prepare(str(_write(tmp_path, "objects_pages.docx")))
    assert d2j.graphics_pages(document) == [1, 3], "the logo repeated on every page is not counted"


@needs_libreoffice
def test_an_odt_page_goes_to_the_atr_only_with_no_line_and_a_graphic(tmp_path):
    """R3: ODT and RTF come from the shared reader as plain lines (no table, furniture or object
    marks), so only a page with no line at all that shows graphics goes to the ATR: the picture
    page does, the table page keeps its cells, and the chart (vector drawings there) is not
    counted."""
    import subprocess

    from api_util.digital_legacy import libreoffice_binary

    source = _write(tmp_path, "objects_pages.docx")
    subprocess.run(
        [
            libreoffice_binary(),
            "--headless",
            "--convert-to",
            "odt",
            "--outdir",
            str(tmp_path),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    odt = tmp_path / "objects_pages.odt"
    out = tmp_path / "objects_pages.odt.docproc.json"
    d2j.convert(str(odt), out_path=str(out))
    docproc = json.loads(out.read_text(encoding="utf-8"))
    routing = {r["page"]: r["route"] for r in docproc["metadata"]["routing"]["pages"]}
    assert routing[1] == "atr", "a picture and no line"
    assert routing[2] == "text-layer", "the table's cells are plain lines here"
    assert routing[3] != "atr", "a chart is vector drawing in an ODT, not a counted graphic"


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
    document, _ = d2j.prepare(str(_write(tmp_path, "photo_text.docx")))
    assert d2j.graphics_pages(document) == [1], "the photo: page-classification reads the page"


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
    for _number, block in tables:
        rows = block["cellGrid"]["rows"]
        assert rows and all(row["cells"] for row in rows)
    routing = docproc["metadata"]["routing"]["pages"]
    assert [r["route"] for r in routing] == ["text-layer"] * len(routing), (
        "a table beside running text"
    )
    document, _ = d2j.prepare(str(_write(tmp_path, "rich.docx")))
    assert d2j.graphics_pages(document) == [], "a text box is text, not a graphic"


@needs_libreoffice
def test_the_paradata_name_the_rendered_pdf_stored_when_it_is_read_and_written(tmp_path, capsys):
    """Decision 5 with R5: the rendered PDF is read (by the ATR: page 2 is forced), written with
    --rendered-out, and so named in `result`, stored, by the SHA-512 of the written file. The
    docproc is not the stored one: the ATR's is (H14)."""
    rendered = tmp_path / "r.pdf"
    graphics = tmp_path / "g.json"
    _docproc(
        tmp_path,
        "scan_page.docx",
        rendered_out=str(rendered),
        graphics_pages_out=str(graphics),
        paradata_dir=str(tmp_path / "para"),
    )
    paradata = json.loads(next((tmp_path / "para").glob("*.json")).read_text(encoding="utf-8"))
    outputs = {o["name"]: o for o in paradata["outputs"]}
    assert outputs[d2j.RENDERED_DISTRIBUTION] == {
        "name": d2j.RENDERED_DISTRIBUTION,
        "sha512": hashlib.sha512(rendered.read_bytes()).hexdigest(),
        "media_type": "application/pdf",
        "stored": True,
    }
    assert outputs["atr/docproc-json"]["stored"] is False, (
        "page 2 needs the ATR: its docproc is stored"
    )
    assert json.loads(graphics.read_text(encoding="utf-8")) == {"graphics_pages": [2]}
    assert "graphics_pages" not in json.dumps(paradata), "a hand-over, not paradata"
    assert paradata["parameters"]["render"].startswith("pdf:writer_pdf_Export:")
    assert paradata["parameters"]["graphic_min_side_pt"] == 24.0
    libreoffice = next(c for c in paradata["components"] if c["name"] == "libreoffice")
    assert libreoffice.get("version"), "the component with its version, as a fact"
    assert not {"license", "license_url", "license_detail"} & set(paradata)


@needs_libreoffice
def test_a_read_rendered_pdf_not_written_is_not_named(tmp_path, capsys):
    """R5: naming follows writing. Without --rendered-out the PDF the ATR needs is not written, so
    it is not named, and the command line says what to pass."""
    _docproc(tmp_path, "scan_page.docx", paradata_dir=str(tmp_path / "para"))
    paradata = json.loads(next((tmp_path / "para").glob("*.json")).read_text(encoding="utf-8"))
    assert d2j.RENDERED_DISTRIBUTION not in {o["name"] for o in paradata["outputs"]}
    assert "pass --rendered-out" in capsys.readouterr().err


@needs_libreoffice
def test_a_rendered_pdf_nothing_reads_is_neither_written_nor_named(tmp_path, capsys):
    """Decision 5: rich.docx has running text on every page and shows no graphics, so neither
    the ATR nor page-classification reads its rendering: --rendered-out writes nothing."""
    rendered = tmp_path / "r.pdf"
    _docproc(tmp_path, "rich.docx", rendered_out=str(rendered), paradata_dir=str(tmp_path / "para"))
    assert not rendered.exists()
    assert "nothing reads the rendered PDF" in capsys.readouterr().err
    paradata = json.loads(next((tmp_path / "para").glob("*.json")).read_text(encoding="utf-8"))
    assert d2j.RENDERED_DISTRIBUTION not in {o["name"] for o in paradata["outputs"]}
    assert paradata["parameters"]["render"].startswith("pdf:writer_pdf_Export:"), "still rendered"


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
    assert "rendered_pdf" not in body and body["graphics_pages"] == []
    assert body["docproc"]["sourceFormat"] == "csv"


def test_reformat_reports_a_wrapped_line_in_limits_applied(client):
    files = {"file": ("dlouhy.txt", ("slovo " * 400).encode("utf-8"), "text/plain")}
    body = client.post("/reformat", files=files).json()
    assert [note["limit"] for note in body["limits_applied"]] == ["max_line_chars"]
    assert body["limits_applied"][0]["effect"] == "split"


@needs_libreoffice
def test_reformat_hands_over_the_rendered_pdf_the_atr_reads(client):
    """Decision 5: page 2 of scan_page.docx is forced, so the ATR reads the document as its
    rendered PDF: handed over, and named in `result`, stored, by the same SHA-512."""
    body = _reformat(client, "scan_page.docx")
    assert [p["pageNumber"] for p in body["docproc"]["pages"] if p.get("needsAtr")] == [2]
    assert body["graphics_pages"] == [2]
    data = base64.b64decode(body["rendered_pdf"]["data"])
    assert data.startswith(b"%PDF") and body["rendered_pdf"]["media_type"] == "application/pdf"
    assert (
        hashlib.sha512(data).hexdigest()
        == body["rendered_pdf"]["sha512"]
        == body["reader"]["rendering"]["sha512"]
    )
    result = {r["name"]: r for r in body["paradata"]["result"]}
    assert result[d2j.RENDERED_DISTRIBUTION]["sha512"] == body["rendered_pdf"]["sha512"]
    assert "stored" not in result[d2j.RENDERED_DISTRIBUTION], "a stored distribution"
    assert result["atr/docproc-json"]["stored"] is False, "the ATR's docproc is the stored one"


@needs_libreoffice
def test_reformat_hands_over_the_rendered_pdf_only_when_it_is_read(client):
    """Decision 5: photo_text.docx keeps its text, but its photo is a graphic, so
    page-classification reads its page from the rendered PDF; rich.docx shows nothing for either
    step, so its rendering is neither handed over nor named (the `rendered` field is gone)."""
    photo = _reformat(client, "photo_text.docx")
    assert photo["graphics_pages"] == [1] and photo["rendered_pdf"]["data"]
    assert not any(p.get("needsAtr") for p in photo["docproc"]["pages"])
    rich = _reformat(client, "rich.docx", rendered="true")
    assert rich["graphics_pages"] == [] and "rendered_pdf" not in rich
    assert d2j.RENDERED_DISTRIBUTION not in {r["name"] for r in rich["paradata"]["result"]}
