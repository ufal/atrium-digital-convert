"""tests/test_startup_log.py — a clean start logs one record in the agreed shape (atrium-project#61).

The hub's image smoke test (`docker-tool.reusable.yml`) waits for the container to report
healthy, then looks in `docker logs` for a line shaped
`%(asctime)s %(levelname)s %(name)s %(message)s` at the default LOG_LEVEL. uvicorn's own lines
have their own format, so the record has to come from this service. The LLM service logged its
warmup; the converter has no warmup, and until this startup record a healthy container logged
nothing at INFO, so the smoke test failed on a working image.

These tests run the real `lifespan` (a `TestClient` used as a context manager) and format the
record with the entrypoint's own format string, so the pattern below is checked against the line
the container would print.
"""

from __future__ import annotations

import logging
import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from service import api  # noqa: E402

#: The entrypoint's `logging.basicConfig` format (service/api.py `__main__`).
AGREED_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
#: The pattern the smoke test greps `docker logs` for.
SMOKE_PATTERN = re.compile(r"^[0-9-]+ [0-9:,]+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) [^ ]+ ")


@pytest.fixture
def start(caplog, monkeypatch):
    """Run the app's startup and shutdown once; return this module's records."""
    monkeypatch.setattr(api._state, "warm", api._state.warm)  # restored after the test

    def run():
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=api.logger.name):
            with TestClient(api.app):
                pass
        return [record for record in caplog.records if record.name == api.logger.name]

    return run


def test_a_clean_start_logs_one_info_record_in_the_agreed_shape(start, monkeypatch):
    monkeypatch.setattr(api, "_deep_health", lambda: None)
    records = start()
    ready = [r for r in records if r.levelno == logging.INFO and " ready: " in r.getMessage()]
    assert len(ready) == 1, [r.getMessage() for r in records]
    line = logging.Formatter(AGREED_FORMAT).format(ready[0])
    assert SMOKE_PATTERN.match(line), line
    assert api.app.version in ready[0].getMessage()
    assert api._state.warm is True


def test_the_startup_record_names_the_configured_stages_never_their_urls(start, monkeypatch):
    monkeypatch.setattr(api, "_deep_health", lambda: None)
    monkeypatch.setenv("PAGE_CLASSIFICATION_URL", "http://pc.internal.example:8000")
    monkeypatch.delenv("OCR_POSTPROCESS_URL", raising=False)
    message = next(r.getMessage() for r in start() if " ready: " in r.getMessage())
    assert "stages configured: page-classification" in message
    assert "ocr-postprocess" not in message
    assert "internal.example" not in message


def test_no_configured_stage_is_reported_as_none(start, monkeypatch):
    monkeypatch.setattr(api, "_deep_health", lambda: None)
    monkeypatch.delenv("PAGE_CLASSIFICATION_URL", raising=False)
    monkeypatch.delenv("OCR_POSTPROCESS_URL", raising=False)
    message = next(r.getMessage() for r in start() if " ready: " in r.getMessage())
    assert message.endswith("stages configured: none")


def test_a_failed_deep_check_logs_a_warning_and_no_ready_record(start, monkeypatch):
    monkeypatch.setattr(api, "_deep_health", lambda: "converter dependencies missing: lxml")
    records = start()
    assert [r.levelno for r in records] == [logging.WARNING]
    line = logging.Formatter(AGREED_FORMAT).format(records[0])
    assert SMOKE_PATTERN.match(line), line
    assert "not ready: converter dependencies missing: lxml" in records[0].getMessage()
    assert api._state.warm is False
