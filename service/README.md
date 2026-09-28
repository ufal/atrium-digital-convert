# llm-enrich API service 🧠

LLM-based archaeological keyword extraction: text lines / documents in → per-line (or
per-document) `extracted_keywords_cs` / `extracted_keywords_en` out. The service version is
read from `para_config.txt` `[tool]` (single source of truth, never hard-coded).

It wraps the **torch-free** remote / lightweight-local engine (`llm_client_shared` +
`openrouter_client` / `ollama_client`), so it never needs the GPU stack.

## Quick start

```bash
pip install -r service/requirements.txt

# choose a backend and give it a key/model, then launch:
export LLM_BACKEND=openrouter OPENROUTER_API_KEY=sk-... OPENROUTER_MODEL=openai/gpt-4o-mini
python -m service.api                     # honours PORT/HOST; default 0.0.0.0:8000
# or, for development with auto-reload:
uvicorn service.api:app --host 0.0.0.0 --port 8000
# or:
docker compose --profile api up -d
```

Without a configured backend the service still starts: `/info` and `/health` respond and
report `ready: false`, while the extraction endpoints return `503` until configured.

## Endpoints

| Method | Path                     | Purpose                                                                                                                                                                                                                                                                            |
|--------|--------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GET    | `/info`                  | service identity + capabilities: `service`, `version`, `endpoints`, `limits` (every [limit](#limits), current value), `limits_meta` (the variable behind each), `vocabulary` (terms, and how many reach each prompt), `backend`, `model`, `ready`, `supported_inputs`, `languages` |
| GET    | `/health`                | liveness probe — 200 always, even mid-shutdown. `?deep=true` additionally checks the backend is configured (503 on fail or while draining)                                                                                                                                         |
| GET    | `/ready`                 | readiness probe (issue #55) — 503 until the backend is serviceable, 200 while serving, 503 the instant `SIGTERM` arrives. The Kubernetes `readinessProbe`/`startupProbe` target                                                                                                    |
| POST   | `/extract_keywords`      | extract keywords from an uploaded document                                                                                                                                                                                                                                         |
| POST   | `/extract_keywords_text` | extract keywords from an inline JSON `{"text": "...", "document_json": {...}}` body (document mode)                                                                                                                                                                                |

### `POST /extract_keywords` (multipart form)

| Field           | Default    | Notes                                                                                                                                                                                                          |
|-----------------|------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `file`          | *required* | `.csv` / `*.teitok.xml` → line-level; `.md` / `.txt` → document-level. Any other suffix → 415 `unsupported_media_type`                                                                                         |
| `document_json` | —          | optional record or AMČR seed (JSON); comes back as `document_json` with only the `enrichment` block updated. Not a JSON object → 422 `invalid_record`; schema-invalid → accepted, `document_json_schema_error` |

```bash
curl -X POST "http://localhost:8000/extract_keywords" -F "file=@sample.csv"
curl -X POST "http://localhost:8000/extract_keywords_text" \
     -H "Content-Type: application/json" -d '{"text": "Výzkum odhalil základy gotického kostela."}'
curl -s http://localhost:8000/info
```

### Response schema

```json
{
  "service": "atrium-llm-enrich",
  "doc_id": "sample",
  "backend": "openrouter",
  "model": "openai/gpt-4o-mini",
  "mode": "line",
  "results": [
    {
      "file_id": "input",
      "page": 1,
      "line": 1,
      "categ": "Clear",
      "quality_score": 0.9,
      "original_text": "Výzkum odhalil základy gotického kostela.",
      "enrichment": {
        "extracted_keywords_cs": ["základy", "gotický kostel"],
        "extracted_keywords_en": ["foundations", "Gothic church"],
        "teater_category": "kostel",
        "confidence_score": 0.9
      }
    }
  ],
  "stats": {"processed": 1, "skipped_filter": 0, "skipped_error": 0, "aborted": 0, "attempted": 1, "truncated": 0},
  "limits_applied": []
}
```

| Field                        | Type           | Description                                                                                                                                                                              |
|------------------------------|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `service`                    | str            | canonical tool id (`atrium-llm-enrich`)                                                                                                                                                  |
| `doc_id`                     | str            | document id: from the upload filename, or the sent record's `doc_id` (`/extract_keywords_text`)                                                                                          |
| `backend`                    | str            | active LLM backend (`openrouter` / `ollama`)                                                                                                                                             |
| `model`                      | str            | model id (`<model>@<host>` for Ollama)                                                                                                                                                   |
| `mode`                       | str            | `line` (CSV/TEITOK) or `document` (MD/TXT, inline text)                                                                                                                                  |
| `results`                    | list           | line mode: `file_id`, `page` (int), `line` (int), `categ`, `quality_score`, `original_text`, `enrichment`; document mode: `file_id`, `locator`, `page` (label or null), `enrichment`     |
| `stats`                      | object         | `processed`, `skipped_filter`, `skipped_error`, `aborted`, `attempted`; line mode adds `truncated` (and `unprocessed` after an abort); document mode may add `repaired`, `dropped_items` |
| `limits_applied`             | list           | every [limit](#limits) that shaped the result (below)                                                                                                                                    |
| `document_json`              | object         | only when a record was sent and the run contributed: the record, with the `enrichment` block updated                                                                                     |
| `document_json_schema_error` | str            | only when the returned record does not validate (the sent record's problem)                                                                                                              |
| `paradata`                   | object or null | reserved for the run's `CreateAction` (atrium-project#67 R2); not returned yet                                                                                                           |

The exact types — every field above, the `enrichment` object and the record — are in
[`openapi.json`](openapi.json) ([OpenAPI](#openapi-the-typed-contract)).

`limits_applied` (a list) names every [limit](#limits) that shaped the result without
refusing it (atrium-project#53): the vocabulary terms left out of the prompt
(`vocab_prompt_budget_tokens`, `trimmed` — present on every response while the vocabulary does
not fit), lines whose reply was cut (`llm_max_new_tokens`, `skipped`), a document given up after
too many failed lines (`llm_max_consecutive_errors`, `stopped`). `[]` when no limit applied.

## Errors

Every error has one JSON body (hub `docs/agent_skill_strategy.md` §4.4, atrium-project#32
item 2): `{"status": <int>, "reason": <code or null>, "detail": "<text>"}`. `detail` is always
a string. A limit refusal adds `limit` (`{key, env, value, observed, unit}`); a request
validation error adds FastAPI's list of problems as `errors`.

| Code | `reason`                 | Meaning                                                                                                                                    |
|------|--------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| 413  | `limit_exceeded`         | over `MAX_UPLOAD_MB`, or (document mode) a document that does not fit `LLM_CONTEXT_WINDOW` with the prompt and the reply                   |
| 415  | `unsupported_media_type` | the file is not `.csv`, `.teitok.xml`, `.md` or `.txt`; `accepted` lists the suffixes (it was a bare 422 before atrium-project#32 round 2) |
| 422  | `invalid_record`         | the `document_json` sent is not UTF-8 JSON, not an object, or has a newer `schema_version` major — refused before any model call           |
| 422  | `limit_exceeded`         | document mode: the model's reply was cut at `LLM_MAX_NEW_TOKENS` — split the document, or send it as lines                                 |
| 422  | `null`                   | unusable input (missing filename, malformed CSV or TEITOK — a 500 before round 2 — no lines), or request validation (`errors`)             |
| 500  | `null`                   | processing failure                                                                                                                         |
| 502  | `null`                   | upstream LLM backend error: retries exhausted, or the provider refused the request (its reply is in `detail`)                              |
| 503  | `null`                   | backend not configured / not ready, or the replica is shutting down (client retries)                                                       |

## Configuration (environment)

| Variable              | Default                  | Meaning                                                                                   |
|-----------------------|--------------------------|-------------------------------------------------------------------------------------------|
| `PORT`                | `8000`                   | port the service **binds**, and the one `service/healthcheck.py` probes (issues #55, #58) |
| `HOST`                | `0.0.0.0`                | bind address (issue #58). ⚠️ see the warning below                                        |
| `GRACEFUL_SHUTDOWN_S` | `20`                     | seconds uvicorn waits for in-flight requests (issue #55)                                  |
| `RELOAD`              | `false`                  | filesystem auto-reload — development only                                                 |
| `LOG_LEVEL`           | `INFO`                   | root logger level for the `python -m service.api` start path (issue #61)                  |
| `ALLOWED_ORIGINS`     | `*`                      | CSV of CORS origins                                                                       |
| `LLM_BACKEND`         | `openrouter`             | `openrouter` or `ollama`                                                                  |
| `OPENROUTER_API_KEY`  | —                        | **required, secret** — key for the OpenRouter backend                                     |
| `OPENROUTER_MODEL`    | —                        | **required** — OpenRouter model id                                                        |
| `OLLAMA_HOST`         | `http://localhost:11434` | Ollama server URL                                                                         |
| `OLLAMA_MODEL`        | —                        | **required** for the ollama backend — Ollama model tag                                    |

Every limit — `MAX_UPLOAD_MB`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_CONTEXT_WINDOW` and the
rest — is listed under [Limits](#limits).

`PORT` and `HOST` are read by `service/api.py`'s `__main__` block, which is what the `api`
image's `ENTRYPOINT` (`python -m service.api`) runs. Before issue #58 the entrypoint baked
`--port 8000` into an exec-form array — which runs no shell, so `$PORT` could not expand —
while `service/healthcheck.py` read it. Setting `PORT` therefore moved the health *probe*
and not the listener, and the container reported unhealthy forever.

> ⚠️ `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`, so a
> loopback bind passes every probe while being unreachable from outside the container.


## Limits

Every limit is an environment setting (atrium-project#53, factor III), declared in
`tool_limits.py` and reported with its current value by `GET /info` (`limits`; `limits_meta`
says which variable sets it and whether the value came from the environment, `llm_config.txt`
or the default). A malformed value stops the service at startup, naming the variable. An input
over a limit is refused with the [harmonised error](#errors); a limit that shapes a result
without refusing it is named in `limits_applied`. `tests/test_limits_contract.py` checks this
table against `tool_limits.py` and `.env.example`.

| Key (`/info`)                  | Variable                                                        | Default | Unit     | Over the limit                                                                                                                                                                                                                                                                                                                                                                          |
|--------------------------------|-----------------------------------------------------------------|---------|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`                | `MAX_UPLOAD_MB`                                                 | 10      | MB       | 413 `limit_exceeded` — per part (the file, `document_json`) and for the whole `/extract_keywords_text` body                                                                                                                                                                                                                                                                             |
| `llm_context_window`           | `LLM_CONTEXT_WINDOW`                                            | 128000  | tokens   | sizes the vocabulary prompt (terms that do not fit are left out — standing `trimmed` note) and, in document mode, the document: one that does not fit with the prompt and the reply → 413 `limit_exceeded`. The default depends on the backend — 128000 `openrouter`, 32000 `ollama`, as in each client's CLI — after `CONTEXT_WINDOW` in `llm_config.txt`; sent to Ollama as `num_ctx` |
| `llm_max_new_tokens`           | `LLM_MAX_NEW_TOKENS`                                            | 2048    | tokens   | a reply cut at it is never used: document mode → 422 `limit_exceeded`; line mode → the line gets no result, `skipped` note. OpenRouter `max_tokens`, Ollama `num_predict`                                                                                                                                                                                                               |
| `llm_timeout`                  | `LLM_TIMEOUT`                                                   | 300     | s        | the call is retried (`LLM_MAX_RETRIES`)                                                                                                                                                                                                                                                                                                                                                 |
| `llm_max_retries`              | `LLM_MAX_RETRIES`                                               | 3       | attempts | only a timeout, a connection error, HTTP 429 or 5xx is retried; once they run out: document mode → 502, line mode → that line is an error                                                                                                                                                                                                                                               |
| `llm_max_consecutive_errors`   | `LLM_MAX_CONSECUTIVE_ERRORS`                                    | 10      | errors   | line mode: the document is given up, `stopped` note (the lines before keep their results)                                                                                                                                                                                                                                                                                               |
| `vocab_prompt_budget_tokens`   | — (derived: `LLM_CONTEXT_WINDOW` − `LLM_MAX_NEW_TOKENS` − 512)  | —       | tokens   | vocabulary terms past it are left out of the prompt — standing `trimmed` note, and a warning at startup                                                                                                                                                                                                                                                                                 |
| `document_input_budget_tokens` | — (derived: the window, less the reply and the document prompt) | —       | tokens   | document mode: a longer document → 413 `limit_exceeded`; `null` until the engine is warm                                                                                                                                                                                                                                                                                                |

Token counts here are estimates at 4 characters per token (`llm_client_shared.approx_token_count`),
the same estimate that sizes the vocabulary prompt. The vocabulary shares the window with the
document: with a window too small for both, `document_input_budget_tokens` is small, and a
document over it is refused rather than sent and answered with no results.

## How it works

On startup the service loads `llm_config.txt` + the archaeological vocabulary, builds the
system prompt and Pydantic schema once, and binds a `chat_fn` to the chosen backend. Each
request writes the upload to a temp file and calls `llm_client_shared.run_line_level` (CSV/
TEITOK) or `run_document_level` (MD/TXT) in a threadpool, so the event loop stays responsive.
Backend warmup failures are recorded rather than fatal, keeping `/info` and `/health` live.

## Shutdown behavior (issue #55)

The `api` image declares `HEALTHCHECK` (shallow `GET /health` via the vendored
`service/healthcheck.py`) and `STOPSIGNAL SIGTERM`, and sets `ENV GRACEFUL_SHUTDOWN_S=20`,
which `service/api.py`'s `__main__` block passes to uvicorn as
`timeout_graceful_shutdown`. (It was the `--timeout-graceful-shutdown 20` CLI flag until
issue #58 moved the whole start command into that block so `$PORT` could be honoured.)

On `SIGTERM` the service flips `GET /ready` to **503** at once (so an orchestrator stops
routing to it), starts refusing new work in `_require_engine()` with a 503, and lets
uvicorn finish in-flight requests before exiting. `GET /health` deliberately stays 200
throughout — a liveness probe failing mid-shutdown would get the container killed before
the drain completed.

⚠️ llm-enrich's slow work happens **inside** the request: one remote LLM call per line,
each bounded by `LLM_TIMEOUT` (default 300s). A large document can therefore legitimately
run longer than the 20s drain budget and be cut short. Raise both
`GRACEFUL_SHUTDOWN_S` and the deployment's grace period together for that
workload — see `docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured
signal on purpose so a supervisor sees the real cause. That is a normal stop, not a crash.

## OpenAPI (the typed contract)

The service's OpenAPI document is committed as [`service/openapi.json`](openapi.json) and
attached to every release as `openapi.json` with its `openapi.json.sha256` (atrium-project#32
round 2). It is what a client is generated from: every request and response field is typed,
every error response is the `ErrorBody` above, the registered `reason` codes are listed in
`x-atrium-reason-codes`, and a returned record is typed by the vendored record schema
(`AtriumDocument`). `GET /info` reports `openapi_sha256`, the digest of the spec the running
image serves — equal to the release's `openapi.json.sha256` for an image built from that tag.

- **After an API change**, regenerate and commit it:
  `python atrium_openapi.py export --app service.api:app --out service/openapi.json`.
  `tests/test_openapi_contract.py` fails while it is stale.
- **Compatibility.** Each release compares its spec with the previous release's
  (`release.yml`, `atrium_openapi.py compare` with oasdiff): a breaking change fails the
  release unless the major version went up (for 0.x, that means 1.0), and a removed reason
  code always fails. New fields, endpoints and reason codes are additive.
- **fastapi and pydantic are pinned** exactly (`service/requirements.txt`,
  `requirements-test.txt`): the spec is generated by them. Bump both by hand and regenerate.

## Tests

`tests/test_api_contract.py` (hermetic, `importorskip("fastapi")`) asserts the §4 meta-contract
against the in-process app, and drives both endpoints with a canned engine to hold every
response — 200s and refusals — to the published schema. `tests/test_openapi_contract.py`
(vendored from the hub) checks the committed spec itself. Run:
`pytest -m "not slow" tests/test_api_contract.py tests/test_openapi_contract.py`.
