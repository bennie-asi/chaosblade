"""Behavioral tests for the ``terminal_reports`` node (task-349ccf5d funnel).

The node is the single report-producing step on every experiment
terminal path (``se_detect`` / ``direct_execute`` end / ``reject``),
ahead of ``save_memory``. These tests pin:

1. Non-injection intents (chat / recover-bridge) short-circuit without
   touching the LLM or the tracker — same early-exit semantics as
   ``save_memory``.
2. A ``planning_rejected`` terminal task DOES generate a postmortem —
   the gate is no longer keyed on ``blade_uid`` (which a rejected plan
   never produces), and the category is outside the skip list.
3. R11 — both report keys are ALWAYS written, even when None.
4. ``_infer_failure_detail`` trust-upstream guard: an upstream
   ``failure_detail`` (e.g. planning_rejected stamped by the reject
   node) is never overwritten by REPLAN_EXHAUSTED inference, while a
   genuinely missing detail is still inferred.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

_PM_MARKDOWN = "## Summary\nPlanning was rejected but the analysis ran.\n"


@pytest.fixture
def terminal_reports_mod(monkeypatch):
    from chaos_agent.agent.nodes.store import terminal_reports

    monkeypatch.setattr(
        terminal_reports, "sync_node_status_to_session", lambda *a, **k: None,
    )
    return terminal_reports


@pytest.fixture
def enabled_settings(monkeypatch):
    from chaos_agent.config import settings as s_mod

    monkeypatch.setattr(s_mod.settings, "postmortem_enabled", True)
    monkeypatch.setattr(s_mod.settings, "postmortem_timeout_seconds", 10)
    monkeypatch.setattr(s_mod.settings, "postmortem_max_messages", 30)
    return s_mod.settings


class TestIntentEarlyExit:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("intent", ["chat", "recover"])
    async def test_non_inject_intent_short_circuits(
        self, intent, terminal_reports_mod,
    ):
        """No fault experiment → no reports, and the LLM factory must
        never be reached (budget guard)."""
        from chaos_agent.agent import factory as _factory

        def boom(**kwargs):
            raise AssertionError("make_llm called for a non-inject intent")

        monkey_llm = boom
        state = {"task_id": "task-chat01", "confirmed_intent": intent, "messages": []}
        with patch.object(_factory, "make_llm", monkey_llm):
            updates = await terminal_reports_mod.terminal_reports_node(state)

        # R11 — both keys present and None even on the early-exit path.
        assert updates == {"postmortem": None, "issue_report": None}


class TestPlanningRejectedReport:
    @pytest.mark.asyncio
    async def test_planning_rejected_generates_postmortem(
        self, terminal_reports_mod, enabled_settings, tmp_path, monkeypatch,
    ):
        """task-349ccf5d core regression: a planning_rejected task has
        NO blade_uid (nothing was ever injected), yet the postmortem
        gate must fire — the old blade_uid-keyed gate silently dropped
        exactly these reports."""
        from chaos_agent.config import settings as s_mod
        monkeypatch.setattr(s_mod.settings, "memory_dir", tmp_path / "memory")

        state = {
            "task_id": "task-plrej01",
            "confirmed_intent": "inject",
            "blade_uid": "",
            "failure_detail": {
                "category": "planning_rejected",
                "context": "user refused the injection plan",
            },
            "replan_count": 2,
            "replan_context": {"error_summary": "plan invalid"},
            "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=_PM_MARKDOWN))
        with patch(
            "chaos_agent.agent.factory.make_llm", return_value=mock_llm,
        ), patch(
            "chaos_agent.agent.postmortem.store.POSTMORTEM_DIR",
            tmp_path / "postmortems",
        ):
            updates = await terminal_reports_mod.terminal_reports_node(state)

        assert mock_llm.ainvoke.call_count == 1  # gate fired despite no uid
        pm = updates["postmortem"]
        assert isinstance(pm, dict)
        assert "## Summary" in pm["markdown"]
        assert updates["issue_report"] is None  # not a FAILED drill

    @pytest.mark.asyncio
    async def test_postmortem_aux_record_carries_duration(
        self, terminal_reports_mod, enabled_settings, tmp_path, monkeypatch,
    ):
        """The postmortem audit record must carry the LLM call duration —
        the aux-log exists precisely so a slow postmortem call can be
        inspected after the fact (a live run once logged duration=None)."""
        from chaos_agent.config import settings as s_mod
        monkeypatch.setattr(s_mod.settings, "memory_dir", tmp_path / "memory")

        state = {
            "task_id": "task-pmdur01",
            "confirmed_intent": "inject",
            "blade_uid": "",
            "failure_detail": {
                "category": "planning_rejected",
                "context": "user refused the injection plan",
            },
            "replan_count": 1,
            "replan_context": {"error_summary": "plan invalid"},
            "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=_PM_MARKDOWN))
        mock_store = MagicMock()
        with patch(
            "chaos_agent.agent.factory.make_llm", return_value=mock_llm,
        ), patch(
            "chaos_agent.agent.postmortem.store.POSTMORTEM_DIR",
            tmp_path / "postmortems",
        ), patch(
            "chaos_agent.memory.session_store.get_global_session_store",
            return_value=mock_store,
        ):
            await terminal_reports_mod.terminal_reports_node(state)

        mock_store.record_aux_llm_call.assert_called_once()
        kwargs = mock_store.record_aux_llm_call.call_args.kwargs
        assert kwargs["purpose"] == "postmortem"
        assert isinstance(kwargs["duration_ms"], int)
        assert kwargs["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_planning_rejected_category_survives_save_memory(
        self, enabled_settings, tmp_path, monkeypatch,
    ):
        """Trust-upstream guard: save_memory's ``_infer_failure_detail``
        must NOT overwrite the reject node's planning_rejected detail
        with REPLAN_EXHAUSTED, even though replan bookkeeping + error
        are present. Without the guard the result card shows the wrong
        category and the postmortem gate re-evaluates against a lie."""
        from chaos_agent.agent.nodes.store import memory_nodes
        monkeypatch.setattr(memory_nodes, "sync_to_store", AsyncMock())
        monkeypatch.setattr(
            memory_nodes, "sync_node_status_to_session", lambda *a, **k: None,
        )
        monkeypatch.setattr(enabled_settings, "memory_dir", tmp_path / "memory")

        state = {
            "task_id": "task-plrej02",
            "confirmed_intent": "inject",
            "failure_detail": {
                "category": "planning_rejected",
                "context": "user refused the injection plan",
            },
            "error": "planning rejected by user",
            "replan_count": 2,
            "replan_context": {"error_summary": "plan invalid"},
            "messages": [],
        }

        updates = await memory_nodes.save_memory(state)

        # Guard returned {} — no inference keys leaked into updates, so
        # the persisted category stays planning_rejected.
        assert "failure_detail" not in updates
        assert "error" not in updates


class TestInferFailureDetailGuard:
    """Direct unit-level lock on ``_infer_failure_detail`` semantics."""

    def test_upstream_failure_detail_is_trusted(self):
        from chaos_agent.agent.nodes.store.memory_nodes import (
            _infer_failure_detail,
        )

        state = {
            "task_id": "task-guard01",
            "confirmed_intent": "inject",
            "failure_detail": {
                "category": "planning_rejected",
                "context": "user refused the plan",
            },
            "error": "planning rejected",
            "replan_count": 2,
            "replan_context": {"error_summary": "x"},
            "messages": [],
        }
        assert _infer_failure_detail(state) == {}

    def test_missing_failure_detail_is_still_inferred(self):
        """Contrast case: with no upstream detail the REPLAN_EXHAUSTED
        inference still fires (the guard must not over-suppress)."""
        from chaos_agent.agent.nodes.store.memory_nodes import (
            _infer_failure_detail,
        )

        state = {
            "task_id": "task-guard02",
            "confirmed_intent": "inject",
            "error": "blade create exploded",
            "replan_count": 1,
            "replan_context": {"error_summary": "x"},
            "messages": [],
        }
        inferred = _infer_failure_detail(state)
        assert inferred["failure_detail"]["category"] == "replan_exhausted"

    def test_expired_layer1_overridden_by_verified_layer2_is_not_failed(self):
        """Expired-record Layer1 failure must not veto a Layer2-verified
        verdict (task inject-e47de3e8: executor cleanup destroyed the record,
        Layer2 verified the restarts, task was wrongly failed)."""
        from chaos_agent.agent.nodes.store.memory_nodes import (
            _infer_failure_detail,
        )

        state = {
            "task_id": "task-guard03",
            "confirmed_intent": "inject",
            "verification": {
                "level": "verified",
                "layer1": {"status": "failed", "expired": True, "details": "Destroyed"},
                "layer2": {"status": "passed", "details": "restarts +4"},
            },
            "messages": [],
        }
        assert _infer_failure_detail(state) == {}

    def test_expired_layer1_without_layer2_confirmation_still_fails(self):
        """Contrast: exemption only applies when Layer2 passed AND level is
        verified — a bare expired Layer1 failure still fails the task."""
        from chaos_agent.agent.nodes.store.memory_nodes import (
            _infer_failure_detail,
        )

        state = {
            "task_id": "task-guard04",
            "confirmed_intent": "inject",
            "verification": {
                "level": "partial",
                "layer1": {"status": "failed", "expired": True, "details": "Destroyed"},
                "layer2": {"status": "unknown", "details": ""},
            },
            "messages": [],
        }
        inferred = _infer_failure_detail(state)
        assert inferred["failure_detail"]["category"] == "verification_failed"


class TestR11AlwaysWrite:
    @pytest.mark.asyncio
    async def test_skip_category_still_writes_both_keys(
        self, terminal_reports_mod, enabled_settings, tmp_path, monkeypatch,
    ):
        """user_rejected is on the skip list → no LLM call, but BOTH
        keys must land in the updates (None) so a stale payload from a
        prior experiment on this thread cannot bleed through."""
        from chaos_agent.config import settings as s_mod
        monkeypatch.setattr(s_mod.settings, "memory_dir", tmp_path / "memory")

        state = {
            "task_id": "task-urej01",
            "confirmed_intent": "inject",
            "blade_uid": "",
            "failure_detail": {"category": "user_rejected"},
            "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock()
        with patch(
            "chaos_agent.agent.factory.make_llm", return_value=mock_llm,
        ):
            updates = await terminal_reports_mod.terminal_reports_node(state)

        assert mock_llm.ainvoke.call_count == 0
        assert set(updates.keys()) == {"postmortem", "issue_report"}
        assert updates["postmortem"] is None
        assert updates["issue_report"] is None


class TestTrackerSourceAttribution:
    @pytest.mark.asyncio
    async def test_node_completed_event_keeps_terminal_reports_source(
        self, terminal_reports_mod, enabled_settings, tmp_path, monkeypatch,
    ):
        """The tracker is a flat single-source model: when the gate
        fires, _generate_postmortem runs its own lifecycle under
        source="postmortem" and overwrites _current_source. The node's
        own COMPLETED event must still be attributed to
        "terminal_reports" (CLI printer / SSE key events off source)."""
        from chaos_agent.config import settings as s_mod
        from chaos_agent.observability.status_tracker import get_tracker
        monkeypatch.setattr(s_mod.settings, "memory_dir", tmp_path / "memory")

        state = {
            "task_id": "task-src01",
            "confirmed_intent": "inject",
            "failure_detail": {"category": "planning_rejected"},
            "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=_PM_MARKDOWN))
        with patch(
            "chaos_agent.agent.factory.make_llm", return_value=mock_llm,
        ), patch(
            "chaos_agent.agent.postmortem.store.POSTMORTEM_DIR",
            tmp_path / "postmortems",
        ):
            await terminal_reports_mod.terminal_reports_node(state)

        events = get_tracker("task-src01").get_history()
        started = [e for e in events if e["phase"] == "started"]
        completed = [e for e in events if e["phase"] == "completed"]
        # Both lifecycles opened...
        assert any(e["source"] == "terminal_reports" for e in started)
        assert any(e["source"] == "postmortem" for e in started)
        # ...and the node's own completion keeps its source.
        assert any(
            e["source"] == "terminal_reports"
            and e["message"] == "Terminal reports generated"
            for e in completed
        ), f"terminal_reports COMPLETED missing: {completed}"
