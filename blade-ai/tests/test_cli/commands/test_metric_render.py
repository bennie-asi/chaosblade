"""Tests for the human-readable ``blade-ai metric`` text rendering.

Covers both the pure render transforms (metrics_render.py) and the
command wiring (default ``-o text`` vs machine-readable ``-o json``).
"""

import json
import re

import typer
from typer.testing import CliRunner

from chaos_agent.cli.metrics_render import (
    format_ms,
    render_metric,
    render_metric_list,
    render_text,
)

runner = CliRunner()


def _app(command) -> typer.Typer:
    app = typer.Typer()
    app.command()(command)
    return app


# ---------------------------------------------------------------------------
# Fixtures — mirror the get_metric / get_all_metrics envelope shapes
# ---------------------------------------------------------------------------

def _task_data() -> dict:
    return {
        "task_id": "inject-3198b391-b2ab-4ed4-b91b-1a3941209c51",
        "status": "success",
        "task_state": "injected",
        "phase": "verified",
        "fault_type": "pod-process-stop",
        "skill_name": "k8s-chaos-skills",
        "target": {
            "namespace": "default",
            "names": ["demo-pod-abc"],
            "labels": {},
            "resource_type": "pod",
        },
        "params": {"process": "nginx"},
        "experiment_uid": "uid-123",
        "safety_status": "safe",
        "verification": {
            "level": "verified",
            "layer1": {"status": "skipped", "details": "not applicable"},
            "layer2": {"status": "passed", "details": "workers stopped. service down"},
            "warnings": ["long warning text. trailing detail " + "x" * 300],
        },
        "error": "",
        "gmt_create": "2026-08-13T16:32:02+08:00",
        "finished_at": "2026-08-13T16:44:19+08:00",
        "duration_ms": 737000,
        "spans": [
            {
                "node_name": "intent_confirm", "start_time": 100.0,
                "duration_ms": 0.08, "token_input": 0, "token_output": 0,
                "tool_calls": [], "error": "interrupted (awaiting resume)",
            },
            {
                "node_name": "agent_loop", "start_time": 103.0,
                "duration_ms": 60000.0, "token_input": 10023, "token_output": 807,
                "tool_calls": ["kubectl_read", "kubectl_read"], "error": None,
            },
            {
                "node_name": "execute_loop", "start_time": 200.0,
                "duration_ms": 10000.0, "token_input": 0, "token_output": 0,
                "tool_calls": ["kubectl"], "error": "boom happened",
            },
        ],
        "summary": {
            "total_token_input": 683002, "total_token_output": 28545,
            "total_llm_calls": 25, "total_tool_calls": 39,
            "total_duration_ms": 675517,
        },
    }


def _list_data() -> dict:
    return {
        "total": 1,
        "tasks": [{
            "task_id": "inject-3198b391-b2ab-4ed4-b91b-1a3941209c51",
            "status": "success", "task_state": "injected",
            "phase": "verified", "fault_type": "pod-process-stop",
            "gmt_create": "2026-08-13T16:32:02+08:00",
            "summary": {"total_duration_ms": 675517},
        }],
    }


# ---------------------------------------------------------------------------
# format_ms
# ---------------------------------------------------------------------------

class TestFormatMs:
    def test_milliseconds(self):
        assert format_ms(350) == "350ms"

    def test_seconds(self):
        assert format_ms(4800) == "4.8s"

    def test_minutes(self):
        assert format_ms(675517) == "11m16s"

    def test_hours(self):
        assert format_ms(3_700_000) == "1h01m"


# ---------------------------------------------------------------------------
# Single-task rendering
# ---------------------------------------------------------------------------

class TestRenderMetric:
    def test_header_shows_status_glyph_fault_and_short_id(self):
        out = render_metric(_task_data())
        assert "✓ inject-3198b391" in out
        assert "success" in out
        assert "pod-process-stop" in out

    def test_target_line_includes_ns_name_and_params(self):
        out = render_metric(_task_data())
        assert "default/demo-pod-abc" in out
        assert "process=nginx" in out

    def test_cost_line_counts_all_four_rollups(self):
        out = render_metric(_task_data())
        assert "683002↓ 28545↑ tokens" in out
        assert "LLM ×25" in out
        assert "tools ×39" in out

    def test_verification_layers_rendered_with_verdict(self):
        out = render_metric(_task_data())
        assert "L1 skipped" in out
        assert "L2 passed" in out
        assert "workers stopped" in out

    def test_warning_is_truncated_to_first_sentence(self):
        out = render_metric(_task_data())
        assert "long warning text." in out
        assert "x" * 300 not in out

    def test_waterfall_lists_all_spans_in_order(self):
        out = render_metric(_task_data())
        lines = out.splitlines()
        names = [line.strip().split()[0] for line in lines
                 if line.startswith("  ") and ("intent_confirm" in line
                 or "agent_loop" in line or "execute_loop" in line)]
        assert names == ["intent_confirm", "agent_loop", "execute_loop"]

    def test_interrupted_span_marked_with_pause_not_error(self):
        out = render_metric(_task_data())
        row = next(line for line in out.splitlines() if "intent_confirm" in line)
        assert "⏸" in row
        assert "✗" not in row

    def test_error_span_marked_with_cross(self):
        out = render_metric(_task_data())
        row = next(line for line in out.splitlines() if "execute_loop" in line)
        assert "✗" in row

    def test_span_tokens_and_tool_counts_rendered(self):
        out = render_metric(_task_data())
        row = next(line for line in out.splitlines() if "agent_loop" in line)
        assert "10023↓807↑" in row
        assert "kubectl_read×2" in row

    def test_task_level_error_rendered_at_bottom(self):
        data = _task_data()
        data["error"] = "injection exploded"
        out = render_metric(data)
        assert "injection exploded" in out

    def test_no_spans_shows_placeholder(self):
        data = _task_data()
        data["spans"] = []
        out = render_metric(data)
        assert "(none recorded)" in out

    def test_color_mode_emits_ansi(self):
        out = render_metric(_task_data(), color=True)
        assert "\033[32m" in out
        plain = render_metric(_task_data(), color=False)
        assert "\033[" not in plain


