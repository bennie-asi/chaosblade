"""Tests for the GuardFeedback evidence contract (design 4.7).

Evidence spans are display/audit coordinates — they must never leak into
the model-facing text, and every pre-existing construction site must keep
working with zero changes (``evidence`` defaults to empty).
"""

from dataclasses import FrozenInstanceError

import pytest

from chaos_agent.tools.guard_feedback import (
    EvidenceSpan,
    GuardFeedback,
    ViolatedConstraint,
)


class TestEvidenceSpan:
    def test_fields(self):
        span = EvidenceSpan(start=4, end=9, label="command_substitution")
        assert (span.start, span.end, span.label) == (4, 9, "command_substitution")

    def test_frozen(self):
        span = EvidenceSpan(start=0, end=1, label="x")
        with pytest.raises(FrozenInstanceError):
            span.start = 5  # type: ignore[misc]


class TestGuardFeedbackEvidence:
    def test_evidence_defaults_empty(self):
        # The backward-compat contract: construction sites that predate the
        # evidence field keep working unchanged.
        fb = GuardFeedback(allowed=False, reason="nope")
        assert fb.evidence == ()

    def test_evidence_never_enters_llm_text(self):
        base = GuardFeedback(
            allowed=False, reason="R", offending="tok", compliant_form="fix"
        )
        with_evidence = GuardFeedback(
            allowed=False,
            reason="R",
            offending="tok",
            compliant_form="fix",
            evidence=(EvidenceSpan(0, 3, "shell_metacharacter"),),
        )
        assert with_evidence.render_for_llm() == base.render_for_llm()
        assert with_evidence.as_tuple() == base.as_tuple()

    def test_budget_exceeded_enum(self):
        # Design 4.7: the ONLY new constraint value — reshapeable, its fix is
        # "split into simpler command shapes", so never a hard floor.
        assert ViolatedConstraint.BUDGET_EXCEEDED.value == "budget_exceeded"
