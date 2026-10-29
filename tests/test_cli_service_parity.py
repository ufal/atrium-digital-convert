"""
tests/test_cli_service_parity.py
================================
The command line and the service read the same inputs and give the same results
(atrium-project#72, the service–CLI alignment of 2026-10-26).

`api_util/digital_to_json.py` and `POST /reformat` / `POST /describe` run one converter, and since
v1.3.0-beta the two views as well: `--markdown` is `markdown=true`, `--describe` is `/describe`,
both through `service/describe.py`. What differs on purpose is pinned here too: the paradata is a
file on the command line and a `CreateAction` in the response (hub #71).

v1.4.0-beta: the paradata's parameters and counts are the same (`d2j.run_config`; the service adds
`endpoint`); an empty file, a record that cannot be opened, and an inherited invalid record end
alike on both sides (`d2j.open_record`, `d2j.gate`).
"""

from __future__ import annotations

import json
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

from api_util import digital_to_json as d2j  # noqa: E402
from service import api  # noqa: E402

client = TestClient(api.app)
FIXTURES = make_fixtures.build_all()

#: The blocks this converter writes; `assembled` and the run stamps differ per run by design.
BLOCKS = ("doc_id", "source", "pages", "lines", "content", "tables")


@pytest.fixture
def no_stages(monkeypatch):
    monkeypatch.delenv("PAGE_CLASSIFICATION_URL", raising=False)
    monkeypatch.delenv("OCR_POSTPROCESS_URL", raising=False)


def _cli(tmp_path: Path, name: str, *flags: str) -> Path:
    src = tmp_path / "in" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(FIXTURES[name])
    out = tmp_path / "out"
    assert d2j.main([str(src), "--out-dir", str(out), *flags]) == 0
    return out


def _service(endpoint: str, name: str, **form) -> dict:
    files = {"file": (name, FIXTURES[name], "application/octet-stream")}
    response = client.post(endpoint, files=files, data=form)
    assert response.status_code == 200, response.text
    return response.json()


def _blocks(record: dict) -> dict:
    return {key: record.get(key) for key in BLOCKS}


@pytest.mark.parametrize(
    "name", ["minimal.pdf", "table.pdf", "garbled.pdf", "rich.docx", "headings.pdf"]
)
def test_the_record_is_the_same_from_the_cli_and_from_reformat(tmp_path, name):
    out = _cli(tmp_path, name)
    stem = Path(name).stem
    cli_record = json.loads((out / f"{stem}.document.json").read_text(encoding="utf-8"))
    body = _service("/reformat", name)
    assert _blocks(cli_record) == _blocks(body["document_json"])
    assert cli_record["provenance"]["license"] == body["document_json"]["provenance"]["license"]


@pytest.mark.parametrize("name", ["minimal.pdf", "rich.docx"])
def test_markdown_is_the_same_from_the_cli_and_from_reformat(tmp_path, name):
    out = _cli(tmp_path, name, "--markdown")
    stem = Path(name).stem
    body = _service("/reformat", name, markdown="true")
    assert (out / f"{stem}.md").read_text(encoding="utf-8") == body["markdown"]


@pytest.mark.parametrize("name", ["garbled.pdf", "minimal.docx"])
def test_the_assessment_is_the_same_from_the_cli_and_from_describe(tmp_path, no_stages, name):
    out = _cli(tmp_path, name, "--describe")
    stem = Path(name).stem
    report = json.loads((out / f"{stem}.describe.json").read_text(encoding="utf-8"))
    body = _service("/describe", name)
    for key in ("pages", "summary", "stages", "reader"):
        assert report[key] == body[key], key
    assert {s["status"] for s in report["stages"]} == {"not_configured"}


def test_the_describe_options_mean_the_same_on_both_sides(tmp_path, no_stages):
    out = _cli(tmp_path, "minimal.pdf", "--describe", "--stages", "none", "--no-text")
    report = json.loads((out / "minimal.describe.json").read_text(encoding="utf-8"))
    body = _service("/describe", "minimal.pdf", stages="none", include_text="false")
    assert report["pages"] == body["pages"]
    assert report["stages"] == body["stages"]
    assert all(page.get("text") is None for page in report["pages"])


