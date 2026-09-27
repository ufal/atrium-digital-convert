"""tool_limits.py — every limit atrium-llm-enrich has (atrium-project#53, factor III).

One declaration, read by the service (``service/api.py``) and by the lightweight clients it
drives (``openrouter_client.py``, ``ollama_client.py``, ``llm_client_shared.py``), and
reported by ``GET /info`` (``limits`` and ``limits_meta``). Each limit is an environment
setting; a malformed value stops the process at startup, naming the variable
(``atrium_limits.LimitConfigError``). ``.env.example`` and ``service/README.md``'s
``## Limits`` table list the same set; ``tests/test_limits_contract.py`` checks that they
agree.

**The context window's default depends on the backend** (atrium-project#53, D3): 128000
for ``openrouter``, 32000 for ``ollama`` — the same default each client's CLI uses
(:data:`BACKEND_CONTEXT_WINDOW`). The environment wins, then ``CONTEXT_WINDOW`` in the
config file ``LLM_CONFIG`` names (llm_config.txt), then that default. It is read when this
module is imported, so a malformed value fails the start instead of being recorded as a
warm-up failure the service keeps running with.

What happens over each limit — refused (with the HTTP status), or processed in full with a
``limits_applied`` note (with the effect) — is said beside it.

Standard library only (``atrium_limits`` is the hub's canonical module at the repo root):
the CLI clients import this too.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from atrium_limits import LimitConfigError, LimitSet, limit, upload_limit

_REPO_ROOT = Path(__file__).resolve().parent

#: The context window each backend's client assumes (its CLI's ``--context-window``
#: default, and the service's default for ``LLM_CONTEXT_WINDOW``).
BACKEND_CONTEXT_WINDOW: Dict[str, int] = {"openrouter": 128000, "ollama": 32000}
#: Tokens reserved for formatting on top of the reply (``LLM_MAX_NEW_TOKENS``) when the
#: prompt budget is computed. Not a setting: it is the prompt template's own overhead.
PROMPT_OVERHEAD_TOKENS = 512


def llm_backend() -> str:
    """The backend ``LLM_BACKEND`` selects (``openrouter`` by default)."""
    return (os.environ.get("LLM_BACKEND") or "openrouter").strip().lower()


#: §4.5 upload limit, per uploaded part (the file, `document_json`) and for the whole
#: `/extract_keywords_text` body. Over it → 413 ``limit_exceeded``.
MAX_UPLOAD = upload_limit(10)
#: The model's context window, in tokens. It sizes the vocabulary prompt (more terms than
#: fit are left out → standing ``trimmed`` note) and, in document mode, the document: one
#: that would not fit with the prompt and the reply → 413 ``limit_exceeded``. Sent to
#: Ollama as ``num_ctx``.
LLM_CONTEXT_WINDOW = limit(
    "LLM_CONTEXT_WINDOW",
    BACKEND_CONTEXT_WINDOW.get(llm_backend(), 32000),
    unit="tokens",
    minimum=1024,
)
#: Most tokens one reply may have (OpenRouter ``max_tokens``, Ollama ``num_predict``). A
#: reply cut at it is never used: document mode → 422 ``limit_exceeded``; line mode → the
#: line gets no result → ``skipped`` note.
LLM_MAX_NEW_TOKENS = limit("LLM_MAX_NEW_TOKENS", 2048, unit="tokens", minimum=16, status=422)
#: Per-request timeout of one LLM call, in seconds; a timeout is retried.
LLM_TIMEOUT = limit("LLM_TIMEOUT", 300, unit="s", minimum=1)
#: Attempts of one LLM call; only a timeout, a connection error, HTTP 429 or 5xx is
#: retried. Once they run out: document mode → 502; line mode → the line is an error.
LLM_MAX_RETRIES = limit("LLM_MAX_RETRIES", 3, unit="attempts", minimum=1)
#: Line mode: consecutive failed lines after which the document is given up → ``stopped``
#: note (the lines before it keep their results).
LLM_MAX_CONSECUTIVE_ERRORS = limit("LLM_MAX_CONSECUTIVE_ERRORS", 10, unit="errors", minimum=1)


def config_path() -> Path:
    """The config file the service reads (``LLM_CONFIG``, default llm_config.txt)."""
    path = Path(os.environ.get("LLM_CONFIG") or "llm_config.txt")
    return path if path.is_absolute() or path.exists() else _REPO_ROOT / path


def config_values() -> Dict[str, str]:
    """``{LLM_CONTEXT_WINDOW: raw}`` when the config file sets ``CONTEXT_WINDOW``.

    Same KEY=VALUE reading as ``llm_client_shared.load_config`` (blank lines and ``#``
    comments skipped, one matched pair of quotes removed), without its dependencies.
    """
    try:
        lines = config_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "CONTEXT_WINDOW":
            value = value.strip()
            for quote in ('"', "'"):
                if len(value) >= 2 and value[0] == value[-1] == quote:
                    value = value[1:-1]
            return {LLM_CONTEXT_WINDOW.env: value} if value else {}
    return {}


def context_window() -> int:
    """The effective context window: environment, config file, backend default."""
    return LIMITS.get(LLM_CONTEXT_WINDOW.key)


def reserved_tokens() -> int:
    """Tokens kept free for the reply and the prompt's formatting."""
    return LLM_MAX_NEW_TOKENS.get() + PROMPT_OVERHEAD_TOKENS


def vocab_prompt_budget_tokens() -> int:
    """Tokens the vocabulary prompt may take (estimated at 4 characters per token)."""
    return context_window() - reserved_tokens()


#: What the service measured once its prompts were built (``set_prompt_facts``).
_PROMPT_FACTS: Dict[str, Any] = {}


def set_prompt_facts(**facts: Any) -> None:
    _PROMPT_FACTS.clear()
    _PROMPT_FACTS.update(facts)


def document_input_budget_tokens() -> Optional[int]:
    """Tokens a document may have in document mode: the window, less the reply and the
    document prompt (the vocabulary). ``None`` until the service has built its prompts."""
    prompt = _PROMPT_FACTS.get("document_prompt_tokens")
    if prompt is None:
        return None
    return max(0, context_window() - LLM_MAX_NEW_TOKENS.get() - prompt)


LIMITS = LimitSet(
    MAX_UPLOAD,
    LLM_CONTEXT_WINDOW,
    LLM_MAX_NEW_TOKENS,
    LLM_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_MAX_CONSECUTIVE_ERRORS,
    config=config_values,
)
LIMITS.derived(
    "vocab_prompt_budget_tokens",
    vocab_prompt_budget_tokens,
    unit="tokens",
    derived_from=["LLM_CONTEXT_WINDOW", "LLM_MAX_NEW_TOKENS"],
)
LIMITS.derived(
    "document_input_budget_tokens",
    document_input_budget_tokens,
    unit="tokens",
    derived_from=["LLM_CONTEXT_WINDOW", "LLM_MAX_NEW_TOKENS", "VOCAB_PATH"],
)

if vocab_prompt_budget_tokens() <= 0:
    raise LimitConfigError(
        f"LLM_CONTEXT_WINDOW is {context_window()} tokens, which leaves no room for the prompt "
        f"after LLM_MAX_NEW_TOKENS ({LLM_MAX_NEW_TOKENS.get()}) and {PROMPT_OVERHEAD_TOKENS} tokens "
        "of formatting; raise the first or lower the second."
    )
