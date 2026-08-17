"""Model-connection layer: endpoint dialects + model knowledge.

Single home for everything that varies between LLM endpoints and
models, so ``agent/factory.py`` stays a thin construction site:

- :mod:`~chaos_agent.llm.detect` — base_url → dialect (hostname-based,
  user-overridable, conservative default)
- :mod:`~chaos_agent.llm.compat` — declarative compat spec (data, not
  class hierarchies)
- :mod:`~chaos_agent.llm.models` — model-family predicates
- :mod:`~chaos_agent.llm.thinking` — semantic on/off → wire payload
- :mod:`~chaos_agent.llm.budgets` — per-model context budget table

This package must stay import-light and settings-free (pure logic):
``config/settings.py`` imports the budget table from here, so nothing
in this package may import settings at module level.
"""

from chaos_agent.llm.budgets import (
    AUX_THINKING_SKIP_MIN_WINDOW,
    DEFAULT_MODEL_BUDGETS,
    window_allows_thinking_skip,
)
from chaos_agent.llm.compat import (
    ALLOWED_THINKING_FORMATS,
    CompatSpec,
    ThinkingFormat,
)
from chaos_agent.llm.detect import (
    detect_compat,
    detect_thinking_format,
    parse_hostname,
    resolve_thinking_format,
)
from chaos_agent.llm.models import (
    is_qwen_family,
    is_tiered_effort_model,
    normalize_model,
)
from chaos_agent.llm.thinking import thinking_payload

__all__ = [
    "ALLOWED_THINKING_FORMATS",
    "AUX_THINKING_SKIP_MIN_WINDOW",
    "DEFAULT_MODEL_BUDGETS",
    "CompatSpec",
    "ThinkingFormat",
    "detect_compat",
    "detect_thinking_format",
    "is_qwen_family",
    "is_tiered_effort_model",
    "normalize_model",
    "parse_hostname",
    "resolve_thinking_format",
    "thinking_payload",
    "window_allows_thinking_skip",
]
