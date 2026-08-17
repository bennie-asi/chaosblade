"""Translate the semantic thinking switch into the vendor wire payload.

Callers express intent only (thinking on/off); this module owns the
dialect translation. Empirical basis (task-7b3c554a replay, see
scripts/bench_thinking.py): on dashscope, an *explicit* disable cuts
aux-call latency by 6-75x (baseline derive 264.6s -> 3.5s, postmortem
130s -> 19.7s) with no quality loss — but only when the disable field
actually ships. The legacy factory injected the knob only when True,
so thinking models silently fell back to their thinking-on default.
"""

from __future__ import annotations

from chaos_agent.llm.compat import ThinkingFormat
from chaos_agent.llm.models import is_qwen_family, is_tiered_effort_model


def thinking_payload(
    model: str | None,
    thinking_format: ThinkingFormat,
    enabled: bool,
) -> dict:
    """Return the ``extra_body`` dict for the thinking switch.

    An empty dict means "send nothing" — the universal safe default for
    unknown endpoints and for cross-family requests (e.g. a non-qwen
    model served through a dashscope-compatible gateway).
    """
    if thinking_format is ThinkingFormat.NONE:
        return {}

    if thinking_format is ThinkingFormat.QWEN:
        # Family gate: vendor knobs never leak to non-qwen models
        # sharing the endpoint.
        if not is_qwen_family(model):
            return {}
        if is_tiered_effort_model(model):
            # qwen3.8-max family reads the tier directly; 'none' is the
            # documented disable and must ship explicitly.
            return {"reasoning_effort": "high" if enabled else "none"}
        return {"enable_thinking": bool(enabled)}

    if thinking_format is ThinkingFormat.OPENAI:
        return {"reasoning_effort": "high" if enabled else "none"}

    if thinking_format is ThinkingFormat.DEEPSEEK:
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}

    # Z.AI / OpenRouter / Together: endpoint-native dialects. Unlike the
    # QWEN branch there is no model-family gate — these endpoints only
    # serve their own models, so the hostname detection already implies
    # the family. ZAI wire shape: openclaw's live translation ships a
    # ``thinking`` object with a ``clear_thinking`` companion, and the
    # z.ai official docs (docs.z.ai/guides/capabilities/thinking)
    # confirm ``thinking.type`` is the on/off switch across ALL GLM
    # thinking models incl. GLM-5.2 — ``reasoning_effort`` there is an
    # extra TIER knob (GLM-5.2+ only: none/minimal skip thinking), not
    # a replacement switch, so the binary on/off maps onto thinking.type.
    if thinking_format is ThinkingFormat.ZAI:
        if enabled:
            return {"thinking": {"type": "enabled", "clear_thinking": False}}
        return {"thinking": {"type": "disabled"}}

    if thinking_format is ThinkingFormat.OPENROUTER:
        return {"reasoning": {"effort": "high" if enabled else "none"}}

    if thinking_format is ThinkingFormat.TOGETHER:
        return {"reasoning": {"enabled": bool(enabled)}}

    if thinking_format is ThinkingFormat.QWEN_CHAT_TEMPLATE:
        # Local vLLM/sglang serving qwen chat templates (openclaw's
        # live shape, verbatim): the Jinja template reads
        # enable_thinking; preserve_thinking keeps prior-round thinking
        # blocks on message replay. Never URL-detected — selected via
        # the llm_thinking_format override only.
        return {
            "chat_template_kwargs": {
                "enable_thinking": bool(enabled),
                "preserve_thinking": True,
            }
        }

    return {}
