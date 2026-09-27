"""tests/test_limits.py — every limit is a setting, and none cuts an input quietly.

atrium-project#53 (factor III), for this repo: the limits are declared in tool_limits.py, the
context window defaults per backend, a document that cannot fit one call is refused before
the call, a reply cut at the token cap is never used, only transient failures are retried,
and every limit that shapes a result without refusing it is returned in ``limits_applied``.
tests/test_limits_contract.py (canonical) checks the declaration against .env.example and
service/README.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import tool_limits
from llm_client_shared import (
    ReplyTruncated,
    RequestRefused,
    build_document_schema,
    build_schema,
    run_document_level,
    run_line_level,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_LINE_REPLY = json.dumps(
    {
        "extracted_keywords_cs": ["kostel"],
        "extracted_keywords_en": ["church"],
        "teater_category": "kostel",
        "confidence_score": 0.9,
    }
)
_CSV = (
    "text,page_num,line_num,categ,quality_score\n"
    "Výzkum odhalil základy gotického kostela.,1,1,Clear,0.9\n"
    "Další řádek o nálezu keramiky u kostela.,1,2,Clear,0.9\n"
    "Třetí řádek o hrobech na hřbitově.,1,3,Clear,0.9\n"
)


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = text or json.dumps(payload or {})
    return r


def _run(code: str, **env) -> subprocess.CompletedProcess:
    """Import tool_limits in a fresh interpreter (its defaults are read at import)."""
    import os

    full = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    full.update(env)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=full, capture_output=True, text=True
    )


# ── the context window: per backend, then the config file, then the environment ──────


def test_the_context_window_defaults_per_backend():
    code = "import tool_limits as t; print(t.LLM_CONTEXT_WINDOW.default, t.context_window())"
    assert _run(code).stdout.split() == ["128000", "128000"]
    assert _run(code, LLM_BACKEND="ollama").stdout.split() == ["32000", "32000"]
    assert _run(code, LLM_BACKEND="ollama", LLM_CONTEXT_WINDOW="64000").stdout.split() == [
        "32000",
        "64000",
    ]


def test_the_config_files_context_window_comes_before_the_default(tmp_path, monkeypatch):
    cfg = tmp_path / "llm_config.txt"
    cfg.write_text('# comment\nCONTEXT_WINDOW="16000"\n', encoding="utf-8")
    monkeypatch.setenv("LLM_CONFIG", str(cfg))
    monkeypatch.delenv("LLM_CONTEXT_WINDOW", raising=False)
    assert tool_limits.context_window() == 16000
    assert tool_limits.LIMITS.meta()["llm_context_window"]["source"] == "config"
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "20000")
    assert tool_limits.context_window() == 20000
    assert tool_limits.vocab_prompt_budget_tokens() == 20000 - 2048 - 512


def test_a_window_with_no_room_for_the_prompt_fails_at_import():
    result = _run("import tool_limits", LLM_CONTEXT_WINDOW="2000")
    assert result.returncode != 0
    assert "LimitConfigError" in result.stderr and "LLM_CONTEXT_WINDOW" in result.stderr


def test_a_malformed_limit_fails_at_import_naming_it():
    result = _run("import tool_limits", LLM_MAX_NEW_TOKENS="lots")
    assert result.returncode != 0 and "LLM_MAX_NEW_TOKENS" in result.stderr


# ── the clients: the cap is sent, a cut reply is refused, only transient errors retried ──


def test_openrouter_sends_the_cap_and_refuses_a_cut_reply(monkeypatch):
    from openrouter_client import make_chat_fn

    monkeypatch.setenv("LLM_MAX_NEW_TOKENS", "64")
    session = MagicMock()
    session.post.return_value = _resp(
        payload={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
    )
    chat = make_chat_fn(session, {}, "m", None, 3, 7, None)
    with pytest.raises(ReplyTruncated, match="64 tokens") as info:
        chat([{"role": "user", "content": "x"}])
    assert info.value.max_new_tokens == 64
    assert session.post.call_count == 1  # not retried
    assert session.post.call_args.kwargs["json"]["max_tokens"] == 64
    assert session.post.call_args.kwargs["timeout"] == 7


def test_openrouter_does_not_retry_a_4xx_and_keeps_its_body(monkeypatch):
    from openrouter_client import make_chat_fn

    monkeypatch.setattr("openrouter_client.time.sleep", lambda _s: None)
    session = MagicMock()
    session.post.return_value = _resp(400, text='{"error": "context length exceeded"}')
    chat = make_chat_fn(session, {}, "m", None, 3, 7, None)
    with pytest.raises(RequestRefused, match="context length exceeded"):
        chat([{"role": "user", "content": "x"}])
    assert session.post.call_count == 1

    session.post.reset_mock()
    session.post.return_value = _resp(503, text="overloaded")
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        chat([{"role": "user", "content": "x"}])
    assert session.post.call_count == 3


def test_ollama_gets_the_window_and_the_cap_and_refuses_a_cut_reply(monkeypatch):
    from ollama_client import make_chat_fn

    monkeypatch.setattr("ollama_client.time.sleep", lambda _s: None)
    monkeypatch.setenv("LLM_MAX_NEW_TOKENS", "128")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "16000")
    session = MagicMock()
    session.post.return_value = _resp(payload={"message": {"content": "{}"}, "done_reason": "stop"})
    chat = make_chat_fn(session, "http://o", "m", {}, 2, 9)
    assert chat([{"role": "user", "content": "x"}]) == "{}"
    options = session.post.call_args.kwargs["json"]["options"]
    assert (options["num_ctx"], options["num_predict"]) == (16000, 128)

    session.post.return_value = _resp(
        payload={"message": {"content": "{"}, "done_reason": "length"}
    )
    with pytest.raises(ReplyTruncated):
        chat([{"role": "user", "content": "x"}])

    session.post.reset_mock()
    session.post.return_value = _resp(404, text="model 'm' not found")
    with pytest.raises(RequestRefused, match="not found"):
        chat([{"role": "user", "content": "x"}])
    assert session.post.call_count == 1


# ── the shared drivers count what the limits did ─────────────────────────────────────


def _truncating_chat(_messages):
    raise ReplyTruncated("cut", 2048)


def test_line_mode_counts_cut_replies_and_the_stop(tmp_path):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    records, stats = run_line_level(
        csv_path, _truncating_chat, "p", build_schema(["kostel"]), max_consecutive_errors=2
    )
    assert records == []
    assert (stats["truncated"], stats["aborted"], stats["unprocessed"]) == (2, 1, 1)


def test_document_mode_raises_in_strict_mode_only(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("Výzkum odhalil základy kostela.", encoding="utf-8")
    model = build_document_schema(["kostel"])
    records, stats = run_document_level(doc, _truncating_chat, "p", model)
    assert (records, stats["aborted"], stats["truncated"]) == ([], 1, 1)
    with pytest.raises(ReplyTruncated):
        run_document_level(doc, _truncating_chat, "p", model, strict=True)


# ── the service ──────────────────────────────────────────────────────────────────────

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from service import api  # noqa: E402

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


@pytest.fixture
def engine(monkeypatch):
    from atrium_limits import LimitNotes

    standing = LimitNotes()
    standing.note("vocab_prompt_budget_tokens", "trimmed", 1257, "terms left out", value=29440)
    eng = {
        "backend": "openrouter",
        "model": "test/model",
        "line_prompt": "p",
        "line_model": build_schema(["kostel"]),
        "line_chat_fn": lambda _m: _LINE_REPLY,
        "doc_prompt": "p",
        "doc_prompt_tokens": 1000,
        "doc_model": build_document_schema(["kostel"]),
        "doc_chat_fn": lambda _m: _DOC_REPLY,
        "filter_params": {},
        "vocab_notes": {"line": standing, "document": standing},
    }
    monkeypatch.setattr(api, "_require_engine", lambda: eng)
    return eng


@pytest.fixture
def client():
    return TestClient(api.app)


def _text(client, text="Výzkum odhalil základy gotického kostela."):
    return client.post("/extract_keywords_text", json={"text": text})


def test_a_document_that_cannot_fit_one_call_is_413_before_any_call(client, engine, monkeypatch):
    calls = []
    engine["doc_chat_fn"] = lambda m: calls.append(m) or _DOC_REPLY
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "4000")  # 1000 prompt + 2048 reply leave ~950
    response = _text(client, "slovo " * 1000)  # ~1500 tokens
    assert response.status_code == 413
    body = response.json()
    assert body["reason"] == "limit_exceeded" and body["limit"]["env"] == "LLM_CONTEXT_WINDOW"
    assert body["limit"]["value"] == 4000 and body["limit"]["observed"] > 4000
    assert calls == []
    assert _text(client).status_code == 200


def test_a_cut_reply_in_document_mode_is_422_limit_exceeded(client, engine):
    engine["doc_chat_fn"] = _truncating_chat
    response = _text(client)
    assert response.status_code == 422
    body = response.json()
    assert body["reason"] == "limit_exceeded" and body["limit"]["key"] == "llm_max_new_tokens"


def test_exhausted_retries_in_document_mode_are_502_not_an_empty_200(client, engine):
    def failing(_m):
        raise RuntimeError("OpenRouter request failed after 3 attempts: HTTP 503")

    engine["doc_chat_fn"] = failing
    response = _text(client)
    assert response.status_code == 502 and "after 3 attempts" in response.json()["detail"]


def test_the_response_carries_the_standing_vocabulary_note(client, engine):
    body = _text(client).json()
    assert body["limits_applied"][0]["limit"] == "vocab_prompt_budget_tokens"
    assert body["limits_applied"][0]["count"] == 1257


def test_line_mode_notes_cut_replies_and_the_stop(client, engine, monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONSECUTIVE_ERRORS", "2")
    engine["line_chat_fn"] = _truncating_chat
    response = client.post(
        "/extract_keywords", files={"file": ("d.csv", _CSV.encode(), "text/csv")}
    )
    assert response.status_code == 200
    notes = {n["limit"]: n for n in response.json()["limits_applied"]}
    assert (notes["llm_max_new_tokens"]["effect"], notes["llm_max_new_tokens"]["count"]) == (
        "skipped",
        2,
    )
    assert notes["llm_max_consecutive_errors"]["effect"] == "stopped"
    assert "1 line(s) after them" in notes["llm_max_consecutive_errors"]["detail"]


def test_the_inline_text_body_is_bounded(client, engine, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.0001")
    response = _text(client, "slovo " * 100)
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large: over 0.0001 MB (MAX_UPLOAD_MB)."


def test_an_oversized_document_json_part_is_413(client, engine, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")
    files = {
        "file": ("d.csv", _CSV.encode(), "text/csv"),
        "document_json": ("d.document.json", b"{" + b" " * 4096 + b"}", "application/json"),
    }
    response = client.post("/extract_keywords", files=files)
    assert response.status_code == 413
    assert response.json()["detail"] == "document_json too large: over 0.001 MB (MAX_UPLOAD_MB)."


def test_info_reports_every_limit(client, monkeypatch):
    monkeypatch.setenv("LLM_MAX_NEW_TOKENS", "1024")
    data = client.get("/info").json()
    assert data["limits"] == tool_limits.LIMITS.values()
    assert data["limits"]["vocab_prompt_budget_tokens"] == tool_limits.context_window() - 1024 - 512
    assert data["limits_meta"]["vocab_prompt_budget_tokens"]["source"] == "derived"
    assert "vocabulary" in data


def test_the_engine_records_the_vocabulary_cut(monkeypatch, caplog):
    if not (REPO_ROOT / "data_samples" / "vocab" / "union_nested.json").is_file():
        pytest.skip("no shipped vocabulary here")
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "8000")
    with caplog.at_level("WARNING", logger="service.api"):
        eng = api._load_engine()
    vocab = eng["vocabulary"]
    assert vocab["line_prompt_terms"] < vocab["terms"]
    [note] = eng["vocab_notes"]["line"].as_list()
    assert (
        note["count"] == vocab["terms"] - vocab["line_prompt_terms"]
        and note["value"] == 8000 - 2048 - 512
    )
    assert "vocabulary terms left out" in caplog.text
    assert tool_limits.document_input_budget_tokens() == 8000 - 2048 - eng["doc_prompt_tokens"]
