"""tests/test_api_contract.py — ATRIUM API meta-contract conformance (strategy §4, issue #32).

Hermetic contract test: asserts the ``/info`` envelope, ``/health``, ``/ready`` (issue #55), the advertised endpoint
set, and OpenAPI validity against the in-process app. ``importorskip``-guarded and tolerant of
missing service dependencies, so it is a clean no-op in the fast lane and a real check in CI.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# --- per-service contract parameters -----------------------------------------------------------
SERVICE = "atrium-llm-enrich"
APP_IMPORT = "service.api"
PRIMARY_ENDPOINTS = ["/extract_keywords", "/extract_keywords_text"]
# -----------------------------------------------------------------------------------------------

try:
    app = __import__(APP_IMPORT, fromlist=["app"]).app
# Only a missing dependency skips (atrium-project#53). This used to be `except Exception`,
# which turned ANY import-time failure into a green skip — including a malformed limit
# (atrium_limits.LimitConfigError), which must fail loudly.
except ImportError as exc:
    pytest.skip(f"cannot import {APP_IMPORT}.app: {exc}", allow_module_level=True)

client = TestClient(app)


def test_info_envelope_required_fields():
    """§4.1: /info always carries service, version, endpoints, limits.max_upload_mb."""
    response = client.get("/info")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == SERVICE
    assert data["version"] and data["version"] == app.version
    assert isinstance(data["endpoints"], list) and data["endpoints"]
    assert isinstance(data["limits"], dict)
    assert "max_upload_mb" in data["limits"]


def test_info_reports_every_declared_limit():
    """atrium-project#53: /info `limits` is tool_limits.LIMITS, value for value, and
    `limits_meta` names the variable that sets each one. tests/test_limits_contract.py checks
    the declaration against .env.example and the README."""
    from tool_limits import LIMITS

    data = client.get("/info").json()
    assert data["limits"] == LIMITS.values()
    assert data["limits_meta"] == LIMITS.meta()


def test_errors_have_the_harmonised_body():
    """§4.4 (atrium-project#32 item 2): every error is {status, reason, detail}."""
    body = client.get("/no-such-route").json()
    assert body == {"status": 404, "reason": None, "detail": "Not Found"}


def test_info_endpoints_match_real_routes():
    """Advertised endpoints are real routes, and every primary endpoint is advertised."""
    advertised = set(client.get("/info").json()["endpoints"])
    real = {r.path for r in app.routes if getattr(r, "methods", None)}
    assert advertised <= real
    for path in PRIMARY_ENDPOINTS:
        assert path in advertised, f"{path} missing from /info endpoints"


def test_health_shallow_ok():
    """§4.1: shallow /health is a cheap 200 liveness probe."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_primary_endpoints_documented_in_openapi():
    paths = app.openapi()["paths"]
    for path in PRIMARY_ENDPOINTS:
        assert path in paths, f"{path} missing from OpenAPI paths"


def test_openapi_document_is_spec_valid():
    """The runtime /openapi.json validates against the OpenAPI 3.x spec (§2.2)."""
    spec_validator = pytest.importorskip("openapi_spec_validator")
    spec_validator.validate(app.openapi())


# --- §4.6 readiness + shutdown contract (issue #55) --------------------------------------------
# The state-machine itself is unit-tested once, in the hub
# (atrium-project/docs/templates/shared/test_atrium_service.py). What these assert is that THIS
# repo actually wired it up: the route exists, it is advertised, and — the one that matters —
# liveness does not start failing just because the service is draining.

try:
    _state = getattr(__import__(APP_IMPORT, fromlist=["app"]), "_state", None)
except Exception:  # noqa: BLE001 - same missing-heavy-deps case this file already guards
    # Repos guard the app import two different ways (module-level pytest.skip vs a
    # `deps_present` flag + pytestmark.skipif). Under the second style this module keeps
    # loading after a failed import, so this must not raise at import time; the skip
    # marker already stops the tests below from running.
    _state = None


def test_ready_route_is_registered_and_advertised():
    """§4.6: /ready exists, and /info advertises it like any other route."""
    assert _state is not None, (
        f"{APP_IMPORT} has no module-level `_state` — the service has not adopted "
        "ServiceState/attach_health(state=...) (issue #55)"
    )
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    assert response.json()["status"] in {"ready", "starting", "draining"}
    assert "/ready" in client.get("/info").json()["endpoints"]


def test_ready_reports_starting_before_warmup_and_ready_after():
    """503 until the service's own lifespan marks it warm, 200 once it has.

    `client` above is a bare TestClient, so the ASGI lifespan has NOT run and the service is
    genuinely un-warm here — which is exactly the pre-warmup state a Kubernetes startupProbe
    sees on a cold pod.
    """
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.draining = False
        _state.warm = False
        assert client.get("/ready").status_code == 503
        assert client.get("/ready").json()["status"] == "starting"

        _state.warm = True
        assert client.get("/ready").status_code == 200
        assert client.get("/ready").json()["status"] == "ready"
    finally:
        _state.warm, _state.draining = was_warm, was_draining


def test_liveness_stays_200_while_draining_but_readiness_does_not():
    """The load-bearing distinction of issue #55.

    If shallow /health went 503 on SIGTERM, an orchestrator's livenessProbe would SIGKILL the
    container before its drain finished — the very failure the drain exists to prevent. Routing
    traffic away from a draining pod is /ready's job.
    """
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.warm = True
        _state.draining = True

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "draining"
    finally:
        _state.warm, _state.draining = was_warm, was_draining


def test_deep_health_reports_draining_with_operator_fields():
    """`?deep=true` had no coverage in any repo before issue #55."""
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.warm = True
        _state.draining = True
        response = client.get("/health?deep=true")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["detail"] == "shutting down"
        assert body["draining"] is True
        assert "in_flight" in body
    finally:
        _state.warm, _state.draining = was_warm, was_draining


# --- the typed contract (atrium-project#32 round 2) --------------------------------------------
# tests/test_openapi_contract.py (canonical, vendored) checks the committed spec itself. What
# these add is the part only this repo can do: drive the real endpoints (with the engine
# replaced by a canned one, as tests/test_limits.py does) and hold every response — 200s and
# refusals alike — to the schema the PUBLISHED spec declares for it.

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import atrium_openapi  # noqa: E402
import atrium_rocrate  # noqa: E402

_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")

_LINE_REPLY = json.dumps(
    {
        "extracted_keywords_cs": ["kostel"],
        "extracted_keywords_en": ["church"],
        "teater_category": "kostel",
        "confidence_score": 0.9,
    }
)
_DOC_REPLY = json.dumps(
    {
        "items": [
            {
                "locator": "gotického kostela",
                "page": "1",
                "extracted_keywords_cs": ["kostel"],
                "extracted_keywords_en": ["church"],
                "teater_category": "kostel",
                "confidence_score": 0.9,
            }
        ]
    }
)
_CSV = "text,page_num,line_num,categ,quality_score\nVýzkum odhalil základy gotického kostela.,1,1,Clear,0.9\n"
_SEED = {
    "schema_version": "1.0",
    "record_type": "atrium-document",
    "doc_id": "C-202000543A-DT-27",
    "pages": [{"page": "1", "page_index": 1}],
}


@pytest.fixture
def engine(monkeypatch):
    from llm_client_shared import build_document_schema, build_schema
    from service import api

    calls = []
    eng = {
        "backend": "openrouter",
        "model": "test/model",
        "line_prompt": "p",
        "line_model": build_schema(["kostel"]),
        "line_chat_fn": lambda m: calls.append(m) or _LINE_REPLY,
        "doc_prompt": "p",
        "doc_prompt_tokens": 100,
        "doc_model": build_document_schema(["kostel"]),
        "doc_chat_fn": lambda m: calls.append(m) or _DOC_REPLY,
        "filter_params": {},
        "calls": calls,
    }
    monkeypatch.setattr(api, "_require_engine", lambda: eng)
    return eng


def _conforms(path, status, response):
    pytest.importorskip("jsonschema")
    assert response.status_code == status, response.text
    atrium_openapi.validate_response(_SPEC, path, "post", status, response.json())
    return response.json()


def test_line_mode_response_conforms_to_the_published_schema(engine):
    response = client.post(
        "/extract_keywords", files={"file": ("d.csv", _CSV.encode(), "text/csv")}
    )
    body = _conforms("/extract_keywords", 200, response)
    assert body["mode"] == "line" and body["results"][0]["page"] == 1
    # The run comes back as its CreateAction (atrium-project#71): the upload in, the results out.
    action = body["paradata"]
    assert atrium_rocrate.action_problems(action) == []
    assert [(e["name"], e["encodingFormat"]) for e in action["object"]] == [("d.csv", "text/csv")]
    assert [e["name"] for e in action["result"]] == ["results.json"]


def test_document_mode_with_a_seed_conforms_including_the_record(engine, tmp_path, monkeypatch):
    """The returned record is held to the vendored record schema, through the spec's
    AtriumDocument component — the type AMČR's generated client deserialises it into."""
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/extract_keywords_text",
        json={"text": "Výzkum odhalil základy gotického kostela.", "document_json": _SEED},
    )
    body = _conforms("/extract_keywords_text", 200, response)
    assert body["doc_id"] == _SEED["doc_id"] and body["document_json"]["doc_id"] == _SEED["doc_id"]
    assert "document_json_schema_error" not in body


