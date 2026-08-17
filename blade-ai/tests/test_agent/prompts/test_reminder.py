"""Guard tests for system-reminder tag binding (U-shaped Phase A).

Tag binding has two halves that must stay in lockstep:
1. Every harness-injected reminder is wrapped in ``<system-reminder>`` tags
   (wrap helper + the two persist helpers that funnel all corrective hints).
2. Every phase's primacy section declares what the tags mean, so the model
   connects a tag occurrence back to the harness instead of reading it as
   user speech or tool output.

The repeat-count stamp must remain parseable through the wrap: hint counts
live in ``state["hint_repeat_counts"]`` but ``count_prior_hints`` reads the
stamp back out of persisted messages as the fallback path, so a wrap that
hid or corrupted the stamp would silently reset escalation.
"""

import pytest

from chaos_agent.agent.prompts.reminder import (
    SYSTEM_REMINDER_CLOSE,
    SYSTEM_REMINDER_DECLARATION,
    SYSTEM_REMINDER_OPEN,
    wrap_system_reminder,
)


class TestWrapSystemReminder:
    def test_wraps_text_in_tags(self):
        wrapped = wrap_system_reminder("Do something different.")
        assert wrapped.startswith(SYSTEM_REMINDER_OPEN)
        assert wrapped.endswith(SYSTEM_REMINDER_CLOSE)
        assert "Do something different." in wrapped

    def test_idempotent(self):
        once = wrap_system_reminder("text")
        twice = wrap_system_reminder(once)
        assert twice == once
        assert twice.count(SYSTEM_REMINDER_OPEN) == 1

    def test_normalizes_surrounding_whitespace(self):
        assert wrap_system_reminder("  x  ") == wrap_system_reminder("x")


class TestPersistedHintsAreWrapped:
    """All corrective/budget hints funnel through the two persist helpers."""

    def test_corrective_hint_wrapped_with_parseable_repeat_stamp(self):
        from chaos_agent.agent.nodes.execute.llm_step_helpers import (
            count_prior_hints,
            persist_corrective_hint,
        )

        injections: list = []
        msg = persist_corrective_hint(
            injections, [], "stagnation", "kubectl_read", "Change action."
        )
        assert msg.content.startswith(SYSTEM_REMINDER_OPEN)
        assert msg.content.endswith(SYSTEM_REMINDER_CLOSE)
        # The fallback count path must still read the stamp through the wrap.
        assert count_prior_hints([injections[0]], "stagnation", "kubectl_read") == 1

    def test_corrective_hint_repeat_text_inside_tag(self):
        from chaos_agent.agent.nodes.execute.llm_step_helpers import (
            persist_corrective_hint,
        )

        injections: list = []
        msg = persist_corrective_hint(
            injections, [], "loop", "verify", "You are repeating.",
            counts={"loop:verify": 2},
        )
        assert "reminder #3" in msg.content
        assert msg.content.startswith(SYSTEM_REMINDER_OPEN)

    def test_replaceable_hint_wrapped(self):
        from chaos_agent.agent.nodes.execute.llm_step_helpers import (
            persist_replaceable_hint,
        )

        injections: list = []
        msg = persist_replaceable_hint(
            injections, "budget", "execute", "iteration 12 of max 15"
        )
        assert msg.content.startswith(SYSTEM_REMINDER_OPEN)
        assert msg.content.endswith(SYSTEM_REMINDER_CLOSE)
        assert "iteration 12 of max 15" in msg.content


# Every phase prompt that can receive a wrapped reminder must declare the
# tag in its primacy zone; a wrap without the declaration is an anonymous
# tag the model has no contract for.
_DECLARATION_SOURCES = [
    ("chaos_agent.agent.prompts.sections.workflow", "get_core_principles_section"),
    ("chaos_agent.agent.prompts.sections.workflow", "get_executor_core_principles_section"),
    ("chaos_agent.agent.prompts.sections.verification", "get_verifier_core_principles_section"),
    ("chaos_agent.agent.prompts.sections.recovery", "get_recover_core_principles_section"),
    ("chaos_agent.agent.prompts.sections.intent", "get_intent_priorities_section"),
]


class TestDeclarationBinding:
    @pytest.mark.parametrize("module_path,func_name", _DECLARATION_SOURCES)
    def test_section_declares_the_tag(self, module_path, func_name):
        import importlib

        section = getattr(importlib.import_module(module_path), func_name)()
        assert "<system-reminder>" in section, (
            f"{func_name} no longer mentions the tag — wrapped reminders "
            "would arrive without a contract"
        )
        assert SYSTEM_REMINDER_DECLARATION in section, (
            f"{func_name} drifted from the canonical declaration wording"
        )

    @pytest.mark.parametrize("is_kubectl_blade", [True, False], ids=["kubectl_blade", "non_chaosblade"])
    def test_layer1_recovery_prompt_declares_the_tag(self, is_kubectl_blade):
        from chaos_agent.agent.nodes.recover._recover_layer1 import (
            _build_layer1_recovery_prompt,
        )

        prompt = _build_layer1_recovery_prompt(is_kubectl_blade=is_kubectl_blade)
        assert SYSTEM_REMINDER_DECLARATION in prompt

    def test_recover_verifier_prompt_declares_the_tag(self):
        from chaos_agent.agent.prompts.sections.recovery import (
            build_recover_verifier_system_prompt,
        )

        prompt = build_recover_verifier_system_prompt()
        assert SYSTEM_REMINDER_DECLARATION in prompt

    def test_declaration_names_not_user_or_tool_speech(self):
        # The contract's whole point: reminders are neither user input nor
        # tool output. Guard the wording against drift that would weaken it.
        assert "NOT part of the user's input" in SYSTEM_REMINDER_DECLARATION
        assert "tool's output" in SYSTEM_REMINDER_DECLARATION
