"""Thinking-switch dialect translation (semantic on/off → wire payload).

Locks the bug that motivated this module: the disable field must
actually SHIP for thinking-by-default models (qwen3.8-max) — an absent
flag silently leaves thinking on. Also locks the family gate: vendor
knobs never leak to non-qwen models served through a qwen endpoint.
"""

import pytest

from chaos_agent.llm import ThinkingFormat, thinking_payload

QWEN = ThinkingFormat.QWEN


class TestNoneFormat:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_unknown_endpoint_ships_nothing(self, enabled):
        assert thinking_payload("qwen3.8-max", ThinkingFormat.NONE, enabled) == {}
        assert thinking_payload("gpt-5", ThinkingFormat.NONE, enabled) == {}


class TestQwenDialect:
    def test_tiered_model_maps_on_off_to_tiers(self):
        assert thinking_payload("qwen3.8-max", QWEN, True) == {
            "reasoning_effort": "high",
        }
        # 'none' is the documented disable and MUST ship explicitly.
        assert thinking_payload("qwen3.8-max", QWEN, False) == {
            "reasoning_effort": "none",
        }

    def test_tiered_model_prefix_covers_snapshots(self):
        assert thinking_payload("qwen3.8-max-latest", QWEN, False) == {
            "reasoning_effort": "none",
        }

    def test_hybrid_model_uses_boolean_switch(self):
        assert thinking_payload("qwen3.7-plus", QWEN, True) == {
            "enable_thinking": True,
        }
        assert thinking_payload("qwen3.7-plus", QWEN, False) == {
            "enable_thinking": False,
        }

    @pytest.mark.parametrize("model", ["glm-4.6", "deepseek-chat", "", None])
    def test_family_gate_blocks_cross_family_leak(self, model):
        """A non-qwen model on a dashscope endpoint gets no knob."""
        assert thinking_payload(model, QWEN, True) == {}
        assert thinking_payload(model, QWEN, False) == {}

    def test_model_name_is_case_insensitive(self):
        assert thinking_payload("Qwen3.8-Max", QWEN, False) == {
            "reasoning_effort": "none",
        }


class TestOpenaiDialect:
    @pytest.mark.parametrize("model", ["gpt-5", "o3", "anything"])
    def test_reasoning_effort_tiers(self, model):
        assert thinking_payload(model, ThinkingFormat.OPENAI, True) == {
            "reasoning_effort": "high",
        }
        assert thinking_payload(model, ThinkingFormat.OPENAI, False) == {
            "reasoning_effort": "none",
        }


class TestDeepseekDialect:
    def test_thinking_type_object(self):
        assert thinking_payload("deepseek-chat", ThinkingFormat.DEEPSEEK, True) == {
            "thinking": {"type": "enabled"},
        }
        assert thinking_payload("deepseek-chat", ThinkingFormat.DEEPSEEK, False) == {
            "thinking": {"type": "disabled"},
        }


class TestZaiDialect:
    """Z.AI wire shape follows openclaw's live translation: a
    ``thinking`` object with a ``clear_thinking`` companion — NOT the
    top-level boolean the old interface doc claimed."""

    def test_enabled_ships_clear_thinking_companion(self):
        assert thinking_payload("glm-4.6", ThinkingFormat.ZAI, True) == {
            "thinking": {"type": "enabled", "clear_thinking": False},
        }

    def test_disabled_ships_type_disabled(self):
        assert thinking_payload("glm-4.6", ThinkingFormat.ZAI, False) == {
            "thinking": {"type": "disabled"},
        }


class TestOpenrouterDialect:
    def test_nested_reasoning_effort(self):
        assert thinking_payload("anthropic/claude-x", ThinkingFormat.OPENROUTER, True) == {
            "reasoning": {"effort": "high"},
        }
        assert thinking_payload("anthropic/claude-x", ThinkingFormat.OPENROUTER, False) == {
            "reasoning": {"effort": "none"},
        }


class TestTogetherDialect:
    def test_nested_reasoning_enabled(self):
        assert thinking_payload("deepseek-r1", ThinkingFormat.TOGETHER, True) == {
            "reasoning": {"enabled": True},
        }
        assert thinking_payload("deepseek-r1", ThinkingFormat.TOGETHER, False) == {
            "reasoning": {"enabled": False},
        }


class TestQwenChatTemplateDialect:
    """Local vLLM/sglang deployments (openclaw's live shape, verbatim).
    Never URL-detected — only reachable via the config override."""

    def test_chat_template_kwargs_on(self):
        assert thinking_payload(
            "qwen3.8-max", ThinkingFormat.QWEN_CHAT_TEMPLATE, True
        ) == {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
            },
        }

    def test_chat_template_kwargs_off(self):
        assert thinking_payload(
            "qwen3.8-max", ThinkingFormat.QWEN_CHAT_TEMPLATE, False
        ) == {
            "chat_template_kwargs": {
                "enable_thinking": False,
                "preserve_thinking": True,
            },
        }


class TestAllowedFormats:
    def test_new_dialects_are_config_overridable(self):
        from chaos_agent.llm import ALLOWED_THINKING_FORMATS

        for fmt in ("zai", "openrouter", "together", "qwen-chat-template"):
            assert fmt in ALLOWED_THINKING_FORMATS
