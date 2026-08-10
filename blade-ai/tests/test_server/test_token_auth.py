"""Contract tests for TokenAuthMiddleware and AgentClient bearer headers.

The API exposes fault injection and binds 0.0.0.0 by default, so the
token gate is the only network-level protection. These tests pin the
opt-in semantics: empty token = auth disabled (embedded/loopback flows
unchanged), configured token = every request must carry it.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaos_agent.config.settings import settings
from chaos_agent.server.middleware import TokenAuthMiddleware, set_token_auth_bypass


def _make_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware)

    @app.get("/ping")
    def ping():
        return {"pong": True}

    return TestClient(app)


class TestTokenAuthMiddleware:
    def test_disabled_when_token_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "")
        resp = _make_client().get("/ping")
        assert resp.status_code == 200

    def test_disabled_when_token_whitespace(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "   ")
        resp = _make_client().get("/ping")
        assert resp.status_code == 200

    def test_rejects_missing_header(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        resp = _make_client().get("/ping")
        assert resp.status_code == 401
        assert resp.json()["code"] == 4011

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        resp = _make_client().get("/ping", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_rejects_non_bearer_scheme(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        resp = _make_client().get("/ping", headers={"Authorization": "Basic sekrit"})
        assert resp.status_code == 401

    def test_accepts_valid_token(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        resp = _make_client().get("/ping", headers={"Authorization": "Bearer sekrit"})
        assert resp.status_code == 200
        assert resp.json() == {"pong": True}

    def test_non_ascii_token_gates_cleanly(self, monkeypatch):
        """A non-ASCII token must not crash the middleware.

        ``hmac.compare_digest`` raises TypeError for non-ASCII str
        operands, so before the bytes-comparison fix ANY request against
        a server with such a token died with a 500. The contract here
        is "reject cleanly, never crash": HTTP header values are
        byte-restricted, so a non-ASCII token can never match over the
        wire anyway — every request must simply get a 401.
        """
        monkeypatch.setattr(settings, "server_token", "密码-Ω-42")
        client = _make_client()
        assert client.get("/ping").status_code == 401
        assert client.get(
            "/ping", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        assert client.get(
            "/ping", headers={"Authorization": "Basic c2Vrcml0"}
        ).status_code == 401


class TestEmbeddedServerBypass:
    """The TS TUI's embedded server must never be token-gated.

    The TUI spawns it on loopback itself and sends no Authorization
    header — a server_token in config.json would 401-block the entire
    TUI. The bypass is process-level and set ONLY by the embedded entry
    points (run_server(embedded=True)); the public server command keeps
    the gate.
    """

    def test_bypass_skips_gate_even_with_token(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        set_token_auth_bypass(True)
        try:
            resp = _make_client().get("/ping")
            assert resp.status_code == 200
        finally:
            # Module-global flag — always restore, or later tests in the
            # same process would run with auth silently disabled.
            set_token_auth_bypass(False)

    def test_public_server_path_keeps_gate(self, monkeypatch):
        """With the bypass off (the public command's state), the gate holds."""
        monkeypatch.setattr(settings, "server_token", "sekrit")
        set_token_auth_bypass(False)
        resp = _make_client().get("/ping")
        assert resp.status_code == 401


class TestAgentClientAuthHeaders:
    def test_no_header_without_token(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "")
        from chaos_agent.cli.client import AgentClient

        assert AgentClient(base_url="http://localhost:1")._auth_headers() == {}

    def test_bearer_header_with_token(self, monkeypatch):
        monkeypatch.setattr(settings, "server_token", "sekrit")
        from chaos_agent.cli.client import AgentClient

        assert AgentClient(base_url="http://localhost:1")._auth_headers() == {
            "Authorization": "Bearer sekrit"
        }


class TestServerTokenMasking:
    def test_server_token_is_sensitive(self):
        """config get/list must mask the gate token like the API key."""
        from chaos_agent.cli.config_manager import SENSITIVE_KEYS

        assert "server_token" in SENSITIVE_KEYS
