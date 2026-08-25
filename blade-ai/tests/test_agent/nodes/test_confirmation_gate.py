"""Tests for confirmation_gate node."""

from unittest.mock import patch

import pytest

from chaos_agent.agent.nodes.gates.confirmation_gate import confirmation_gate


class TestConfirmationGate:
    """Tests for the confirmation_gate node function."""

    @pytest.mark.asyncio
    async def test_approved_returns_no_confirmation_needed(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Delete pod my-pod in namespace default"
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved"):
            result = await confirmation_gate(state)

        assert result["needs_confirmation"] is False
        assert result.get("safety_status") != "rejected"

    @pytest.mark.asyncio
    async def test_rejected_returns_rejected_status(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Delete pod my-pod"
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="rejected"):
            result = await confirmation_gate(state)

        assert result["safety_status"] == "rejected"
        assert "rejected" in result["safety_reason"].lower()
        assert result["needs_confirmation"] is False

    @pytest.mark.asyncio
    async def test_interrupt_called_with_confirmation_info(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default", "names": ["my-pod"]}
        state["plan"] = "Delete pod my-pod in namespace default"
        state["safety_status"] = "safe"
        state["safety_reason"] = None

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["skill_name"] == "pod-delete"
        # confirmation_gate projects fault_spec to the 4-key target
        # dict shape — the order of fields and the inclusion of
        # labels/resource_type is contract-tied to the spec layout.
        assert call_args["target"]["namespace"] == "default"
        assert call_args["target"]["names"] == ["my-pod"]
        assert "plan_summary" in call_args
        assert call_args["safety_status"] == "safe"

    @pytest.mark.asyncio
    async def test_plan_summary_truncated(self, sample_agent_state):
        long_plan = "x" * 1000
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = long_plan
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert len(call_args["plan_summary"]) == 500

    @pytest.mark.asyncio
    async def test_empty_plan_summary(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = ""
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["plan_summary"] == ""

    @pytest.mark.asyncio
    async def test_none_plan_summary(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = None
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["plan_summary"] == ""

    @pytest.mark.asyncio
    async def test_plan_summary_prefers_state_summary(self, sample_agent_state):
        """finish_planning's human-facing summary (stored by
        extract_planning_metadata) wins over the head-of-plan slice —
        the full complex-track plan must not bleed into the compact
        summary field."""
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "x" * 1000
        state["plan_summary"] = "compact human summary"
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["plan_summary"] == "compact human summary"

    @pytest.mark.asyncio
    async def test_preview_slices_review_sections_only(self, sample_agent_state):
        """The confirm-card preview carries Task Summary + Execution Steps
        only — Verification Methods / Rollback stay off the card (they
        travel to the verifier / plan file); a 50-70-line full plan
        rendered inline recreates the Ink cursor desync incident."""
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = (
            "## Task Summary\ninject mem load on node-a\n\n"
            "## Execution Steps\n1. blade create k8s node-mem load\n\n"
            "## Expected Impact\nmemory pressure visible in top\n\n"
            "## Verification Methods\nsample kubectl top node twice\n\n"
            "## Rollback and Recovery\nblade destroy <uid>"
        )
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        preview = mock_interrupt.call_args[0][0]["plan_preview_markdown"]
        assert "inject mem load on node-a" in preview
        assert "blade create k8s node-mem load" in preview
        assert "Verification Methods" not in preview
        assert "sample kubectl top node twice" not in preview
        assert "Rollback and Recovery" not in preview
        assert "Expected Impact" not in preview

    @pytest.mark.asyncio
    async def test_preview_headerless_plan_falls_back_to_full(self, sample_agent_state):
        """Simple-track / legacy plans carry no ``##`` headers → the whole
        text renders (it is already compact); nothing is lost."""
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Delete pod my-pod in namespace default"
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        preview = mock_interrupt.call_args[0][0]["plan_preview_markdown"]
        assert "Delete pod my-pod in namespace default" in preview

    @pytest.mark.asyncio
    async def test_safety_reason_included(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Delete pod"
        state["safety_status"] = "warning"
        state["safety_reason"] = "High blast radius"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["safety_reason"] == "High blast radius"

    @pytest.mark.asyncio
    async def test_default_safety_status(self):
        """When safety_status key is absent from state, defaults to 'safe'."""
        state = {
            "skill_name": "pod-delete",
            "target": {"namespace": "default"},
            "plan": "Plan",
        }

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        assert call_args["safety_status"] == "safe"

    @pytest.mark.asyncio
    async def test_none_target_handled(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        # Clear fault_spec so confirmation_gate produces an empty-shape
        # target dict (the FaultSpec's default zero values).
        state["fault_spec"] = None
        state["plan"] = "Plan"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="approved") as mock_interrupt:
            await confirmation_gate(state)

        call_args = mock_interrupt.call_args[0][0]
        # When no spec on record, target dict is all empty defaults.
        assert call_args["target"] == {
            "namespace": "", "names": [], "labels": {}, "resource_type": "",
        }

    @pytest.mark.asyncio
    async def test_unexpected_decision_rejected(self, sample_agent_state):
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Plan"
        state["safety_status"] = "safe"

        with patch("chaos_agent.agent.nodes.gates.confirmation_gate.interrupt", return_value="maybe"):
            result = await confirmation_gate(state)

        assert result["safety_status"] == "rejected"

    @pytest.mark.asyncio
    async def test_fault_intent_brief_in_confirmation_info(self, sample_agent_state):
        """fault_intent (fault_type / scope / target / action) must reach
        the TUI confirm card. Prior to wiring, L2 only had ``target``
        (namespace + names) — operators had to reverse-engineer the
        fault category from ``params`` keys (e.g. seeing ``mem-percent``
        to infer mem-load). Surfacing the L1 semantic classification
        in L2's payload makes "this is a mem-load" visible at a glance.

        Fixture-provided spec is pod/kill/delete → fault_type
        ``pod-kill-delete``.
        """
        state = sample_agent_state
        state["skill_name"] = "pod-delete"
        state["plan"] = "Delete pod"
        state["safety_status"] = "safe"

        with patch(
            "chaos_agent.agent.nodes.gates.confirmation_gate.interrupt",
            return_value="approved",
        ) as mock_interrupt:
            await confirmation_gate(state)

        payload = mock_interrupt.call_args[0][0]
        intent = payload.get("fault_intent")
        assert intent is not None, "fault_intent must be present in confirm payload"
        assert intent["fault_type"] == "pod-kill-delete"
        assert intent["scope"] == "pod"
        assert intent["target"] == "kill"
        assert intent["action"] == "delete"

    @pytest.mark.asyncio
    async def test_fault_intent_is_none_when_spec_empty(self, sample_agent_state):
        """When the spec is missing / incomplete (no fault_type derivable),
        the field must be ``None`` — not ``{}`` and not a half-empty dict
        — so the TUI's truthy check (``if faultIntent``) correctly
        suppresses the Fault row instead of rendering ``"  ()"``.

        Empty spec is the dry-run / clarification-incomplete path.
        """
        state = sample_agent_state
        state["fault_spec"] = None
        state["skill_name"] = "skill"
        state["plan"] = "p"
        state["safety_status"] = "safe"

        with patch(
            "chaos_agent.agent.nodes.gates.confirmation_gate.interrupt",
            return_value="approved",
        ) as mock_interrupt:
            await confirmation_gate(state)

        payload = mock_interrupt.call_args[0][0]
        assert payload.get("fault_intent") is None


class TestGateRejectionPersistsTerminalState:
    """End-to-end lock of the gate-rejection chain (node half).

    The store-level semantics are pinned in test_task_store.py
    (TestExecutionGateRejection: safety_status='rejected' lands →
    inference derives terminal "rejected" → field-less flushes cannot
    resurrect it). THIS test pins the node's half of the chain: the
    reject branch must actually persist the evidence through
    sync_to_store. Breaking any link — dropping the result field,
    deleting the sync_to_store call, or reordering the safety/error
    branches in infer_task_state — regresses gate rejections into
    boot-card ghosts, and each break fails a different assertion here.
    """

    @pytest.mark.asyncio
    async def test_rejection_lands_terminal_state_in_store(self, sample_agent_state):
        from chaos_agent.persistence.task_store import get_task_store

        state = sample_agent_state
        state["task_id"] = "inject-gate-e2e1"
        state["skill_name"] = "pod-delete"
        state["target"] = {"namespace": "default"}
        state["plan"] = "Delete pod my-pod in namespace default"
        state["safety_status"] = "safe"

        with patch(
            "chaos_agent.agent.nodes.gates.confirmation_gate.interrupt",
            return_value="rejected",
        ):
            result = await confirmation_gate(state)

        # Link 1: the state delta carries the evidence field.
        assert result["safety_status"] == "rejected"
        assert result["needs_confirmation"] is False

        # Links 2+3: it actually landed in the store, and inference
        # derived the terminal verdict from it (read back through the
        # conftest-isolated store — no mocks on the persistence path).
        store = await get_task_store()
        data = await store.get("inject-gate-e2e1")
        assert data is not None, "gate rejection must persist a task row"
        assert data["safety_status"] == "rejected"
        assert data["task_state"] == "rejected"
        assert data["needs_confirm"] == 0