#: An AMČR seed (atrium-project#71): the file id and the archive's own view of the original.
_AMCR_SEED = {
    "doc_id": "C-202000543A-DT-27",
    "source": {"sha512": "c" * 128, "filename": "zprava.pdf", "media_type": "application/pdf"},
}


def test_an_amcr_seed_keeps_its_identity_and_the_run_is_returned(engine, tmp_path, monkeypatch):
    """atrium-project#71 through /extract_keywords: the seed's id and source come back
    unchanged (llm-enrich reads no source), the `enrichment` block carries the run_uuid that is
    the returned CreateAction's @id, and nothing is written to the working directory."""
    monkeypatch.chdir(tmp_path)
    files = {
        "file": (
            "zprava.md",
            "Výzkum odhalil základy gotického kostela.".encode(),
            "text/markdown",
        ),
        "document_json": (
            "seed.document.json",
            json.dumps(_AMCR_SEED).encode(),
            "application/json",
        ),
    }
    body = _conforms("/extract_keywords", 200, client.post("/extract_keywords", files=files))
    record = body["document_json"]
    assert record["doc_id"] == _AMCR_SEED["doc_id"] and record["source"] == _AMCR_SEED["source"]
    assert "document_json_schema_error" not in body

    action = body["paradata"]
    assert atrium_rocrate.action_problems(action) == []
    assert record["assembled"]["blocks"]["enrichment"]["run_uuid"] == action["@id"]
    assert record["provenance"]["contributors"][-1]["paradata_ref"] == action["@id"]
    assert "#record" in {e["@id"] for e in action["object"]}
    assert "#block-enrichment" in {e["@id"] for e in action["result"]}
    assert list(tmp_path.iterdir()) == []


