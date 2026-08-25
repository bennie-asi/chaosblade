"""Tests for intent_confirm node — verifies the interrupt payload, the
post-decision state transitions, and the **Option A handoff**.

Why payload-shape matters: the TUI renderer (tui/renderers/intent_confirm.py)
reads ``intent_confidence`` out of this dict to decide whether to draw the
low-confidence warning row. If the node forgets to forward it, the warning
silently never fires.

Why the Option A handoff matters: the messages-trim and
``bootstrap_task_session`` side effects used to fire from
``intent_clarification`` the moment intent converged, which meant a user
rejection at the confirm gate left the dialogue truncated and an orphan
task file on disk. The approved / dry_run branches now own these side
effects so a rejection is fully reversible. The blast-radius assertions
in ``test_intent_clarification.py`` cover the inverse — that
``intent_clarification`` no longer produces these side effects on the
inject branch — so the two test files together pin the contract from
both sides.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
)

from chaos_agent.agent.spec.fault_spec import FaultSpec
from chaos_agent.agent.nodes.planning import intent_confirm as ic_mod
from chaos_agent.agent.nodes.planning.intent_confirm import intent_confirm


def _spec(
    *,
    scope: str = "pod",
    fault_target: str = "cpu",
    fault_action: str = "fullload",
    namespace: str = "cms-demo",
    **kwargs,
) -> dict:
    """Build a ``fault_spec`` state dict in the new (post-refactor)
    serialised shape — what ``intent_clarification`` writes and what
    ``read_fault_spec`` expects to find."""
    kwargs.setdefault("duration_seconds", 600)
    spec = FaultSpec(
        namespace=namespace,
        scope=scope,
        fault_target=fault_target,
        fault_action=fault_action,
        **kwargs,
    )
    return spec.to_dict()


def _state(**overrides):
    base = {
        "task_id": "t-confirm-1",
        # State key is ``fault_spec`` after the FaultSpec refactor; the
        # legacy ``fault_intent`` key on state was retired in favour of
        # a single normalised source of truth (``read_fault_spec``).
        "fault_spec": _spec(),
        "intent_confidence": 0.92,
    }
    base.update(overrides)
    return base


class TestIntentConfirmInterruptPayload:
    """interrupt() is monkey-patched to a sentinel-raising lambda that
    captures its argument; we read the dict the node tried to send."""

    @pytest.mark.asyncio
    async def test_unknown_scope_is_rejected_before_interrupt(self):
        state = _state(fault_spec=_spec(scope="typo_scope"))

        result = await intent_confirm(state)

        assert result["confirmed_intent"] is None
        assert "unsupported scope" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_payload_carries_intent_confidence(self):
        captured: dict = {}

        def fake_interrupt(info):
            captured.update(info)
            raise RuntimeError("interrupt-stub")

        with patch("chaos_agent.agent.nodes.planning.intent_confirm.interrupt", fake_interrupt):
            with pytest.raises(RuntimeError, match="interrupt-stub"):
                await intent_confirm(_state())

        assert captured["type"] == "intent_confirm"
        assert captured["intent_confidence"] == pytest.approx(0.92)
        # ``fault_type`` is a *derived* property — composed from
        # ``scope-blade_target-blade_action`` inside ``FaultSpec`` (see
        # ``fault_spec.py:fault_type`` @property). Asserting the
        # composed value here pins both the projection (spec →
        # legacy intent dict via ``to_intent_dict``) and the payload
        # wiring (intent dict → confirm SSE).
        assert captured["fault_intent"]["fault_type"] == "pod-cpu-fullload"

    @pytest.mark.asyncio
    async def test_missing_confidence_defaults_to_zero(self):
        """When upstream did not set intent_confidence (e.g. legacy paths),
        the node must coerce to 0.0 rather than propagate None — the
        renderer's ``> 0`` gate relies on a real float."""
        captured: dict = {}

        def fake_interrupt(info):
            captured.update(info)
            raise RuntimeError("interrupt-stub")

        state = _state()
        state.pop("intent_confidence")
        with patch("chaos_agent.agent.nodes.planning.intent_confirm.interrupt", fake_interrupt):
            with pytest.raises(RuntimeError, match="interrupt-stub"):
                await intent_confirm(state)

        assert captured["intent_confidence"] == 0.0


class TestIntentConfirmDecisionRouting:

    @pytest.mark.asyncio
    async def test_approved_emits_handoff_summary(self):
        """Dual-graph model: approved branch writes handoff_summary
        (string) instead of appending SystemMessage to messages.
        """
        with patch.object(ic_mod, "interrupt", return_value="approved"):
            result = await intent_confirm(_state())
        summary = result.get("handoff_summary", "")
        assert summary, "approved branch must set handoff_summary"
        assert summary.startswith("[Intent Clarification Summary]")

    @pytest.mark.asyncio
    async def test_rejected_keeps_reviewed_fault_spec_for_refinement(self):
        """Rejection keeps the sole reviewed contract for the next dialogue turn."""
        with patch(
            "chaos_agent.agent.nodes.planning.intent_confirm.interrupt",
            return_value="rejected",
        ):
            result = await intent_confirm(_state())
        assert result == {"confirmed_intent": None}


