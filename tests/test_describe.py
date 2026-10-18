"""tests/test_describe.py — `POST /describe` and its adjacent stages (atrium-digital-convert#2).

The stages run over real HTTP: `tools/stage_stub.py` (the stand-in for page-classification and
ocr-postprocess) is served by uvicorn on a free local port, so the client code, the multipart
shapes, the guard and the record accretion are all the real ones. The failure modes the stub
cannot produce — a refused connection, a timeout, a 500, an older server — are driven by
monkeypatching `requests`, which is all `service/stages.py` talks through.

What must hold whatever the stages do: `/describe` answers 200, the record stays the converter's
where a stage misbehaves, and `stages[]` says what happened.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pdfplumber")
pytest.importorskip("jsonschema")
uvicorn = pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))
sys.path.insert(0, str(REPO_ROOT / "tools"))
import make_fixtures  # noqa: E402
import stage_stub  # noqa: E402

from service import api, stages  # noqa: E402

client = TestClient(api.app)
FIXTURES = make_fixtures.build_all()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def stub_url():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(stage_stub.app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the stage stub did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def both_stages(monkeypatch, stub_url):
    monkeypatch.setenv("PAGE_CLASSIFICATION_URL", stub_url)
    monkeypatch.setenv("OCR_POSTPROCESS_URL", stub_url)
    return stub_url


@pytest.fixture
def no_stages(monkeypatch):
    monkeypatch.delenv("PAGE_CLASSIFICATION_URL", raising=False)
    monkeypatch.delenv("OCR_POSTPROCESS_URL", raising=False)


def _describe(name, data=None, **form):
    files = {
        "file": (name, data if data is not None else FIXTURES[name], "application/octet-stream")
    }
    response = client.post("/describe", files=files, data=form)
    assert response.status_code == 200, response.text
    return response.json()


def _status(body):
    return {s["stage"]: s["status"] for s in body["stages"]}


# ── with the stages running ─────────────────────────────────────────────────────────────────


def test_a_garbled_page_is_classified_and_routed_by_its_category(both_stages, monkeypatch):
    monkeypatch.setenv("STUB_NEEDS_OCR_CATEGORY", "TEXT_HW")
    body = _describe("garbled.pdf")
    [page] = body["pages"]
    assert page["text_layer"] == "garbled" and page["needs_ocr"] is True
    assert (
        page["category"]["label"] == "TEXT_HW"
        and page["category"]["source"] == "page-classification"
    )
    assert page["route"] == "htr", page["route_reason"]
    stage = {s["stage"]: s for s in body["stages"]}["page-classification"]
    assert stage["status"] == "ok" and stage["record_adopted"] is True and stage["pages"] == [1]
    record = body["document_json"]
    assert record["pages"][0]["category"] == "TEXT_HW"
    assert record["assembled"]["blocks"]["page_categories"]["program"] == "page-classification"
    # every line keeps the converter's decode verdict: nothing for the quality model to score
    scorer = {s["stage"]: s for s in body["stages"]}["ocr-postprocess"]
    assert scorer["status"] == "skipped" and scorer["record_adopted"] is False
    assert "3 line(s) keep the converter's decode verdict" in scorer["detail"]


def test_clean_pages_skip_the_classifier_and_get_the_common_quality_model(both_stages):
    body = _describe("minimal.pdf")
    assert _status(body) == {"page-classification": "skipped", "ocr-postprocess": "ok"}
    for page in body["pages"]:
        assert page["route"] == "nlp"
        assert page["quality"]["source"] == "ocr-postprocess" and page["quality"]["band"] == "Clear"
    record = body["document_json"]
    assert all(row.get("categ") for row in record["lines"]), "the scorer wrote categ on every line"
    assert record["source"]["origin"] == "digital-born-pdf", "scoring never re-originates the plane"


def test_classify_pages_all_asks_about_every_page(both_stages):
    body = _describe("minimal.pdf", classify_pages="all")
    stage = {s["stage"]: s for s in body["stages"]}["page-classification"]
    assert stage["status"] == "ok" and stage["pages"] == [1, 2]
    assert [p["category"]["label"] for p in body["pages"]] == ["TEXT_P", "TEXT_P"]
    assert [p["route"] for p in body["pages"]] == ["nlp", "nlp"], (
        "a clean text layer still goes to NLP"
    )


@pytest.mark.parametrize("category, route", [("DRAW_L", "ocr"), ("PHOTO", "nlp")])
def test_a_page_whose_only_text_is_its_page_number_is_classified_and_routed_by_type(
    both_stages, monkeypatch, category, route
):
    """#5: stamped_scan.pdf's page 3 is a page number over a half-page image, `digital` under
    FURNITURE_ONLY_IMAGE_COVERAGE. Its page type decides whether the image holds text: AMČR's
    excavation plans (DRAW_L) carry their labels and title block in the image."""
    monkeypatch.setenv("STUB_NEEDS_OCR_CATEGORY", "DRAW")
    monkeypatch.setenv("STUB_PAGE_CATEGORY", category)
    body = _describe("stamped_scan.pdf")
    stage = {s["stage"]: s for s in body["stages"]}["page-classification"]
    assert stage["status"] == "ok" and stage["pages"] == [1, 3], "page 2 has body text"
    first, second, third = body["pages"]
    assert (first["text_layer"], first["route"]) == ("none", "none")
    assert (second["category"], second["route"]) == (None, "nlp")
    assert third["text_layer"] == "digital" and third["category"]["label"] == category
    assert third["route"] == route, third["route_reason"]
    assert body["summary"]["reacquire_pages"] == ([3] if route == "ocr" else [])


def test_a_docx_has_no_page_images_to_classify(both_stages):
    body = _describe("rich.docx")
    assert _status(body)["page-classification"] == "skipped"
    assert "DOCX" in {s["stage"]: s for s in body["stages"]}["page-classification"]["detail"]


def test_decode_verdicts_are_not_rescored(both_stages):
    body = _describe("garbled.pdf")
    record = body["document_json"]
    assert {row["categ"] for row in record["lines"]} <= {"Garbage", "Inverted"}


def test_stages_none_keeps_the_assessment_local(both_stages):
    body = _describe("minimal.pdf", stages="none")
    assert _status(body) == {
        "page-classification": "not_requested",
        "ocr-postprocess": "not_requested",
    }
    assert all(page["quality"]["source"] == "digital-convert" for page in body["pages"])


def test_one_stage_can_be_asked_alone(both_stages):
    body = _describe("garbled.pdf", stages="page-classification")
    assert _status(body) == {"page-classification": "ok", "ocr-postprocess": "not_requested"}


def test_include_text_false_leaves_the_text_out(both_stages):
    body = _describe("minimal.pdf", include_text="false", stages="none")
    assert all("text" not in page for page in body["pages"])


# ── when the stages are absent or misbehave ─────────────────────────────────────────────────


def test_unconfigured_stages_are_reported_and_the_converter_answers(no_stages):
    body = _describe("garbled.pdf")
    assert _status(body) == {
        "page-classification": "not_configured",
        "ocr-postprocess": "not_configured",
    }
    [page] = body["pages"]
    assert page["category"] is None and page["route"] == "ocr"
    assert "page type unknown" in page["route_reason"]


def test_a_stage_that_is_down_is_unavailable(monkeypatch):
    port = _free_port()  # nothing listens there
    monkeypatch.setenv("PAGE_CLASSIFICATION_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("OCR_POSTPROCESS_URL", f"http://127.0.0.1:{port}")
    body = _describe("garbled.pdf")
    assert _status(body) == {"page-classification": "unavailable", "ocr-postprocess": "unavailable"}
    assert body["document_json"]["assembled"]["blocks"]["pages"]["program"] == "digital-convert"


class _Reply:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.ok = status < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


def _patch_requests(monkeypatch, post):
    import requests

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Reply(200, {"version": "9.9.9"}))
    monkeypatch.setenv("PAGE_CLASSIFICATION_URL", "http://pc.test")
    monkeypatch.setenv("OCR_POSTPROCESS_URL", "http://op.test")


def test_a_timeout_is_unavailable_and_names_the_setting(monkeypatch):
    import requests

    def post(*_a, **_k):
        raise requests.Timeout("slow")

    _patch_requests(monkeypatch, post)
    body = _describe("garbled.pdf")
    stage = {s["stage"]: s for s in body["stages"]}["page-classification"]
    assert stage["status"] == "unavailable" and "STAGE_TIMEOUT_S" in stage["detail"]


def test_a_500_is_unavailable_and_a_422_is_an_error(monkeypatch):
    def post(url, **_k):
        if "predict_document" in url:
            return _Reply(500, {"status": 500, "reason": None, "detail": "boom"})
        return _Reply(422, {"status": 422, "reason": "invalid_record", "detail": "bad record"})

    _patch_requests(monkeypatch, post)
    body = _describe("garbled.pdf", stages="page-classification")
    pc = {s["stage"]: s for s in body["stages"]}["page-classification"]
    assert pc["status"] == "unavailable" and pc["http_status"] == 500
    body = _describe("minimal.pdf", stages="ocr-postprocess")
    op = {s["stage"]: s for s in body["stages"]}["ocr-postprocess"]
    assert op["status"] == "error" and op["reason"] == "invalid_record"


def test_an_older_page_classification_is_used_for_the_report_but_not_the_record(monkeypatch):
    """It ignores `pages` and keys its record write by physical number ("1", "2", "3"), on a
    record whose pages are labelled otherwise: the guard rejects the write, the report keeps the
    categories of the requested pages."""

    def post(url, files=None, **_k):
        record = json.loads(files["document_json"][1])
        for page in record["pages"]:
            page["page"] = f"physical-{page['page_index']}"  # what a physical-number keying does
        entries = [
            {"page": i, "predictions": [{"label": "TEXT_T", "score": 0.8}]} for i in (1, 2, 3)
        ]
        return _Reply(200, {"type": "document", "pages": entries, "document_json": record})

    _patch_requests(monkeypatch, post)
    body = _describe("image_only.pdf", stages="page-classification")
    stage = body["stages"][0]
    assert stage["status"] == "rejected" and stage["reason"] == "page_key_mismatch"
    assert stage["record_adopted"] is False
    assert "page_categories" not in body["document_json"]
    flagged = [p for p in body["pages"] if p["needs_ocr"]]
    assert flagged and all(p["category"]["label"] == "TEXT_T" for p in flagged)
    assert all(p["category"] is None for p in body["pages"] if not p["needs_ocr"]), (
        "only requested pages count"
    )


def test_an_older_ocr_postprocess_is_asked_page_by_page(monkeypatch):
    calls = []

    def post(url, files=None, data=None, **_k):
        calls.append(url)
        if url.endswith("/score_record"):
            return _Reply(404, {"status": 404, "reason": None, "detail": "Not Found"})
        text = files["file"][1].decode("utf-8")
        lines = [
            {"line_num": n, "text": t, "category": "Clear", "quality_score": 0.9, "lang": "ces"}
            for n, t in enumerate(text.splitlines(), 1)
        ]
        return _Reply(200, {"type": "plain_text", "cleaned_lines": lines})

    _patch_requests(monkeypatch, post)
    body = _describe("minimal.pdf", stages="ocr-postprocess")
    stage = body["stages"][1]
    assert (
        stage["status"] == "ok"
        and "page by page" in stage["detail"]
        and stage["record_adopted"] is False
    )
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["score_record", "process", "process"]
    assert all(
        p["quality"]["source"] == "ocr-postprocess" and p["quality"]["aligned"]
        for p in body["pages"]
    )
    assert [p["quality"]["prose_lines_scored"] for p in body["pages"]] == [3, 1]
    assert body["document_json"]["assembled"]["blocks"]["lines"]["program"] == "digital-convert"


def _score_record_all_trash(url, files=None, data=None, **_k):
    """A `/score_record` that calls every line it is sent Trash (ocr-postprocess's shape)."""
    record = json.loads(files["document_json"][1])
    rows, per_page = [], {}
    for line in record["lines"]:
        line.update(categ="Trash", quality_score=0.1, lang="ces")
        rows.append(
            {"page": str(line["page"]), "line": line["line"], "text": line["text"],
             "category": "Trash", "quality_score": 0.1, "lang": "ces"}
        )  # fmt: skip
        per_page[str(line["page"])] = per_page.get(str(line["page"]), 0) + 1
    pages = [
        {"page": page, "lines_scored": n, "skipped_decode_verdict": 0, "skipped_empty": 0,
         "quality_score": 0.1, "quality_band": "Trash"}
        for page, n in per_page.items()
    ]  # fmt: skip
    body = {"type": "record", "doc_id": record["doc_id"], "cleaned_lines": rows, "pages": pages}
    return _Reply(200, {**body, "document_json": record})


def _score_record_some_unscored(unscored_every=3, page_score=0.85):
    """ocr-postprocess v1.12.0-beta when a batch ran out of GPU memory: every `unscored_every`-th
    line could not be scored (None: none). It carries perplexity 99999, reads Trash, counts in the page score,
    and is noted only in `limits_applied`."""

    def post(url, files=None, data=None, **_k):
        if url.endswith("/info"):
            return _Reply(200, {"version": "v1.12.0-beta"})
        record = json.loads(files["document_json"][1])
        rows, failed = [], 0
        for n, line in enumerate(record["lines"]):
            lost = unscored_every is not None and n % unscored_every == 0
            failed += lost
            categ, score = ("Trash", 0.0) if lost else ("Clear", 0.9)
            line.update(categ=categ, quality_score=score, lang="ces")
            rows.append(
                {"page": str(line["page"]), "line": line["line"], "text": line["text"], "category": categ,
                 "quality_score": score, "lang": "ces", "perplexity": 99999.0 if lost else 40.0}
            )  # fmt: skip
        for page in record["pages"]:
            page.update(quality_score=page_score, quality_band="Trash")
        notes = [
            {
                "limit": "ppl_batch_lines",
                "value": 32,
                "effect": "skipped",
                "count": failed,
                "detail": "...",
            }
        ]
        body = {"type": "record", "doc_id": record["doc_id"], "cleaned_lines": rows, "pages": [],
                "limits_applied": notes, "document_json": record}  # fmt: skip
        return _Reply(200, body)

    return post


def test_lines_that_could_not_be_scored_make_the_stage_partial_and_keep_the_converters_values(
    monkeypatch,
):
    _patch_requests(monkeypatch, _score_record_some_unscored())
    sent = _describe("minimal.pdf", stages="none")["document_json"]
    body = _describe("minimal.pdf", stages="ocr-postprocess")
    stage = body["stages"][1]
    assert stage["status"] == "partial" and stage["record_adopted"] is True
    assert stage["lines_unscored"] == 2 and "could not be scored" in stage["detail"]

    before = {(str(r["page"]), r["line"]): r for r in sent["lines"]}
    scored_pages = set()
    for n, row in enumerate(body["document_json"]["lines"]):
        if n % 3 == 0:  # unscored: as the converter sent it
            original = before[(str(row["page"]), row["line"])]
            assert {k: row.get(k) for k in stages.SCORED_LINE_FIELDS} == {
                k: original.get(k) for k in stages.SCORED_LINE_FIELDS
            }
        else:
            assert row["categ"] == "Clear"
            scored_pages.add(str(row["page"]))
    sent_pages = {str(p["page"]): p for p in sent["pages"]}
    assert scored_pages and len(scored_pages) < len(sent_pages), (
        "the fixture has both kinds of page"
    )
    for page in body["document_json"]["pages"]:
        if str(page["page"]) in scored_pages:  # recomputed from the lines that were scored
            assert (page["quality_score"], page["quality_band"]) == (0.9, "Clear")
        else:  # nothing on it was scored: the converter's values
            original = sent_pages[str(page["page"])]
            assert (page.get("quality_score"), page.get("quality_band")) == (
                original.get("quality_score"),
                original.get("quality_band"),
            )
    for page in body["pages"]:
        assert "Trash" not in page["quality"]["lines_by_category"], (
            "the placeholders stay out of the report"
        )


def test_a_note_without_unscored_rows_still_says_partial(monkeypatch):
    post = _score_record_some_unscored(unscored_every=None)

    def noted(url, **kwargs):
        reply = post(url, **kwargs)
        if url.endswith("/score_record"):
            reply._payload["limits_applied"][0]["count"] = 4
        return reply

    _patch_requests(monkeypatch, noted)
    stage = _describe("minimal.pdf", stages="ocr-postprocess")["stages"][1]
    assert (stage["status"], stage["lines_unscored"]) == ("partial", 4)


def test_without_a_note_the_stage_stays_ok(monkeypatch):
    post = _score_record_some_unscored(unscored_every=None)

    def clean(url, **kwargs):
        reply = post(url, **kwargs)
        if url.endswith("/score_record"):
            reply._payload["limits_applied"] = []
        return reply

    _patch_requests(monkeypatch, clean)
    stage = _describe("minimal.pdf", stages="ocr-postprocess")["stages"][1]
    assert stage["status"] == "ok" and stage["lines_unscored"] is None


def test_when_no_line_could_be_scored_the_stage_is_an_error_and_the_record_is_kept(monkeypatch):
    _patch_requests(monkeypatch, _score_record_some_unscored(unscored_every=1))
    body = _describe("minimal.pdf", stages="ocr-postprocess")
    stage = body["stages"][1]
    assert stage["status"] == "error" and stage["record_adopted"] is False
    assert "no line could be scored" in stage["detail"]
    assert all(
        "categ" not in row or row["categ"] != "Trash" for row in body["document_json"]["lines"]
    )


def test_the_quality_model_moves_a_digital_page_only_for_its_prose(monkeypatch):
    """atrium-digital-convert#5: table cells and short lines read as Trash to a model of running
    text; only a page whose prose the model calls Trash goes to OCR."""
    _patch_requests(monkeypatch, _score_record_all_trash)
    page = _describe("table.pdf", stages="ocr-postprocess")["pages"][0]
    assert page["quality"]["lines_by_category"] == {"Trash": 8}
    assert page["quality"]["prose_lines_scored"] == 1, "6 cells and one short line are not prose"
    assert page["route"] == "nlp" and "fewer than 5 to judge" in page["route_reason"]

    page = _describe("enrichable.pdf", stages="ocr-postprocess")["pages"][0]
    assert page["quality"]["prose_lines_by_category"] == {"Trash": 6}
    assert page["route"] == "ocr" and "prose lines Trash (6 of 6;" in page["route_reason"]


def test_prose_lines_are_judged_on_the_lines_the_converter_sent():
    record = {
        "doc_id": "d",
        "lines": [
            {"page": "1", "line": 0, "text": "Výsledky záchranného archeologického výzkumu"},
            {"page": "1", "line": 1, "text": "Výsledky záchranného archeologického výzkumu",
             "group_id": "tbl0-r0c0"},
        ],
    }  # fmt: skip
    sent = stages._line_index(record)
    assert stages._prose({"page": "1", "line": 0, "text": "x"}, sent) is True
    assert stages._prose({"page": "1", "line": 1}, sent) is False, "a table cell"
    row = {"page": "2", "line": 0, "text": "Výsledky záchranného archeologického výzkumu"}
    assert stages._prose(row, sent) is True, "no such line sent: the row's own text"


def test_lost_letters_route_a_digital_page_to_ocr_without_any_stage(no_stages, monkeypatch):
    page = _describe("unmapped.pdf", stages="none")["pages"][1]
    assert page["text_layer"] == "digital" and not page["needs_ocr"]
    assert (page["layout"]["words"], page["layout"]["damaged_words"]) == (10, 2)
    assert page["route"] == "ocr" and "ROUTE_DAMAGED_WORD_SHARE 0.05" in page["route_reason"]
    monkeypatch.setenv("ROUTE_DAMAGED_WORD_SHARE", "0.5")
    assert _describe("unmapped.pdf", stages="none")["pages"][1]["route"] == "nlp"


def test_a_stage_that_rewrites_converter_fields_is_rejected(monkeypatch):
    def post(url, files=None, **_k):
        record = json.loads(files["document_json"][1])
        first = record["lines"][0]
        scored = {
            "page": first["page"],
            "line": first["line"],
            "category": "Clear",
            "quality_score": 0.9,
        }
        first["text"] = "rewritten"
        return _Reply(200, {"cleaned_lines": [scored], "pages": [], "document_json": record})

    _patch_requests(monkeypatch, post)
    body = _describe("minimal.pdf", stages="ocr-postprocess")
    stage = body["stages"][1]
    assert stage["status"] == "rejected" and stage["reason"] == "converter_field_changed"
    assert body["document_json"]["lines"][0]["text"] != "rewritten"


# ── the guard on its own ────────────────────────────────────────────────────────────────────


def _record():
    return {
        "doc_id": "d",
        "source": {"origin": "digital-born-pdf"},
        "pages": [
            {
                "page": "iv",
                "page_index": 1,
                "needs_ocr": True,
                "needs_ocr_reason": "x",
                "text_layer": "garbled",
            }
        ],
        "lines": [{"page": "iv", "line": 0, "text": "a", "bbox": [0, 0, 1, 1]}],
        "content": {"text": "a"},
    }


def test_the_guard_accepts_owned_contributions():
    before = _record()
    after = json.loads(json.dumps(before))
    after["pages"][0].update(category="TEXT_P", category_confidence=0.9, quality_score=0.8)
    after["lines"][0].update(categ="Clear", quality_score=0.8, lang="ces")
    after["page_categories"] = {"iv": "TEXT_P"}
    assert stages.guard(before, after) is None


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda r: r.update(doc_id="other"), "doc_id_changed"),
        (lambda r: r["source"].update(origin="ocr:x"), "source_changed"),
        (lambda r: r["pages"].append({"page": "1"}), "page_key_mismatch"),
        (lambda r: r["pages"][0].update(needs_ocr=False), "converter_field_changed"),
        (lambda r: r["pages"][0].update(text_layer="digital"), "converter_field_changed"),
        (lambda r: r["lines"].append({"page": "iv", "line": 1}), "line_rows_changed"),
        (lambda r: r.update(content={"text": "b"}), "converter_block_changed"),
    ],
)
def test_the_guard_names_what_it_refuses(mutate, reason):
    before = _record()
    after = json.loads(json.dumps(before))
    mutate(after)
    assert stages.guard(before, after).startswith(reason)


def test_stage_selection():
    assert stages.select_stages(None) == list(stages.STAGES)
    assert stages.select_stages("none") == []
    assert stages.select_stages("ocr-postprocess, page-classification") == list(stages.STAGES)
    with pytest.raises(ValueError):
        stages.select_stages("translator")
