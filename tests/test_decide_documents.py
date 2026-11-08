"""tests/test_decide_documents.py — the document type of PDF pages and documents (#8; atrium-project#83).

No model or network: ``decision_client.FakeSystemOne`` answers on 127.0.0.1. The PDFs are written
here: two pages, each with one line of text in its text layer, rendered by pypdfium2.
"""

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")

import decision_client as dc  # noqa: E402
from tools import decide_documents as dd  # noqa: E402

_PAGE = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents {c} 0 R /Resources << /Font << /F1 9 0 R >> >> >>"


def _pdf(path: Path, lines) -> Path:
    """A minimal PDF, one page per line of text (Helvetica); pdfium rebuilds the missing xref."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(lines)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(lines)} >>")
    for i, line in enumerate(lines):
        stream = f"BT /F1 18 Tf 30 350 Td ({line}) Tj ET"
        objects.append(_PAGE.format(c=4 + 2 * i))
        objects.append(f"<< /Length {len(stream)} >> stream\n{stream}\nendstream")
    while len(objects) < 8:
        objects.append("<< >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    body = "".join(f"{n} 0 obj {obj} endobj\n" for n, obj in enumerate(objects, start=1))
    path.write_bytes(f"%PDF-1.4\n{body}trailer << /Root 1 0 R >>\n%%EOF\n".encode("latin-1"))
    return path


def _client(fake):
    return dc.DecisionClient(fake.url, "fake-model", timeout_s=20, retries=0)


def _read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture()
def pdfs(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    _pdf(folder / "CTX1.pdf", ["Nalezova zprava", "Priloha 1"])
    _pdf(folder / "CTX2.pdf", ["Plan sondy"])
    return folder


def test_the_view_is_the_pdf_page_view_of_the_list():
    view = dd.load_view()
    assert list(view["questions"]) == ["group", "document_type"]
    assert len(view["questions"]["document_type"]["criteria"]) == 32
    assert view["inputs"]["image"].startswith("the rendered page")
    assert "off by default" in view["inputs"]["text"]


def test_render_pages_gives_images_and_the_text_layer_only_when_asked(pdfs):
    plain = list(dd.render_pages(pdfs / "CTX1.pdf", max_side=200))
    assert [p["page"] for p in plain] == [1, 2] and all(p["text"] is None for p in plain)
    assert max(plain[0]["image"].size) == 200
    texts = list(dd.render_pages(pdfs / "CTX1.pdf", max_side=200, with_text=True))
    assert [p["text"] for p in texts] == ["Nalezova zprava", "Priloha 1"]
    assert list(dd.render_pages(pdfs / "CTX1.pdf", with_text=True, max_chars=5))[0]["text"] == ""


def test_by_default_a_request_carries_the_image_and_no_text(pdfs, tmp_path):
    view = dd.load_view()
    with dc.FakeSystemOne() as fake:
        new = dd.run(_client(fake), dd.input_pdfs(pdfs), tmp_path / "out", view, max_side=200)
        requests = fake.requests
    # two pages of CTX1, one of CTX2, then one document request each
    assert len(new["page"]) == 3 and len(new["document"]) == 2 and len(requests) == 5
    for request in requests:
        assert "text" not in request["state"] and request["questions"] == view["questions"]
    images = sorted(len(r["images"]) for r in requests)
    assert images == [1, 1, 1, 1, 2]  # CTX1's document request carries its two pages
    pages = _read(tmp_path / "out" / "DECIDE_PAGES.csv")
    assert list(pages[0]) == dd.columns(view, "page")
    assert {r["with_text"] for r in pages} == {"0"} and {r["chars"] for r in pages} == {""}
    assert all(r["status"] == "ok" and r["document_type"] for r in pages)
    assert all(r["schema_digest"] == view["schema_digest"] for r in pages)


def test_with_text_adds_the_text_layer(pdfs, tmp_path):
    view = dd.load_view()
    with dc.FakeSystemOne() as fake:
        dd.run(
            _client(fake), dd.input_pdfs(pdfs), tmp_path / "out", view, with_text=True, max_side=200
        )
        states = [r["state"] for r in fake.requests]
    assert states[0]["text"] == "Nalezova zprava"
    assert states[2]["text"].startswith("[page 1]\nNalezova zprava")  # CTX1's document request
    pages = _read(tmp_path / "out" / "DECIDE_PAGES.csv")
    assert {r["with_text"] for r in pages} == {"1"} and pages[0]["chars"] == str(
        len("Nalezova zprava")
    )


def test_a_document_request_holds_at_most_four_pages(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    _pdf(folder / "LONG.pdf", [f"Strana {i}" for i in range(6)])
    with dc.FakeSystemOne() as fake:
        new = dd.run(
            _client(fake),
            [folder / "LONG.pdf"],
            tmp_path / "out",
            dd.load_view(),
            level="document",
            doc_pages=9,
        )
        requests = fake.requests
    assert len(requests) == 1 and len(requests[0]["images"]) == dc.MAX_IMAGES
    (row,) = new["document"]
    assert row["pages"] == 6 and row["pages_sent"] == 4
    assert "first 4 of the 6 pages" in requests[0]["state"]["item"]


def test_gold_scores_documents_and_pages(pdfs, tmp_path):
    view = dd.load_view()
    report = next(t for t in view["types"] if t["id"] == "fieldwork_report")
    gold = tmp_path / "gold.csv"
    gold.write_text(
        f"file,document_type\nCTX1.pdf,{report['hes']}\nCTX2,plan_trench\n", encoding="utf-8"
    )
    page_gold = tmp_path / "page_gold.csv"
    page_gold.write_text(f"file,page,document_type\nCTX1,2,{report['cs']}\n", encoding="utf-8")
    documents = dd.read_gold(gold, view["types"])
    assert documents == {("CTX1",): "fieldwork_report", ("CTX2",): "plan_trench"}
    pages = dd.read_gold(page_gold, view["types"], pages=True)
    with dc.FakeSystemOne() as fake:
        dd.run(
            _client(fake),
            dd.input_pdfs(pdfs),
            tmp_path / "out",
            view,
            gold=documents,
            page_gold=pages,
        )
    docs = _read(tmp_path / "out" / "DECIDE_DOCS.csv")
    assert {r["file"]: r["gold_group"] for r in docs} == {"CTX1": "text", "CTX2": "plan_or_map"}
    summary = dd.summarise(docs, view["abstain"])
    assert summary["gold"]["rows"] == 2 and 0 <= summary["gold"]["type_accuracy"] <= 1
    page_rows = _read(tmp_path / "out" / "DECIDE_PAGES.csv")
    assert [r["gold_type"] for r in page_rows] == ["", "fieldwork_report", ""]
    bad = tmp_path / "bad.csv"
    bad.write_text("file,document_type\nCTX1,no such type\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        dd.read_gold(bad, view["types"])


def test_resume_skips_what_is_done_and_a_broken_pdf_is_rejected(pdfs, tmp_path):
    (pdfs / "BROKEN.pdf").write_bytes(b"not a pdf")
    view = dd.load_view()
    out = tmp_path / "out"
    with dc.FakeSystemOne() as fake:
        dd.run(_client(fake), dd.input_pdfs(pdfs), out, view)
        with pytest.raises(SystemExit):
            dd.run(_client(fake), dd.input_pdfs(pdfs), out, view)
        before = len(fake.requests)
        again = dd.run(_client(fake), dd.input_pdfs(pdfs), out, view, resume=True)
        assert len(fake.requests) == before and again == {"page": [], "document": []}
    broken = [r for r in _read(out / "DECIDE_DOCS.csv") if r["file"] == "BROKEN"]
    assert (
        len(broken) == 1
        and broken[0]["status"] == "rejected"
        and broken[0]["error"].startswith("render:")
    )


def test_main_end_to_end_names_the_server_by_kind(pdfs, tmp_path):
    out = tmp_path / "run"
    with dc.FakeSystemOne() as fake:
        code = dd.main(
            ["--input", str(pdfs), "--out-dir", str(out), "--url", fake.url, "--model", "m"]
        )
    assert code == 0
    summary = json.loads((out / "decide_documents.summary.json").read_text(encoding="utf-8"))
    assert summary["served_on"] == "local" and summary["with_text"] is False
    assert summary["pages"]["rows"] == 3 and summary["documents"]["rows"] == 2
    assert fake.url not in json.dumps(summary)
    records = list((out / "paradata").glob("*.json"))
    assert records and "decision_model" in records[0].read_text(encoding="utf-8")


def test_main_refuses_an_unconfigured_server(pdfs, monkeypatch):
    monkeypatch.delenv("DECISION_MODEL_URL", raising=False)
    with pytest.raises(SystemExit):
        dd.main(["--input", str(pdfs), "--out-dir", "x", "--url", "", "--model", "m"])


@pytest.mark.parametrize(
    "flags, message",
    [
        (["--accept-at", "0.9"], "--accept-at and --review-below go together"),
        (["--accept-at", "1.5", "--review-below", "0.5"], "--accept-at is a probability"),
        (["--accept-at", "0.9", "--review-below", "-0.1"], "--review-below is a probability"),
        (["--accept-at", "0.5", "--review-below", "0.8"], "must not exceed --accept-at"),
        (["--doc-pages", "0"], "--doc-pages is 1 to 4"),
        (["--doc-pages", "5"], "--doc-pages is 1 to 4"),
    ],
)
def test_main_refuses_bad_thresholds_and_doc_pages_before_any_request(
    pdfs, tmp_path, capsys, flags, message
):
    # lane() would raise only at the first answer, after a paid request; a clamped --doc-pages
    # would leave the paradata recording a value the run did not use
    out = tmp_path / "run"
    with dc.FakeSystemOne() as fake:
        with pytest.raises(SystemExit) as refused:
            dd.main(
                ["--input", str(pdfs), "--out-dir", str(out), "--url", fake.url, "--model", "m"]
                + flags
            )
        assert not fake.requests
    assert refused.value.code == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


def test_main_takes_thresholds_in_range_and_records_the_doc_pages_it_used(pdfs, tmp_path):
    out = tmp_path / "run"
    with dc.FakeSystemOne() as fake:
        code = dd.main(
            ["--input", str(pdfs), "--out-dir", str(out), "--url", fake.url, "--model", "m"]
            + ["--level", "document", "--doc-pages", "1", "--accept-at", "1", "--review-below", "1"]
        )
    assert code == 0
    docs = _read(out / "DECIDE_DOCS.csv")
    assert {r["pages_sent"] for r in docs} == {"1"}
    assert {r["lane"] for r in docs} <= {"accept", "review"}  # equal thresholds: no escalate lane
    (record,) = (out / "paradata").glob("*.json")
    assert json.loads(record.read_text(encoding="utf-8"))["config"]["doc_pages"] == 1


def _data_uri(image, style):
    """The data URI inside one image as *style* puts it on the wire."""
    if style == "base64":
        assert not image.startswith("data:")
        return "data:image/jpeg;base64," + image
    if style == "url":
        return image["url"]
    if style == "image_url":
        assert image["type"] == "image_url"
        return image["image_url"]["url"]
    return image


@pytest.mark.parametrize("style", dc.IMAGE_STYLES)
def test_image_style_sets_how_the_images_travel(pdfs, tmp_path, style):
    with dc.FakeSystemOne() as fake:
        code = dd.main(
            ["--input", str(pdfs), "--out-dir", str(tmp_path / "run"), "--url", fake.url]
            + ["--model", "m", "--level", "page", "--max-side", "100", "--image-style", style]
        )
        images = [image for request in fake.requests for image in request["images"]]
    assert code == 0 and len(images) == 3
    for image in images:
        uri = _data_uri(image, style)
        assert uri.startswith("data:image/jpeg;base64,")
        assert dc.image_on_wire(uri, style) == image


def test_image_style_refuses_a_style_it_does_not_know(pdfs, capsys):
    with pytest.raises(SystemExit) as refused:
        dd.main(["--input", str(pdfs), "--out-dir", "x", "--model", "m", "--image-style", "png"])
    assert refused.value.code == 2 and "--image-style" in capsys.readouterr().err
