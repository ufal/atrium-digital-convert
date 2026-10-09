"""tests/test_samples_report.py — the measurement tool of the #5 sample run (`tools/samples_report.py`).

The tool reads real PDFs on the cluster; here it reads the generated fixtures and two PDFs built
for the case #5 has to measure: a page that is a full-page image with one stamp line on it
(`digital`, unflagged today) next to an ordinary text page.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")
pdfium = pytest.importorskip("pypdfium2")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))
sys.path.insert(0, str(REPO_ROOT / "tools"))
import make_fixtures as mf  # noqa: E402
import samples_report as sr  # noqa: E402

from api_util import digital_to_json as d2j  # noqa: E402

RULE = (sr.COVERAGE_MIN, sr.BODY_CHARS_MAX)


def _stamp_pdf() -> bytes:
    """Page 1: a scan (one image over the whole page) with a stamp line. Page 2: text."""
    stamp = mf.FULL_PAGE_IMAGE + b"\n" + mf._text_at(400, 770, [b"AMCR CTX000000007"], size=8)
    text = mf._text_at(72, 720, [b"Zprava o vyzkumu, textova strana.", b"Druhy radek."])
    return mf._build_pdf_ex([stamp, text])


def _signals(tmp_path: Path, name: str, data: bytes) -> list:
    path = tmp_path / name
    path.write_bytes(data)
    record = d2j.build_record(str(path))
    return sr.page_signals(path.stem, path, record, {}, {}, RULE)


def test_the_union_of_overlapping_boxes_is_measured_once_and_clipped_to_the_page():
    frame = (0.0, 0.0, 100.0, 100.0)
    assert sr.union_share([(0, 0, 50, 50), (25, 25, 75, 75)], frame) == pytest.approx(
        0.4375, abs=0.01
    )
    assert sr.union_share([(90, 90, 200, 200)], frame) == pytest.approx(0.01, abs=0.002)
    assert sr.union_share([], frame) == 0.0


def test_an_image_inside_nested_forms_is_placed_on_the_page():
    """PDFium gives a nested object's box in its form's space; the containers carry it out."""
    inner = mf._stream_obj(
        b"q 400 0 0 300 0 0 cm /Im1 Do Q",
        b"/Type /XObject /Subtype /Form /BBox [0 0 2000 2000] /Matrix [2 0 0 2 10 20] "
        b"/Resources << /XObject << /Im1 6 0 R >> >>",
    )
    outer = mf._stream_obj(
        b"q 1 0 0 1 50 50 cm /Fm2 Do Q",
        b"/Type /XObject /Subtype /Form /BBox [0 0 2000 2000] /Matrix [1 0 0 1 7 9] "
        b"/Resources << /XObject << /Fm2 7 0 R >> >>",
    )
    pdf = pdfium.PdfDocument(
        mf._serialize_pdf(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>",
                4: mf._stream_obj(b"q 0.5 0 0 0.5 100 100 cm /Fm1 Do Q"),
                5: outer,
                6: mf.IMAGE_XOBJECT,
                7: inner,
            }
        )
    )
    page = pdf[0]
    try:
        assert [tuple(round(v, 1) for v in box) for box in sr.image_boxes(page)] == [
            (133.5, 139.5, 533.5, 439.5)
        ]
    finally:
        page.close()
        pdf.close()


def test_a_stamp_over_a_full_page_image_is_a_candidate_and_a_text_page_is_not(tmp_path):
    scan, text = _signals(tmp_path, "stamp.pdf", _stamp_pdf())
    assert scan["text_layer"] == "digital" and not scan["needs_ocr"]
    assert scan["images"] == 1 and scan["image_coverage"] == pytest.approx(1.0)
    assert scan["body_chars"] == len("AMCR CTX000000007")
    assert scan["candidate"] == 1
    assert (text["images"], text["image_coverage"], text["candidate"]) == (0, 0.0, 0)


def test_a_page_already_flagged_is_not_a_candidate(tmp_path):
    rows = _signals(tmp_path, "image_only.pdf", mf.image_only_pdf())
    scan = rows[1]
    assert scan["text_layer"] == "none" and scan["needs_ocr"] == 1
    assert scan["image_coverage"] == pytest.approx(1.0)
    assert scan["candidate"] == 0


def test_running_header_and_footer_characters_are_not_body(tmp_path):
    first, _ = _signals(tmp_path, "two_column.pdf", mf.two_column_pdf())
    assert first["furniture_chars"] > 0
    assert first["body_chars"] > first["furniture_chars"]
    assert 0 < first["text_area_share"] < 1


def test_measure_records_the_exit_code_time_and_peak(tmp_path):
    out = tmp_path / "run.json"
    assert sr.measure([sys.executable, "-c", "import sys; sys.exit(3)"], out) == 3
    result = json.loads(out.read_text())
    assert result["rc"] == 3 and not result["timed_out"]
    assert result["wall_s"] >= 0 and result["peak_rss_mb"] > 0
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert sr.measure(slow, out, timeout=0.5) == 124
    assert json.loads(out.read_text())["timed_out"] is True


def test_reason_kinds_and_page_ranges():
    assert sr.reason_kind("no extractable text layer: the page draws 2 image(s) but…") == (
        "no text, draws images"
    )
    assert sr.reason_kind("no extractable text layer: the page draws nothing a parser…") == (
        "no text, draws nothing"
    )
    assert sr.reason_kind("embedded text layer does not decode: 3 of 3 lines…") == "mojibake"
    assert sr.reason_kind("the text layer is a prior OCR run: 4 of 4…") == "prior OCR layer"
    assert sr._ranges([7, 1, 2, 3, 9, 10]) == "1-3,7,9-10"


def test_parity_names_the_first_difference(tmp_path):
    path = tmp_path / "minimal.pdf"
    path.write_bytes(mf.minimal_pdf())
    record = d2j.build_record(str(path))
    assert sr.parity(record, json.loads(json.dumps(record))) == "same"
    changed = json.loads(json.dumps(record))
    changed["lines"][1]["text"] = "something else"
    assert sr.parity(record, changed) == "lines[1] differs"


def test_inventory_and_report_on_a_small_run(tmp_path):
    samples, run = tmp_path / "samples", tmp_path / "run"
    samples.mkdir()
    (samples / "CTX1.pdf").write_bytes(_stamp_pdf())
    (samples / "CTX2.pdf").write_bytes(mf.ocr_layer_pdf())
    inventory = run / sr.INVENTORY / "inventory.tsv"
    assert sr.main(["inventory", "--samples", str(samples), "--out", str(inventory)]) == 0
    env = dict(line.split("=", 1) for line in inventory.with_suffix(".env").read_text().split())
    assert env["N_DOCS"] == "2" and env["UPLOAD_MB"] == "50" and env["MAX_DOC_PAGES"] == "2"

    cli = run / sr.CLI
    cli.mkdir()
    record = d2j.build_record(str(samples / "CTX1.pdf"))
    (cli / "CTX1.document.json").write_text(json.dumps(record), encoding="utf-8")
    (cli / "CTX1.run.json").write_text('{"rc": 0, "wall_s": 1.5, "peak_rss_mb": 60.2}')
    (cli / "CTX2.run.json").write_text('{"rc": 3, "wall_s": 0.4, "peak_rss_mb": 40.0}')
    (cli / "CTX2.err").write_text("[digital-convert] ocr_text_layer: 2 of 2 text-bearing pages…\n")

    summary = sr.build_report(run, samples, thumbnails=True)
    with (run / "report" / "summary.csv").open(encoding="utf-8") as handle:
        rows = {row["doc"]: row for row in csv.DictReader(handle)}
    assert rows["CTX1"]["tl_digital"] == "2" and rows["CTX1"]["candidates"] == "1"
    assert (rows["CTX2"]["cli_rc"], rows["CTX2"]["cli_reason"]) == ("3", "ocr_text_layer")
    assert rows["CTX1"]["reformat_http"] == ""  # a stage that did not run stays empty

    text = summary.read_text(encoding="utf-8")
    assert "## Light engine / Docling\n\nNot run." in text
    assert "1 of 2 `digital` pages" in text
    candidates = (run / "report" / "candidates.md").read_text(encoding="utf-8")
    assert "![](thumbs/CTX1_p1.png)" in candidates
    assert (run / "report" / "thumbs" / "CTX1_p1.png").is_file()
    assert "| digital-convert | MAX_UPLOAD_MB | 50 | 50 |" in (
        run / "report" / "limits.md"
    ).read_text(encoding="utf-8")
    delivered = run / "report" / "deliver"
    assert json.loads((delivered / "CTX1.document.json").read_text())["doc_id"] == record["doc_id"]
    assert not (delivered / "CTX2.document.json").exists()
