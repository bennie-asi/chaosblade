"""L4 observability pinning for the ``terminal_reports`` node.

task-349ccf5d moved report generation out of ``save_memory`` into a
dedicated ``terminal_reports`` node wrapped with
``with_phase_events("terminal_reports", "postmortem", ...)``. The L4
SDK side must see it:

1. ``_PHASE_STEP_MAP`` maps the node to the ``postmortem`` runtime
   step, so ``runtime.step("postmortem")`` containers still form.
2. ``_normalize_langgraph_event`` emits the node's ``phase_started``
   custom event (the postmortem phase is on the keep list — it fires
   once per terminal pass, no ReAct-loop noise).
"""

from chaos_agent.l4.events import (
    _PHASE_STEP_MAP,
    _normalize_langgraph_event,
)


class TestPhaseStepMap:
    def test_terminal_reports_maps_to_postmortem_step(self):
        assert _PHASE_STEP_MAP.get("terminal_reports") == "postmortem"

    def test_save_memory_entry_retained(self):
        """save_memory still exists (narrowed to persistence); its map
        entry must not have been removed along with the report code."""
        assert _PHASE_STEP_MAP.get("save_memory") == "postmortem"


class TestPhaseEventNormalization:
    def test_postmortem_phase_started_is_emitted(self):
        """with_phase_events("terminal_reports", "postmortem") fires a
        phase_started custom event; the L4 normalizer must keep it so
        the platform sees the terminal-reports phase begin."""
        out = _normalize_langgraph_event({
            "event": "on_custom_event",
            "name": "phase_started",
            "data": {"phase": "postmortem", "node": "terminal_reports"},
        })
        assert len(out) == 1
        assert out[0]["phase"] == "postmortem"
        assert out[0]["node"] == "terminal_reports"

    def test_postmortem_phase_completed_stays_deferred(self):
        """phase_completed is deliberately dropped here — _process_event
        emits it when the step container closes. Pinning so a refactor
        cannot double-close the container mid-flight."""
        out = _normalize_langgraph_event({
            "event": "on_custom_event",
            "name": "phase_completed",
            "data": {"phase": "postmortem", "node": "terminal_reports"},
        })
        assert out == []
