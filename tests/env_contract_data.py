"""Repo-local declarations for tests/test_env_contract.py (atrium-project#60).

Never vendored, never in para-drift, never in docs/templates/ruff.toml's [format]
exclude — unlike test_env_contract.py itself, this file's SHAPE is per-repo by
design: what one repo deliberately withholds from its ledger is not the same set as
what another does. See the canonical test's module docstring for the full rationale.
"""

from __future__ import annotations

# Read by shipped code but deliberately absent from .env.example, each with a reason.
NOT_PUBLISHED: dict[str, str] = {
    "DOCLING_ARTIFACTS_PATH": "read only by api_util/digital_docling.py (`--engine docling`), set by the Dockerfile's digital-docling stage; the api image carries no Docling",
    "STUB_PAGE_CATEGORY": "read only by tools/stage_stub.py, a local stand-in for page-classification that no image ships",
    "STUB_NEEDS_OCR_CATEGORY": "read only by tools/stage_stub.py, a local stand-in for page-classification that no image ships",
}

# In .env.example but read by no Python in this repo — each with a reason.
CONSUMED_ELSEWHERE: dict[str, str] = {
    "ATRIUM_VERSION": "read only by docker-compose.yaml to pick the image tag; no Python here reads it",
    "ATRIUM_UID": "read only by docker-compose.yaml (`user: ${ATRIUM_UID:-10001}:0`); no Python here reads it",
}

# service/README.md or .env.example cells whose value is prose rather than a literal
# the code-default resolver can compare against.
PROSE_DEFAULTS: dict[str, str] = {}