def test_a_bad_stages_value_is_refused_by_both(tmp_path, capsys):
    src = tmp_path / "minimal.pdf"
    src.write_bytes(FIXTURES["minimal.pdf"])
    with pytest.raises(SystemExit) as caught:
        d2j.main([str(src), "--describe", "--stages", "no-such-stage"])
    assert caught.value.code == 2
    files = {"file": ("minimal.pdf", FIXTURES["minimal.pdf"], "application/pdf")}
    assert (
        client.post("/describe", files=files, data={"stages": "no-such-stage"}).status_code == 422
    )


def test_paradata_is_a_file_on_the_cli_and_a_create_action_in_the_service(tmp_path):
    """Kept apart on purpose (hub #71): the command line writes the run's record beside its
    outputs, the service returns it as the answer's `paradata`."""
    para = tmp_path / "paradata"
    _cli(tmp_path, "minimal.pdf", "--markdown", "--paradata-dir", str(para))
    (written,) = list(para.glob("*_digital-convert.json"))
    record = json.loads(written.read_text(encoding="utf-8"))
    assert record["config"]["markdown"] is True
    body = _service("/reformat", "minimal.pdf", markdown="true")
    assert body["paradata"]["@type"] == "CreateAction"
    served = body["paradata"]["paradataRecord"]
    assert served["config"] == {**record["config"], "endpoint": "/reformat"}
    assert (
        served["statistics"]["output_counts_by_type"]
        == record["statistics"]["output_counts_by_type"]
    )


@pytest.mark.parametrize(
    ("content", "seed", "code", "reason"),
    [
        (b"", None, 4, "corrupt"),
        (None, b"{not json", 3, "invalid_record"),
        (None, b'{"schema_version": "9.0", "doc_id": "x"}', 3, "invalid_record"),
        # v1.4.1-beta: a record that opens but cannot be added to (atrium-project#72). The merge
        # failed on `.get`: a traceback from the CLI and a 500 from the service.
        (None, b'{"doc_id": "minimal", "pages": "1"}', 3, "invalid_record"),
        (None, b'{"doc_id": "minimal", "lines": {"page": "1", "line": 0}}', 3, "invalid_record"),
    ],
)
def test_an_empty_file_or_an_unreadable_record_ends_alike(
    tmp_path, capsys, content, seed, code, reason
):
    data = FIXTURES["minimal.pdf"] if content is None else content
    src = tmp_path / "minimal.pdf"
    src.write_bytes(data)
    flags = ["--out-dir", str(tmp_path / "out")]
    files = {"file": ("minimal.pdf", data, "application/pdf")}
    if seed is not None:
        (tmp_path / "seed.json").write_bytes(seed)
        flags += ["--document-json", str(tmp_path / "seed.json")]
        files["document_json"] = ("seed.json", seed, "application/json")
    assert d2j.main([str(src), *flags]) == code
    assert f"] {reason}:" in capsys.readouterr().err
    response = client.post("/reformat", files=files)
    assert response.status_code == 422
    assert reason in (response.json().get("reason"), response.json().get("cause"))


def test_an_inherited_invalid_record_is_converted_and_reported_by_both(tmp_path, capsys):
    seed = {
        "schema_version": "1.0",
        "record_type": "atrium-document",
        "doc_id": "minimal",
        "translations": 5,
    }
    (tmp_path / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
    out = _cli(tmp_path, "minimal.pdf", "--document-json", str(tmp_path / "seed.json"))
    assert (out / "minimal.document.json").is_file()
    assert "does not validate" in capsys.readouterr().err
    response = client.post(
        "/reformat",
        files={
            "file": ("minimal.pdf", FIXTURES["minimal.pdf"], "application/pdf"),
            "document_json": ("seed.json", json.dumps(seed).encode(), "application/json"),
        },
    )
    assert response.status_code == 200
    assert response.json().get("document_json_schema_error")