# ---------------------------------------------------------------------------
# List rendering
# ---------------------------------------------------------------------------

class TestRenderMetricList:
    def test_table_has_header_and_row(self):
        out = render_metric_list(_list_data())
        assert "Tasks: 1" in out
        assert "inject-3198b391" in out
        assert "pod-process-stop" in out

    def test_empty_store_message(self):
        assert render_metric_list({"total": 0, "tasks": []}) == \
            "No tasks recorded yet."

    def test_table_has_no_blank_margin_lines_and_columns(self):
        out = render_metric_list(_list_data())
        lines = out.splitlines()
        assert all(line.strip() for line in lines)  # no blank margin rows
        assert all(line == line.rstrip() for line in lines)  # no trailing pad
        header = lines[1]
        for col in ("TASK", "STATUS", "FAULT", "PHASE", "DUR", "CREATED"):
            assert col in header

    def test_color_mode_only_affects_ansi_not_layout(self):
        plain = render_metric_list(_list_data(), color=False)
        colored = render_metric_list(_list_data(), color=True)
        assert "\033[" not in plain
        assert "\033[" in colored
        # Visible layout must be identical — and no trailing padding may
        # survive after ANSI-stripping in either mode.
        def strip_ansi(s: str) -> str:
            return re.sub(r"\033\[[0-9;]*m", "", s)
        plain_lines = plain.splitlines()
        colored_visible = [strip_ansi(line).rstrip()
                           for line in colored.splitlines()]
        assert colored_visible == plain_lines

    def test_render_text_dispatches_on_envelope_shape(self):
        assert "Node Spans" in render_text(_task_data())
        assert "Tasks: 1" in render_text(_list_data())


# ---------------------------------------------------------------------------
# Command wiring
# ---------------------------------------------------------------------------

class _FakeBackend:
    def __init__(self, envelope):
        self._envelope = envelope

    async def metric(self, task_id):
        return self._envelope

    async def cleanup(self):
        pass


class TestMetricCommandTextOutput:
    def test_default_output_is_rendered_text(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        env = {"code": 0, "message": "success", "data": _task_data()}
        monkeypatch.setattr(mod, "get_backend", lambda: _FakeBackend(env))
        result = runner.invoke(
            _app(mod.metric_command), ["--task-id", "inject-3198"]
        )
        assert result.exit_code == 0
        assert "Node Spans" in result.output
        assert "✓ inject-3198b391" in result.output
        # Raw JSON keys must NOT leak into the human view
        assert '"task_state"' not in result.output

    def test_json_flag_still_dumps_raw_envelope(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        env = {"code": 0, "message": "success", "data": _task_data()}
        monkeypatch.setattr(mod, "get_backend", lambda: _FakeBackend(env))
        result = runner.invoke(
            _app(mod.metric_command), ["--task-id", "inject-3198", "-o", "json"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["data"]["summary"]["total_tool_calls"] == 39

    def test_failed_envelope_shows_message_and_exits_nonzero(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        env = {"code": 1, "message": "Task not found: ghost", "data": None}
        monkeypatch.setattr(mod, "get_backend", lambda: _FakeBackend(env))
        result = runner.invoke(
            _app(mod.metric_command), ["--task-id", "ghost"]
        )
        assert result.exit_code == 1
        assert "✗ Task not found: ghost" in result.output

    def test_list_view_when_no_task_id(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        env = {"code": 0, "message": "success", "data": _list_data()}
        monkeypatch.setattr(mod, "get_backend", lambda: _FakeBackend(env))
        result = runner.invoke(_app(mod.metric_command), [])
        assert result.exit_code == 0
        assert "Tasks: 1" in result.output
