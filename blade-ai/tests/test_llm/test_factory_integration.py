"""Factory integration: make_llm / with_thinking_disabled wire payloads.

Locks the regression that motivated the llm/ module: the thinking
switch used to be a fake toggle (the knob only shipped when True), so
``enable_thinking=False`` on a thinking-by-default model left the full
reasoning path running — a 6-75x latency penalty measured on real
aux calls (scripts/bench_thinking.py).
"""

import pytest

from chaos_agent.config.settings import settings

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def dashscope_qwen(monkeypatch):
    """Point the global settings singleton at dashscope + a tiered model.

    make_llm reads these attributes at call time, so attribute-level
    monkeypatching is sufficient and restores itself after each test.
    """
    monkeypatch.setattr(settings, "api_base_url", DASHSCOPE_URL)
    monkeypatch.setattr(settings, "model_name", "qwen3.8-max")
    monkeypatch.setattr(settings, "llm_thinking_format", "auto")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")


class TestMakeLlmExtraBody:
    def test_thinking_on_ships_tiered_enable(self, dashscope_qwen):
        from chaos_agent.agent.factory import make_llm

        assert make_llm(enable_thinking=True).extra_body == {
            "reasoning_effort": "high",
        }

    def test_explicit_disable_actually_ships(self, dashscope_qwen):
        """The fake-toggle regression: False must produce a wire field,
        not silence."""
        from chaos_agent.agent.factory import make_llm

        assert make_llm(enable_thinking=False).extra_body == {
            "reasoning_effort": "none",
        }

    def test_hybrid_model_ships_boolean_switch(
        self, dashscope_qwen, monkeypatch
    ):
        from chaos_agent.agent.factory import make_llm

        monkeypatch.setattr(settings, "model_name", "qwen3.7-plus")
        assert make_llm(enable_thinking=False).extra_body == {
            "enable_thinking": False,
        }

    def test_unknown_endpoint_ships_nothing(self, dashscope_qwen, monkeypatch):
        from chaos_agent.agent.factory import make_llm

        monkeypatch.setattr(settings, "api_base_url", "https://gw.internal/v1")
        assert make_llm(enable_thinking=True).extra_body is None
        assert make_llm(enable_thinking=False).extra_body is None

    def test_explicit_format_override_wins(
        self, dashscope_qwen, monkeypatch
    ):
        from chaos_agent.agent.factory import make_llm

        monkeypatch.setattr(settings, "api_base_url", "https://gw.internal/v1")
        monkeypatch.setattr(settings, "llm_thinking_format", "qwen")
        assert make_llm(enable_thinking=False).extra_body == {
            "reasoning_effort": "none",
        }


class TestWithThinkingDisabled:
    def test_real_client_gets_disable_copy(self, dashscope_qwen):
        from chaos_agent.agent.factory import (
            make_llm,
            with_thinking_disabled,
        )

        base = make_llm(enable_thinking=True)
        wrapped = with_thinking_disabled(base)
        assert wrapped is not base
        assert wrapped.extra_body == {"reasoning_effort": "none"}
        # The original client stays untouched for the main graph.
        assert base.extra_body == {"reasoning_effort": "high"}

    def test_unknown_endpoint_returns_same_client(
        self, dashscope_qwen, monkeypatch
    ):
        from chaos_agent.agent.factory import (
            make_llm,
            with_thinking_disabled,
        )

        monkeypatch.setattr(settings, "api_base_url", "https://gw.internal/v1")
        base = make_llm()
        assert with_thinking_disabled(base) is base

    @pytest.mark.parametrize("fake", [None, object(), "mock"])
    def test_non_chatopenai_passes_through(self, dashscope_qwen, fake):
        from chaos_agent.agent.factory import with_thinking_disabled

        assert with_thinking_disabled(fake) is fake

    def test_weak_model_keeps_thinking_on(self, dashscope_qwen, monkeypatch):
        """Capability gate: a < 1M-window model must NOT receive the
        thinking-disable — quality protection beats the latency win."""
        from chaos_agent.agent.factory import (
            make_llm,
            with_thinking_disabled,
        )

        # qwen3-max resolves to 262K — below the 1M threshold.
        monkeypatch.setattr(settings, "model_name", "qwen3-max-preview")
        base = make_llm(enable_thinking=True)
        assert with_thinking_disabled(base) is base

    def test_unknown_model_keeps_thinking_on(self, dashscope_qwen, monkeypatch):
        """Unregistered models land on the 100K global fallback — the
        exact 'default model' case that must never lose reasoning."""
        from chaos_agent.agent.factory import (
            make_llm,
            with_thinking_disabled,
        )

        monkeypatch.setattr(settings, "model_name", "some-private-llm")
        base = make_llm(enable_thinking=True)
        assert with_thinking_disabled(base) is base


class TestAuxCapabilityGate:
    def test_flagship_window_passes(self, dashscope_qwen):
        from chaos_agent.agent.factory import aux_calls_can_skip_thinking

        # Fixture model qwen3.8-max → 1M.
        assert aux_calls_can_skip_thinking() is True

    def test_small_window_fails(self, dashscope_qwen, monkeypatch):
        from chaos_agent.agent.factory import aux_calls_can_skip_thinking

        monkeypatch.setattr(settings, "model_name", "glm-4.6")  # 200K
        assert aux_calls_can_skip_thinking() is False

    def test_user_budget_override_respected(self, dashscope_qwen, monkeypatch):
        """model_budgets is the user's authority: registering a small
        window for a flagship name must fail the gate too."""
        from chaos_agent.agent.factory import aux_calls_can_skip_thinking

        monkeypatch.setattr(
            settings, "model_budgets",
            {"qwen3.8-max": {"max_tokens": 50_000, "compact_ratio": 0.8}},
        )
        assert aux_calls_can_skip_thinking() is False


class TestWindowPredicate:
    @pytest.mark.parametrize(
        "window, expected",
        [
            (1_000_000, True),
            (1_048_576, True),
            (2_000_000, True),
            (999_999, False),
            (262_144, False),
            (100_000, False),  # global fallback
        ],
    )
    def test_threshold(self, window, expected):
        from chaos_agent.llm import window_allows_thinking_skip

        assert window_allows_thinking_skip(window) is expected


class TestSettingsThinkingFormatField:
    def test_default_is_auto(self, monkeypatch, tmp_path):
        from chaos_agent.config.settings import Settings

        monkeypatch.setenv("BLADE_AI_CONFIG_DIR", str(tmp_path))
        assert Settings(llm_api_key="test").llm_thinking_format == "auto"

    @pytest.mark.parametrize("value", ["auto", "qwen", "openai", "deepseek", "none"])
    def test_valid_values_accepted(self, monkeypatch, tmp_path, value):
        from chaos_agent.config.settings import Settings

        monkeypatch.setenv("BLADE_AI_CONFIG_DIR", str(tmp_path))
        assert (
            Settings(llm_api_key="test", llm_thinking_format=value)
            .llm_thinking_format == value
        )

    def test_value_is_normalized(self, monkeypatch, tmp_path):
        from chaos_agent.config.settings import Settings

        monkeypatch.setenv("BLADE_AI_CONFIG_DIR", str(tmp_path))
        assert (
            Settings(llm_api_key="test", llm_thinking_format=" QWEN ")
            .llm_thinking_format == "qwen"
        )

    def test_invalid_value_raises(self, monkeypatch, tmp_path):
        from chaos_agent.config.settings import Settings

        monkeypatch.setenv("BLADE_AI_CONFIG_DIR", str(tmp_path))
        with pytest.raises(ValueError):
            Settings(llm_api_key="test", llm_thinking_format="qwn")
