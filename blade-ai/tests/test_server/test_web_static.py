"""Contract tests for the Web UI static hosting (chaos_agent.server.web).

Pins the three guarantees the SPA mount must keep:

1. SPA fallback — extension-less non-API paths serve index.html so
   client-side routes (/tasks, /replay, …) survive a browser refresh.
2. API priority — /api/* is never swallowed by the fallback: known
   endpoints answer normally, unknown ones stay a JSON 404 (a 200 HTML
   page would make client-side error handling parse HTML as JSON).
3. Fail-closed resolution — an invalid BLADE_AI_WEB_DIST override
   yields the actionable 404 hint, not a silent fallthrough to another
   copy of the bundle.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaos_agent.server.app import create_app
from chaos_agent.server.web import mount_web_ui, resolve_web_dist


@pytest.fixture
def web_dist(tmp_path):
    """A minimal but valid SPA bundle: index.html + one hashed asset."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>blade-ai-test</title>", encoding="utf-8",
    )
    (dist / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    return dist


@pytest.fixture
def client(web_dist, monkeypatch):
    monkeypatch.setenv("BLADE_AI_WEB_DIST", str(web_dist))
    return TestClient(create_app())


class TestSPAHosting:
    def test_root_serves_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "blade-ai-test" in r.text

    def test_client_route_falls_back_to_index(self, client):
        # /tasks exists only in the client router — a refresh on it
        # must serve the SPA shell, not 404.
        r = client.get("/tasks")
        assert r.status_code == 200
        assert "blade-ai-test" in r.text

    def test_real_asset_is_served(self, client):
        r = client.get("/assets/app.css")
        assert r.status_code == 200
        assert r.text == "body{}"

    def test_missing_asset_is_a_real_404(self, client):
        # The path has an extension → not an SPA route → no fallback.
        r = client.get("/assets/missing.js")
        assert r.status_code == 404

    def test_api_route_wins_over_the_catch_all_mount(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_unknown_api_path_stays_a_json_404(self, client):
        r = client.get("/api/v1/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")

    def test_bare_api_path_stays_a_json_404(self, client):
        # "/api" has no trailing slash — a startswith("/api/")-only
        # guard would miss it and serve 200 HTML.
        r = client.get("/api")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")


class TestResolveWebDist:
    def test_env_override_fail_closed(self, tmp_path, monkeypatch):
        """A bogus explicit override must surface, not heal itself by
        falling through to the repo / wheel copy."""
        monkeypatch.setenv("BLADE_AI_WEB_DIST", str(tmp_path / "nope"))
        assert resolve_web_dist() is None

    def test_env_override_requires_index_html(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BLADE_AI_WEB_DIST", str(tmp_path))
        assert resolve_web_dist() is None
        (tmp_path / "index.html").write_text("x", encoding="utf-8")
        assert resolve_web_dist() == tmp_path


class TestMissingBundle:
    def test_root_serves_actionable_404_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BLADE_AI_WEB_DIST", str(tmp_path / "nope"))
        client = TestClient(create_app())
        r = client.get("/")
        assert r.status_code == 404
        assert r.json()["error"] == "web_ui_not_bundled"

    def test_mount_web_ui_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BLADE_AI_WEB_DIST", str(tmp_path / "nope"))
        assert mount_web_ui(FastAPI()) is None