# ---------------------------------------------------------------------------
# Option A — handoff side effects (trim + summary + bootstrap) live HERE,
# not in intent_clarification. The classes below pin the full contract.
# ---------------------------------------------------------------------------


def _make_dialogue_messages(n: int) -> list:
    """Build N alternating Human/AI messages with stable ids so a
    trim-on-id assertion can identify which entries were dropped."""
    out: list = []
    for i in range(n):
        if i % 2 == 0:
            out.append(HumanMessage(content=f"u-{i}", id=f"m-{i}"))
        else:
            out.append(AIMessage(content=f"a-{i}", id=f"m-{i}"))
    return out


def _handoff_state(decision_messages: list, *, dry_run: bool = False) -> dict:
    """Common state shape — ``task_id`` already allocated by clarification,
    ``fault_spec`` populated in the post-refactor serialised shape,
    ``dialogue_round`` set non-zero so the summary's ``Dialogue rounds:
    N`` line is exercised. The spec uses pod/cpu/fullload so the
    summary's "Fault: ..." line reads as "pod-cpu-fullload →
    pod/cpu/fullload @ production" — distinctive enough that a
    spec-projection regression (e.g. ``read_fault_spec`` returning
    None silently) would surface as an empty/unknown summary line and
    fail the prefix assertion.
    """
    return {
        "task_id": "task-deadbeef",
        "tui_session_id": "sess_test",
        "fault_spec": _spec(namespace="production"),
        "intent_confidence": 1.0,
        "dialogue_round": 3,
        "messages": decision_messages,
        "dry_run": dry_run,
    }


class TestIntentConfirmApprovedHandoff:
    @pytest.mark.asyncio
    async def test_trim_drops_all_but_last_four(self, monkeypatch):
        monkeypatch.setattr(ic_mod, "interrupt", lambda *_a, **_k: "approved")

        # 6 messages — trim window keeps last 4, drops first 2.
        state = _handoff_state(_make_dialogue_messages(6))
        result = await intent_confirm(state)

        delta = result.get("messages") or []
        remove_msgs = [m for m in delta if isinstance(m, RemoveMessage)]
        assert len(remove_msgs) == 2
        assert {m.id for m in remove_msgs} == {"m-0", "m-1"}

        # handoff_summary carries the summary (not messages)
        summary = result.get("handoff_summary", "")
        assert summary.startswith("[Intent Clarification Summary]")
        assert "Fault: pod-cpu-fullload" in summary
        assert "pod/cpu/fullload @ production" in summary
        assert "Dialogue rounds: 3" in summary

    @pytest.mark.asyncio
    async def test_short_history_skips_trim_but_emits_summary(self, monkeypatch):
        monkeypatch.setattr(ic_mod, "interrupt", lambda *_a, **_k: "approved")

        state = _handoff_state(_make_dialogue_messages(2))
        result = await intent_confirm(state)

        delta = result.get("messages") or []
        assert [m for m in delta if isinstance(m, RemoveMessage)] == []
        summary = result.get("handoff_summary", "")
        assert summary.startswith("[Intent Clarification Summary]")


class TestIntentConfirmRejectedPreservesDialogue:
    @pytest.mark.asyncio
    async def test_rejection_does_not_touch_messages_or_bootstrap(self, monkeypatch):
        """Rejection leaves the working message list untouched so the
        next conversational turn can iterate on established context.
        """
        monkeypatch.setattr(ic_mod, "interrupt", lambda *_a, **_k: "rejected")

        state = _handoff_state(_make_dialogue_messages(8))
        result = await intent_confirm(state)

        assert "messages" not in result, (
            "rejection must NOT touch messages — dialogue is preserved "
            "so the next turn iterates on established context"
        )
        assert result.get("confirmed_intent") is None
        # FaultSpec is the sole continuation state. Rejection must not clear
        # it, because the next turn may refine this reviewed contract.
        assert "fault_spec" not in result
        assert "batch_submit_args" not in result


class TestIntentConfirmDryRunHandoff:
    @pytest.mark.asyncio
    async def test_dry_run_commits_handoff_without_interrupt(self, monkeypatch):
        """``state.dry_run=True`` mirrors the approved path: no
        ``interrupt()`` call (the user opted into preview-only via
        /plan), but the inject pipeline still gets the same clean
        handoff so the plan preview is generated against the same
        Phase-1 LLM context the real flow would see.
        """
        called: dict = {"interrupt": 0}

        def fake_interrupt(*_a, **_k):
            called["interrupt"] += 1
            return "should-not-be-used"

        monkeypatch.setattr(ic_mod, "interrupt", fake_interrupt)

        state = _handoff_state(_make_dialogue_messages(6), dry_run=True)
        result = await intent_confirm(state)

        assert called["interrupt"] == 0, "dry_run must NOT trigger interrupt()"

        summary = result.get("handoff_summary", "")
        assert summary.startswith("[Intent Clarification Summary]")


