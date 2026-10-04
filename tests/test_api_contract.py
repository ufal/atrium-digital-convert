"""tests/test_api_contract.py — ATRIUM API meta-contract conformance (strategy §4, issue #32).

Hermetic contract test: asserts the ``/info`` envelope, ``/health``, ``/ready`` (issue #55), the advertised endpoint
set, and OpenAPI validity against the in-process app. ``importorskip``-guarded and tolerant of
missing service dependencies, so it is a clean no-op in the fast lane and a real check in CI.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# --- per-service contract parameters -----------------------------------------------------------
SERVICE = "atrium-digital-convert"
APP_IMPORT = "service.api"
PRIMARY_ENDPOINTS = ["/reformat", "/describe"]
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
# these add is the part only this repo can do: drive the real endpoints on the generated digital
# fixtures and hold every response — 200s and refusals alike — to the schema the PUBLISHED spec
# declares for it. No stage is configured here (tests/test_describe.py drives the stages).

import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import atrium_openapi  # noqa: E402
import atrium_rocrate  # noqa: E402

_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "digital"))
import make_fixtures  # noqa: E402

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning")]


@pytest.fixture(autouse=True)
def _no_stages(monkeypatch):
    for name in ("PAGE_CLASSIFICATION_URL", "OCR_POSTPROCESS_URL"):
        monkeypatch.delenv(name, raising=False)


def _conforms(path, status, response):
    pytest.importorskip("jsonschema")
    pytest.importorskip("pdfplumber")
    assert response.status_code == status, response.text
    atrium_openapi.validate_response(_SPEC, path, "post", status, response.json())
    return response.json()


def _pdf(name="minimal.pdf"):
    return make_fixtures.build_all()[name]


def test_reformat_returns_the_record_and_its_run():
    response = client.post("/reformat", files={"file": ("zprava.pdf", _pdf(), "application/pdf")})
    body = _conforms("/reformat", 200, response)
    record = body["document_json"]
    assert body["service"] == SERVICE and body["doc_id"] == "zprava" == record["doc_id"]
    assert record["source"]["origin"] == "digital-born-pdf"
    assert record["assembled"]["blocks"]["lines"]["program"] == "digital-convert"
    assert "markdown" not in body, "Markdown only on request"
    assert body["reader"]["kind"] == "pdf" and body["reader"]["pages"] == 2
    action = body["paradata"]
    assert atrium_rocrate.action_problems(action) == []
    assert record["assembled"]["blocks"]["lines"]["run_uuid"] == action["@id"]
    assert [(e["name"], e["encodingFormat"]) for e in action["object"]] == [
        ("zprava.pdf", "application/pdf")
    ]
    assert "#block-lines" in {e["@id"] for e in action["result"]}


def test_reformat_renders_markdown_on_request():
    files = {"file": ("zprava.pdf", _pdf(), "application/pdf")}
    body = _conforms(
        "/reformat", 200, client.post("/reformat", files=files, data={"markdown": "true"})
    )
    assert body["markdown"].startswith("# zprava")
    assert "zprava.md" in {e["name"] for e in body["paradata"]["result"]}


#: An AMČR seed (atrium-project#71): the file id and the archive's own view of the original.
def _seed(data: bytes, sha512=None):
    return {
        "doc_id": "C-202000543A-DT-27",
        "source": {
            "sha512": sha512 or hashlib.sha512(data).hexdigest(),
            "filename": "zprava.pdf",
            "media_type": "application/pdf",
        },
    }


def test_an_amcr_seed_keeps_its_identity_and_the_run_is_returned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = _pdf()
    seed = _seed(data)
    files = {
        "file": ("upload-1234.pdf", data, "application/pdf"),
        "document_json": ("seed.document.json", json.dumps(seed).encode(), "application/json"),
    }
    body = _conforms("/reformat", 200, client.post("/reformat", files=files))
    record = body["document_json"]
    assert record["doc_id"] == seed["doc_id"] == body["doc_id"]
    assert {k: v for k, v in record["source"].items() if k not in ("origin", "page_count")} == seed[
        "source"
    ]
    assert "#record" in {e["@id"] for e in body["paradata"]["object"]}
    assert "document_json_schema_error" not in body
    assert list(tmp_path.iterdir()) == [], "nothing is written to the working directory"


def test_a_seed_for_another_file_is_422_source_digest_mismatch():
    files = {
        "file": ("zprava.pdf", _pdf(), "application/pdf"),
        "document_json": (
            "seed.json",
            json.dumps(_seed(b"", "0" * 128)).encode(),
            "application/json",
        ),
    }
    body = _conforms("/reformat", 422, client.post("/reformat", files=files))
    assert body["reason"] == "source_digest_mismatch"
    assert "source_digest_mismatch" in _SPEC["x-atrium-reason-codes"]


def test_an_ocr_layer_pdf_is_422_ocr_text_layer():
    files = {"file": ("scan.pdf", _pdf("ocr_layer.pdf"), "application/pdf")}
    body = _conforms("/reformat", 422, client.post("/reformat", files=files))
    assert body["reason"] == "ocr_text_layer"


def test_a_garbled_page_is_flagged_not_refused():
    files = {"file": ("g.pdf", _pdf("garbled.pdf"), "application/pdf")}
    body = _conforms("/reformat", 200, client.post("/reformat", files=files))
    page = body["document_json"]["pages"][0]
    assert page["needs_ocr"] is True and page["needs_ocr_reason"]


def test_a_type_the_converter_does_not_read_is_415():
    files = {"file": ("notes.txt", b"hello", "text/plain")}
    body = _conforms("/reformat", 415, client.post("/reformat", files=files))
    assert body["reason"] == "unsupported_media_type" and body["cause"] == "unsupported"
    assert ".pdf" in body["accepted"] and "application/pdf" in body["accepted"]


@pytest.mark.parametrize(
    "part", [b"[1, 2]", b"{not json", b'{"schema_version": "9.0", "doc_id": "x"}']
)
def test_a_record_that_cannot_be_opened_is_422_invalid_record(part):
    files = {
        "file": ("zprava.pdf", _pdf(), "application/pdf"),
        "document_json": ("d.document.json", part, "application/json"),
    }
    body = _conforms("/reformat", 422, client.post("/reformat", files=files))
    assert body["reason"] == "invalid_record"


def test_an_empty_record_part_counts_as_none():
    files = {
        "file": ("zprava.pdf", _pdf(), "application/pdf"),
        "document_json": ("d.document.json", b"", "application/json"),
    }
    body = _conforms("/reformat", 200, client.post("/reformat", files=files))
    assert "#record" not in {e["@id"] for e in body["paradata"]["object"]}


def test_a_broken_file_is_422_with_its_cause():
    files = {"file": ("broken.docx", b"PK\x03\x04 not a zip", "application/octet-stream")}
    body = _conforms("/reformat", 422, client.post("/reformat", files=files))
    assert body["reason"] is None and body["cause"] == "corrupt"


def test_a_document_over_max_pages_is_422_limit_exceeded(monkeypatch):
    monkeypatch.setenv("MAX_PAGES", "1")
    files = {"file": ("zprava.pdf", _pdf(), "application/pdf")}
    body = _conforms("/reformat", 422, client.post("/reformat", files=files))
    assert body["reason"] == "limit_exceeded" and body["limit"]["env"] == "MAX_PAGES"


def test_every_slot_taken_is_429_busy(monkeypatch):
    from service import api

    monkeypatch.setattr(api._slots, "acquire", lambda: False)
    files = {"file": ("zprava.pdf", _pdf(), "application/pdf")}
    response = client.post("/reformat", files=files)
    body = _conforms("/reformat", 429, response)
    assert body["reason"] == "busy" and response.headers["Retry-After"]


def test_a_draining_service_refuses_new_work_with_503():
    assert _state is not None
    was = _state.draining
    try:
        _state.draining = True
        files = {"file": ("zprava.pdf", _pdf(), "application/pdf")}
        body = _conforms("/reformat", 503, client.post("/reformat", files=files))
        assert body["reason"] is None
    finally:
        _state.draining = was


def test_describe_without_stages_conforms_and_routes_every_page():
    files = {"file": ("img.pdf", _pdf("image_only.pdf"), "application/pdf")}
    body = _conforms("/describe", 200, client.post("/describe", files=files))
    assert [s["status"] for s in body["stages"]] == ["not_configured", "not_configured"]
    routes = [(p["text_layer"], p["route"]) for p in body["pages"]]
    assert routes[0] == ("digital", "nlp") and routes[1] == ("none", "ocr")
    assert (
        body["summary"]["pages"] == len(body["pages"])
        and body["summary"]["document_route"] == "mixed"
    )
    assert atrium_rocrate.action_problems(body["paradata"]) == []


def test_describe_refuses_an_unknown_stage_name():
    files = {"file": ("zprava.pdf", _pdf(), "application/pdf")}
    response = client.post("/describe", files=files, data={"stages": "ocr-postprocess,translator"})
    body = _conforms("/describe", 422, response)
    assert "translator" in body["detail"]


def test_ocr_text_layer_is_the_registered_code_of_the_converters_refusal(tmp_path):
    """atrium-digital-convert#4 W6: AMČR's route step sends a document refused with the 422
    `ocr_text_layer` to OCR, and relies on that code staying stable. The converter raises it,
    /reformat answers it, the registry fixes it — so a rename on either side fails here."""
    pytest.importorskip("pdfplumber")
    from api_util import digital_to_json as d2j
    from service.atrium_service import REASON_CODES, REASON_STATUSES

    pdf = tmp_path / "ocr_layer.pdf"
    pdf.write_bytes(_pdf("ocr_layer.pdf"))
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(pdf))
    assert info.value.reason == "ocr_text_layer"
    assert info.value.reason in REASON_CODES and REASON_STATUSES[info.value.reason] == (422,)
    assert info.value.reason in _SPEC["x-atrium-reason-codes"]


def test_the_rename_from_llm_enrich_is_declared_in_the_spec():
    """The release gate accepts a changed service id only when the spec declares the old one
    (atrium-project#72): v1.0.0-beta published `atrium-llm-enrich`."""
    assert _SPEC["info"]["x-atrium-service"] == SERVICE
    assert _SPEC["info"]["x-atrium-service-previous"] == "atrium-llm-enrich"
