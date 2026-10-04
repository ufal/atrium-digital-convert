"""Repo-local declarations for tests/test_openapi_contract.py (atrium-project#32 round 2).

Never vendored, never in para-drift, never in the ruff [format] exclude — unlike the
canonical test that reads it, this file's content is per repo by design: which services the
repo runs, where their committed specs live, which settings could reach a spec, and which
requirement files pin fastapi and pydantic. See the canonical test's docstring.

Since v1.1.0-beta the one service is the born-digital converter (atrium-digital-convert#2):
`/reformat` and `/describe`, published as `atrium-digital-convert` with the rename from
`atrium-llm-enrich` declared in the spec (`x-atrium-service-previous`).
"""

from __future__ import annotations

#: One entry per HTTP service of this repo. `primary`: the domain endpoints whose JSON 200
#: must be a named model (strategy §4.2).
SERVICES = [
    {
        "service": "atrium-digital-convert",
        "service_previous": "atrium-llm-enrich",
        "app": "service.api:app",
        "spec": "service/openapi.json",
        "primary": ["/reformat", "/describe"],
    },
]

#: Settings besides every [limit] variable (which the test perturbs from tool_limits.LIMITS)
#: that a deployment changes and that must not change the spec.
ENV_PERTURB = {
    "PAGE_CLASSIFICATION_URL": "http://page-classification.example:8000",
    "OCR_POSTPROCESS_URL": "http://ocr-postprocess.example:8000",
    "PAGE_CLASSIFICATION_VERSION": "v4.3",
    "PAGE_CLASSIFICATION_TOPN": "5",
    "LIBREOFFICE_BIN": "/usr/bin/soffice",
    "ALLOWED_ORIGINS": "https://example.org",
}

#: Every requirements file a lane or an image installs fastapi or pydantic from: the api image
#: (service/requirements.txt) and the light and docker-tool test lanes (requirements-test.txt).
PIN_FILES = ["service/requirements.txt", "requirements-test.txt"]

#: Run before the app is imported (``MODULE:FUNCTION``), or None: this service imports light.
PREPARE = None