def test_a_wrong_file_type_is_415_unsupported_media_type(engine):
    response = client.post(
        "/extract_keywords", files={"file": ("d.pdf", b"%PDF-1.7", "application/pdf")}
    )
    body = _conforms("/extract_keywords", 415, response)
    assert body["reason"] == "unsupported_media_type"
    assert body["accepted"] == [".csv", ".teitok.xml", ".md", ".txt"]
    assert body["detail"] == "Unsupported file type. Accepted: .csv, .teitok.xml, .md, .txt."


@pytest.mark.parametrize(
    "part", [b"[1, 2]", b"{not json", b'{"schema_version": "9.0", "doc_id": "x"}']
)
def test_a_record_that_cannot_be_opened_is_422_invalid_record_before_any_call(engine, part):
    files = {
        "file": ("d.csv", _CSV.encode(), "text/csv"),
        "document_json": ("d.document.json", part, "application/json"),
    }
    body = _conforms("/extract_keywords", 422, client.post("/extract_keywords", files=files))
    assert body["reason"] == "invalid_record" and engine["calls"] == []


def test_an_inline_record_that_cannot_be_opened_is_422_invalid_record(engine):
    response = client.post(
        "/extract_keywords_text",
        json={"text": "kostel", "document_json": {"schema_version": "7.0"}},
    )
    body = _conforms("/extract_keywords_text", 422, response)
    assert body["reason"] == "invalid_record" and engine["calls"] == []


def test_an_empty_record_part_counts_as_none(engine):
    files = {
        "file": ("d.csv", _CSV.encode(), "text/csv"),
        "document_json": ("d.document.json", b"", "application/json"),
    }
    body = _conforms("/extract_keywords", 200, client.post("/extract_keywords", files=files))
    assert "document_json" not in body


def test_malformed_teitok_is_422_not_500(engine):
    files = {"file": ("d.teitok.xml", b"<TEI><text><unclosed", "application/xml")}
    body = _conforms("/extract_keywords", 422, client.post("/extract_keywords", files=files))
    assert body["detail"].startswith("The upload could not be read:")


def test_an_unready_backend_is_503_with_the_error_body():
    from service import api

    api._engine.clear()
    body = _conforms(
        "/extract_keywords_text", 503, client.post("/extract_keywords_text", json={"text": "x"})
    )
    assert body["reason"] is None


def test_ocr_text_layer_is_the_registered_code_of_the_converters_refusal(tmp_path):
    """atrium-llm-enrich#10 W6: AMČR's route step sends a document refused with the 422
    `ocr_text_layer` to OCR, and relies on that code staying stable. The converter raises it
    (api_util/digital_to_json.py); api-digital (W1) will answer it; the registry fixes it —
    so a rename on either side fails here. The PDF is the pinned fixture that
    tests/test_digital_to_json.py builds the same way."""
    pytest.importorskip("pdfplumber")
    import importlib.util

    from api_util import digital_to_json as d2j
    from service.atrium_service import REASON_CODES, REASON_STATUSES

    maker = Path(__file__).resolve().parent / "fixtures" / "digital" / "make_fixtures.py"
    spec = importlib.util.spec_from_file_location("make_fixtures", maker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pdf = tmp_path / "ocr_layer.pdf"
    pdf.write_bytes(module.build_all()["ocr_layer.pdf"])

    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(pdf))
    assert info.value.reason == "ocr_text_layer"
    assert info.value.reason in REASON_CODES and REASON_STATUSES[info.value.reason] == (422,)
    assert info.value.reason in _SPEC["x-atrium-reason-codes"]
