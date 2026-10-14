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
    return sr.page_signals(path.stem, path, record, {}, RULE)


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
    assert sr.reason_kind("no text layer under the page furniture: images cover 100%…") == (
        "page number over a scan"
    )
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
    assert env["N_DOCS"] == "2" and env["UPLOAD_MB"] == "200" and env["MAX_DOC_PAGES"] == "2"

    cli = run / sr.CLI
    cli.mkdir()
    record = d2j.build_record(str(samples / "CTX1.pdf"))
    (cli / "CTX1.document.json").write_text(json.dumps(record), encoding="utf-8")
    (cli / "CTX1.run.json").write_text('{"rc": 0, "wall_s": 1.5, "peak_rss_mb": 60.2}')
    (cli / "CTX2.run.json").write_text('{"rc": 3, "wall_s": 0.4, "peak_rss_mb": 40.0}')
    (cli / "CTX2.err").write_text("[digital-convert] ocr_text_layer: 2 of 2 text-bearing pages…\n")
    docling = run / sr.DOCLING
    docling.mkdir()
    (docling / "CTX1.run.json").write_text('{"rc": 2, "wall_s": 9.0, "peak_rss_mb": 900.0}')
    (docling / "CTX1.err").write_text(
        "[digital-convert] Docling could not convert CTX1.pdf (OSError: no layout model). Its…\n"
    )

    summary = sr.build_report(run, samples, thumbnails=True)
    with (run / "report" / "summary.csv").open(encoding="utf-8") as handle:
        rows = {row["doc"]: row for row in csv.DictReader(handle)}
    assert rows["CTX1"]["tl_digital"] == "2" and rows["CTX1"]["candidates"] == "1"
    assert (rows["CTX2"]["cli_rc"], rows["CTX2"]["cli_reason"]) == ("3", "ocr_text_layer")
    assert rows["CTX1"]["reformat_http"] == ""  # a stage that did not run stays empty

    text = summary.read_text(encoding="utf-8")
    assert rows["CTX1"]["docling_reason"].startswith("Docling could not convert CTX1.pdf (OSError")
    assert "**Docling wrote no record for 1 document(s):** Docling could not convert" in text
    assert "## Text defects (command line, light engine)" in text
    assert (rows["CTX1"]["split_letters"], rows["CTX1"]["unmapped_glyphs"]) == ("0", "0")
    assert "1 of 2 `digital` pages" in text
    candidates = (run / "report" / "candidates.md").read_text(encoding="utf-8")
    assert "![](thumbs/CTX1_p1.png)" in candidates
    assert (run / "report" / "thumbs" / "CTX1_p1.png").is_file()
    assert "| digital-convert | MAX_UPLOAD_MB | 200 | 200 |" in (
        run / "report" / "limits.md"
    ).read_text(encoding="utf-8")
    delivered = run / "report" / "deliver"
    assert json.loads((delivered / "CTX1.document.json").read_text())["doc_id"] == record["doc_id"]
    assert not (delivered / "CTX2.document.json").exists()


def test_text_defects_count_split_letters_unmapped_glyphs_and_short_lines():
    """#5's signals: a lone ě/č/ř/ů token is a split word, U+FFFD and (cid:N) are unmapped."""
    lines = [{"text": "Ji č ín a Oprávn ě ná"}, {"text": "poh(cid:3)ební poh\ufffdební"}]
    assert sr.text_defects(lines) == {
        "split_letters": 2,
        "unmapped_glyphs": 2,
        "short_line_share": "",
    }
    rotated = [{"text": c} for c in "Obr.18Turnov"]
    assert sr.text_defects(rotated)["short_line_share"] == 1.0


def test_a_docling_failure_is_named_not_called_a_missing_dependency(tmp_path):
    err = tmp_path / "x.err"
    err.write_text("[digital-convert] Docling could not convert X.pdf (RuntimeError: CUDA)\n")
    assert sr.cli_reason(err, {"rc": 2}) == "Docling could not convert X.pdf (RuntimeError: CUDA)"
    err.write_text("[digital-convert] limit_exceeded: The PDF has 529 pages\n")
    assert sr.cli_reason(err, {"rc": 4}) == "limit_exceeded"
    err.write_text("")
    assert sr.cli_reason(err, {"rc": 2}) == "dependency_missing"


# ── the re-run of #5: one column per stage run, the shipped furniture rule, the quality model ──


def _describe_page(route, reason, category=None, quality=None):
    page = {"page": "1", "page_index": 1, "route": route, "route_reason": reason}
    if category:
        page["category"] = {"label": category, "confidence": 0.9}
    if quality:
        page["quality"] = quality
    return {"pages": [page]}


