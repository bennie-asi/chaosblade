"""Exit-code contract: failed envelopes must exit the process non-zero.

CI/shell consumers decide on $?, not on the JSON envelope's code field —
an injection that failed while the process exited 0 would silently skip
every ``|| rollback`` branch. The envelope is still printed (machines
parse it), but the process status now reflects it.
"""

import json

import typer
from typer.testing import CliRunner

runner = CliRunner()

_FAILED = {"code": 1, "message": "boom", "data": None}
_OK = {"code": 0, "message": "success", "data": {"task_id": "t"}}


def _app(command) -> typer.Typer:
    app = typer.Typer()
    app.command()(command)
    return app


class TestInjectExitCode:
    def test_failed_envelope_exits_nonzero(self, monkeypatch):
        import chaos_agent.cli.commands.inject as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _FAILED)
        result = runner.invoke(_app(mod.inject_command), ["-i", "cpu fault"])
        assert result.exit_code == 1
        # The envelope must still be printed for JSON consumers.
        assert json.loads(result.output)["code"] == 1

    def test_success_envelope_exits_zero(self, monkeypatch):
        import chaos_agent.cli.commands.inject as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _OK)
        result = runner.invoke(_app(mod.inject_command), ["-i", "cpu fault"])
        assert result.exit_code == 0


class TestRecoverExitCode:
    def test_failed_envelope_exits_nonzero(self, monkeypatch):
        import chaos_agent.cli.commands.recover as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _FAILED)
        result = runner.invoke(_app(mod.recover_command), ["--task-id", "t1"])
        assert result.exit_code == 1

    def test_success_envelope_exits_zero(self, monkeypatch):
        import chaos_agent.cli.commands.recover as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _OK)
        result = runner.invoke(_app(mod.recover_command), ["--task-id", "t1"])
        assert result.exit_code == 0


class TestConfirmExitCode:
    def test_failed_envelope_exits_nonzero(self, monkeypatch):
        import chaos_agent.cli.commands.confirm as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _FAILED)
        result = runner.invoke(
            _app(mod.confirm_command), ["--task-id", "t1", "--action", "approve"]
        )
        assert result.exit_code == 1

    def test_success_envelope_exits_zero(self, monkeypatch):
        import chaos_agent.cli.commands.confirm as mod

        monkeypatch.setattr(mod, "run_command", lambda c, local, s: _OK)
        result = runner.invoke(
            _app(mod.confirm_command), ["--task-id", "t1", "--action", "approve"]
        )
        assert result.exit_code == 0


class TestMetricExitCode:
    class _FakeBackend:
        def __init__(self, envelope):
            self._envelope = envelope

        async def metric(self, task_id):
            return self._envelope

        async def cleanup(self):
            pass

    def test_failed_envelope_exits_nonzero(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        monkeypatch.setattr(mod, "get_backend", lambda: self._FakeBackend(_FAILED))
        result = runner.invoke(_app(mod.metric_command), [])
        assert result.exit_code == 1

    def test_success_envelope_exits_zero(self, monkeypatch):
        import chaos_agent.cli.commands.metric as mod

        monkeypatch.setattr(mod, "get_backend", lambda: self._FakeBackend(_OK))
        result = runner.invoke(_app(mod.metric_command), [])
        assert result.exit_code == 0
