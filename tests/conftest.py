"""
tests/conftest.py
=================
Shared pytest fixtures and sys.path wiring for atrium-digital-convert's tests.

sys.path is patched here (once, at collection time) so that every test module can import from
both the repo root (``atrium_document.py``, ``tool_limits.py``, the vendored ``text_formats.py``)
and the ``api_util/`` subdirectory.

The remote-client scaffolding that lived here (``remote_client_env``, ``seeded_baseline``,
``stub_llm``) left with the LLM keyword code for atrium-keyword-extract (#1).
"""

import sys
from pathlib import Path


def pytest_configure(config):
    # `slow` is declared in pytest.ini; this stays as the belt-and-braces registration for
    # anyone invoking pytest with a different ini (e.g. `-c` in a container), and is
    # harmless when both are present. With --strict-markers now on, one of the two has to
    # be authoritative — pytest.ini is.
    config.addinivalue_line("markers", "slow: marks tests as slow integration smoke tests")


# ── path wiring ───────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "api_util"))