def test_each_stage_column_comes_from_its_own_run(tmp_path):
    """The 10-14 report paired stage 62's page type with stage 61's route; never again."""
    path = tmp_path / "minimal.pdf"
    path.write_bytes(mf.minimal_pdf())
    record = d2j.build_record(str(path))
    quality = {"source": "ocr-postprocess", "band": "Trash", "lines_by_category": {"Trash": 3}}
    reports = {
        "none": _describe_page("nlp", "trustworthy text layer (decode check passed; …)"),
        "stages": _describe_page(
            "ocr", "the text layer decodes, but the quality model calls 100% …", "TEXT", quality
        ),
        "all": _describe_page("none", "no text layer; page type DRAW is graphical …", "DRAW"),
    }
    first = sr.page_signals("minimal", path, record, reports, RULE)[0]
    assert (first["none_route"], first["stages_route"], first["all_route"]) == (
        "nlp",
        "ocr",
        "none",
    )
    assert (first["stages_category"], first["all_category"]) == ("TEXT", "DRAW")
    assert first["stages_route_kind"] == "quality model"
    assert (first["stages_judged"], first["stages_trash"]) == (3, 3)
    assert first["stages_prose_judged"] == 0, "no record lines in the answer: no prose counted"
    assert first["prose_rule"] == 0
    assert (first["words"], first["damaged_words"]) == (18, 0)


def test_the_furniture_rule_s_pages_and_its_near_misses_are_listed(tmp_path):
    rows = _signals(tmp_path, "stamped_scan.pdf", mf.stamped_scan_pdf())
    assert [(r["text_layer"], r["furniture_only"]) for r in rows] == [
        ("none", 1),
        ("digital", 0),
        ("digital", 1),
    ]
    text = sr.candidates_md(rows, RULE)
    assert "**Flagged by that rule:** 1 page(s)." in text
    assert "**Near misses, only furniture under the threshold:** 1 `digital` page(s)." in text
    assert "no rule is shipped" not in text


def test_the_quality_model_section_counts_what_it_moved_and_why():
    """Table cells read as Trash moved the page; the prose rule would not have."""
    lines = [
        {"page": "1", "line": n, "text": t, "group_id": g, "categ": c}
        for n, (t, g, c) in enumerate(
            [("Vrstva", "tbl0-r0c0", "Trash"), ("30 cm", "tbl0-r0c1", "Trash"),
             ("Ornice", "tbl0-r1c0", "Trash"), ("12", "p1-0", "Trash"),
             *[("Výsledky záchranného archeologického výzkumu", "p1-1", "Clear")] * 5]
        )
    ]  # fmt: skip
    stages = {"document_json": {"lines": lines}}
    signals = [
        {"page": "1", "text_layer": "digital", "stages_band": "Trash",
         "stages_route_kind": "quality model", "all_category": "LINE_P", "prose_rule": 0},
        {"page": "2", "text_layer": "none", "stages_route_kind": "page type"},
    ]  # fmt: skip
    columns = sr.quality_columns(stages, signals)
    assert (columns["qm_digital"], columns["qm_moved"], columns["qm_prose_moves"]) == (1, 1, 0)
    assert columns["qm_moved_categories"] == "LINE_P 1"
    assert (columns["qm_trash_table"], columns["qm_trash_short"], columns["qm_trash_prose"]) == (
        3,
        1,
        0,
    )
    assert sr.quality_columns(None, signals) == {}
    assert sr.route_kind("the text layer decodes, but 2 of its 10 words (20%) lose a letter …") == (
        "lost letters"
    )


def test_limits_take_the_new_stage_timeout_and_report_docling(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / sr.INVENTORY).mkdir(parents=True)
    (run_dir / sr.INVENTORY / "inventory.tsv").write_text(
        "doc\tfile\tmb\tpages\tmax_page_px\nCTX1\tCTX1.pdf\t5.0\t20\t1000\n", encoding="utf-8"
    )
    (run_dir / "env.txt").write_text("gpu: NVIDIA A40, 46068 MiB\n", encoding="utf-8")
    rows = [
        {"doc": "CTX1", "mb": 5.0, "docling_rc": 0, "docling_s": 30.5, "docling_peak_mb": 2182.3}
    ]
    text = sr.limits_md(sr.Run(run_dir), rows)
    assert "| digital-convert | STAGE_TIMEOUT_S | 360 |" in text
    assert "**Docling (`--engine docling`, stage 20).** 1 of 1 documents converted" in text
    assert "2.2–2.2 GB (GPU: NVIDIA A40, 46068 MiB)" in text
