"""Declarative compatibility spec for OpenAI-compatible LLM endpoints.

Design reference: openclaw's ``OpenAICompletionsCompat`` — vendor
differences are expressed as *data* (dialect enums + feature flags),
not per-provider class hierarchies. Parametric differences stay
declarative and user-overridable; only genuinely behavioural quirks
would ever need code hooks (none exist today).

Currently the only dialect dimension we need is the thinking/reasoning
switch, whose wire field is vendor-specific even within the same
OpenAI-compatible protocol:

- Qwen / DashScope: top-level ``enable_thinking: bool`` (older hybrid
  models) or tiered ``reasoning_effort`` (qwen3.8-max family)
- OpenAI: ``reasoning_effort``
- DeepSeek: ``thinking: {"type": "enabled" | "disabled"}``
- Z.AI (GLM): ``thinking: {"type": ..., "clear_thinking": ...}``
- OpenRouter: nested ``reasoning: {"effort": ...}``
- Together: nested ``reasoning: {"enabled": bool}``
- Qwen chat-template (local vLLM/sglang): ``chat_template_kwargs``

Unknown endpoints get :attr:`ThinkingFormat.NONE` — no dialect field
is sent at all, which is the only universally-safe choice (strict
endpoints 400 on unknown parameters; lenient ones silently ignore
them, so sending a wrong dialect buys nothing but risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ThinkingFormat(str, Enum):
    """Wire dialect for the thinking/reasoning switch."""

    #: Do not send any thinking-related field (unknown endpoints).
    NONE = "none"
    #: Qwen / DashScope: ``enable_thinking`` (hybrid models) or
    #: tiered ``reasoning_effort`` (qwen3.8-max family).
    QWEN = "qwen"
    #: OpenAI-style ``reasoning_effort`` tier.
    OPENAI = "openai"
    #: DeepSeek: ``thinking: {"type": ...}``.
    DEEPSEEK = "deepseek"
    #: Z.AI (GLM): ``thinking: {"type": ..., "clear_thinking": ...}``.
    ZAI = "zai"
    #: OpenRouter: nested ``reasoning: {"effort": ...}``.
    OPENROUTER = "openrouter"
    #: Together: nested ``reasoning: {"enabled": bool}``.
    TOGETHER = "together"
    #: Qwen served via a local vLLM/sglang chat template:
    #: ``chat_template_kwargs: {"enable_thinking": bool,
    #: "preserve_thinking": true}``. Deliberately has NO URL
    #: auto-detection — local endpoints carry arbitrary hostnames, so
    #: this dialect is only ever selected via ``llm_thinking_format``.
    QWEN_CHAT_TEMPLATE = "qwen-chat-template"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(m.value for m in cls)


#: Values accepted by ``Settings.llm_thinking_format`` (plus "auto").
ALLOWED_THINKING_FORMATS: frozenset[str] = frozenset(
    {"auto", *ThinkingFormat.values()}
)


@dataclass(frozen=True)
class CompatSpec:
    """Resolved compatibility profile for one endpoint.

    Kept deliberately tiny — grow new dimensions (max-tokens field,
    role naming, cache-control format, ...) as flags here instead of
    forking request builders per provider.
    """

    thinking_format: ThinkingFormat = ThinkingFormat.NONE