# ---------------------------------------------------------------------------
# Task-row lifecycle writes at the confirmation gate (ghost-row fix)
# ---------------------------------------------------------------------------

class TestTaskRowLifecycleWrites:
    """The confirmation gate is the ONLY place that can derive "cancelled"
    (a rejected intent is not unfinished work) and must un-cancel on
    reuse-approval (the monotonicity guard would otherwise pin cancelled).
    Stores go through the conftest-isolated tmp TaskStore singleton.
    """

    @staticmethod
    async def _row_state(task_id: str) -> str:
        from chaos_agent.persistence.task_store import get_task_store

        store = await get_task_store()
        data = await store.get(task_id)
        return (data or {}).get("task_state", "")

    @pytest.mark.asyncio
    async def test_rejected_cancels_task_row(self):
        state = _state(task_id="inject-confirm1")
        with patch.object(ic_mod, "interrupt", return_value="rejected"):
            result = await intent_confirm(state)
        assert result == {"confirmed_intent": None}
        assert await self._row_state("inject-confirm1") == "cancelled"

    @pytest.mark.asyncio
    async def test_approved_revives_cancelled_task_row(self):
        """ID reuse contract: reject then approve the same id — the
        approval must explicitly un-cancel the row."""
        from chaos_agent.persistence.task_store import get_task_store

        store = await get_task_store()
        await store.upsert("inject-confirm1", fault_spec={"target": "pod"})
        await store.update_task_state("inject-confirm1", "cancelled")

        with patch.object(ic_mod, "interrupt", return_value="approved"):
            await intent_confirm(_state(task_id="inject-confirm1"))

        assert await self._row_state("inject-confirm1") == "injecting"

    @pytest.mark.asyncio
    async def test_non_inject_id_is_untouched(self):
        """Sessions with non inject- ids (tui session anchors) must not
        get task rows created by the gate."""
        from chaos_agent.persistence.task_store import get_task_store

        with patch.object(ic_mod, "interrupt", return_value="rejected"):
            await intent_confirm(_state())  # task_id="t-confirm-1"
        store = await get_task_store()
        assert await store.get("t-confirm-1") is None


class TestReviveGuards:
    """_revive_task_row is deliberately conditional — pins the ghost-
    maker regression found in review: update_task_state on a MISSING row
    INSERTs a bare injecting row (a fresh ghost), and on a terminal row
    it would clobber the verdict.
    """

    @pytest.mark.asyncio
    async def test_revive_is_noop_when_row_missing(self):
        """First-ever approval (row not yet created): must NOT insert a
        bare injecting row — the row is born naturally on first sync."""
        from chaos_agent.persistence.task_store import get_task_store

        with patch.object(ic_mod, "interrupt", return_value="approved"):
            await intent_confirm(_state(task_id="inject-fresh1"))

        store = await get_task_store()
        assert await store.get("inject-fresh1") is None

    @pytest.mark.asyncio
    async def test_revive_is_noop_on_terminal_verdict(self):
        """A verdict row (injected) must never be clobbered to injecting."""
        from chaos_agent.persistence.task_store import get_task_store

        store = await get_task_store()
        await store.upsert("inject-done1", skill_name="pod-kill", experiment_uid="abc",
                           verification={"layer1": {"status": "passed"},
                                         "layer2": {"status": "passed"}})
        assert (await store.get("inject-done1"))["task_state"] == "injected"

        with patch.object(ic_mod, "interrupt", return_value="approved"):
            await intent_confirm(_state(task_id="inject-done1"))

        assert (await store.get("inject-done1"))["task_state"] == "injected"

    @pytest.mark.asyncio
    async def test_reject_clears_needs_confirm_flag(self):
        """_cancel_task_row contract: rejection also clears needs_confirm,
        so no later flush re-derives waiting_input for a dead intent."""
        from chaos_agent.persistence.task_store import get_task_store

        store = await get_task_store()
        await store.upsert("inject-rej1", fault_spec={"target": "pod"}, needs_confirm=1)

        with patch.object(ic_mod, "interrupt", return_value="rejected"):
            await intent_confirm(_state(task_id="inject-rej1"))

        data = await store.get("inject-rej1")
        assert data["task_state"] == "cancelled"
        assert data["needs_confirm"] == 0


class TestEarlyRejectionTerminalWrite:
    """The spec-incomplete early rejection (before the card is even
    rendered) runs the same terminal write as an explicit card rejection:
    the row may already carry fault_spec evidence from clarification,
    so without the write it would project "injecting" forever.
    """

    @pytest.mark.asyncio
    async def test_incomplete_spec_cancels_task_row(self):
        from chaos_agent.persistence.task_store import get_task_store

        state = _state(task_id="inject-incomplete1",
                       fault_spec=_spec(scope="typo_scope"))
        result = await intent_confirm(state)

        assert result["confirmed_intent"] is None
        store = await get_task_store()
        data = await store.get("inject-incomplete1")
        assert data is not None
        assert data["task_state"] == "cancelled"
        assert data["needs_confirm"] == 0
