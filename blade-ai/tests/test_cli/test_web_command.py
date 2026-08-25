"""Tests for the public ``blade-ai web`` command.

Pins the launch contract: loopback-only embedded server (the token gate
is bypassed, so offering a non-loopback bind would expose an
unauthenticated fault-injection API to the LAN — there is intentionally
no ``--host``), a browser tab fired only once /health answers, and a
loud exit when no Web UI bundle resolves.
"""

from unittest.mock import patch

from typer.testing import CliRunner

from chaos_agent.cli.commands import web as web_mod
from chaos_agent.cli.main import app

runner = CliRunner()


class TestWebCommandSurface:
    def test_web_is_visible_in_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "web" in result.output

    def test_web_takes_no_host_option(self):
        """Loopback-only is a security property, not a missing flag."""
        result = runner.invoke(app, ["web", "--help"])
        assert result.exit_code == 0
        assert "--host" not in result.output


class TestWebDelegation:
    def test_explicit_port_and_no_browser(self, tmp_path):
        with (
            patch("chaos_agent.server.web.resolve_web_dist", return_value=tmp_path),
            patch("chaos_agent.server.app.run_server") as run_server,
        ):
            result = runner.invoke(app, ["web", "--no-browser", "--port", "9000"])

        assert result.exit_code == 0
        assert run_server.call_args.kwargs == {
            "host": "127.0.0.1",
            "port": 9000,
            "embedded": True,
        }

    def test_port_zero_asks_the_os(self, tmp_path):
        with (
            patch("chaos_agent.server.web.resolve_web_dist", return_value=tmp_path),
            patch("chaos_agent.server.app.run_server") as run_server,
        ):
            result = runner.invoke(app, ["web", "--no-browser"])

        assert result.exit_code == 0
        kwargs = run_server.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert isinstance(kwargs["port"], int) and kwargs["port"] > 0
        assert kwargs["embedded"] is True

    def test_browser_thread_starts_unless_disabled(self, tmp_path):
        with (
            patch("chaos_agent.server.web.resolve_web_dist", return_value=tmp_path),
            patch("chaos_agent.server.app.run_server"),
            patch.object(web_mod, "threading") as mock_threading,
        ):
            result = runner.invoke(app, ["web", "--port", "9000"])

        assert result.exit_code == 0
        mock_threading.Thread.assert_called_once()
        call = mock_threading.Thread.call_args
        assert call.kwargs["target"] is web_mod._open_browser_when_ready
        assert call.kwargs["args"] == ("http://127.0.0.1:9000",)
        assert call.kwargs["daemon"] is True
        mock_threading.Thread.return_value.start.assert_called_once()

    def test_no_browser_skips_the_thread(self, tmp_path):
        with (
            patch("chaos_agent.server.web.resolve_web_dist", return_value=tmp_path),
            patch("chaos_agent.server.app.run_server"),
            patch.object(web_mod, "threading") as mock_threading,
        ):
            result = runner.invoke(app, ["web", "--no-browser"])

        assert result.exit_code == 0
        mock_threading.Thread.assert_not_called()

    def test_no_bundle_exits_loud(self, monkeypatch):
        # Fail-closed env override: resolve_web_dist returns None even
        # though this repo has a built web/dist.
        monkeypatch.setenv("BLADE_AI_WEB_DIST", "/nonexistent-web-dist")
        with patch("chaos_agent.server.app.run_server") as run_server:
            result = runner.invoke(app, ["web", "--no-browser"])

        assert result.exit_code == 1
        assert "web/dist" in result.stderr
        assert not run_server.called


class TestAllocateLoopbackPort:
    def test_returns_a_free_port(self):
        port = web_mod._allocate_loopback_port()
        assert isinstance(port, int)
        assert 0 < port <= 65535


class TestOpenBrowserWhenReady:
    def test_opens_once_health_answers(self, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(web_mod.webbrowser, "open", opened.append)

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(
            web_mod.urllib.request, "urlopen", lambda *a, **k: _Resp(),
        )

        web_mod._open_browser_when_ready("http://127.0.0.1:1")
        assert opened == ["http://127.0.0.1:1"]

    def test_opens_anyway_after_timeout(self, monkeypatch):
        """A genuinely broken server surfaces as a browser error page,
        not as silence — the tab opens even when /health never answers."""
        opened: list[str] = []
        monkeypatch.setattr(web_mod.webbrowser, "open", opened.append)
        monkeypatch.setattr(web_mod, "_HEALTH_TIMEOUT_S", 0.05)
        monkeypatch.setattr(web_mod.time, "sleep", lambda *_: None)

        def _down(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(web_mod.urllib.request, "urlopen", _down)

        web_mod._open_browser_when_ready("http://127.0.0.1:1")
        assert opened == ["http://127.0.0.1:1"]
