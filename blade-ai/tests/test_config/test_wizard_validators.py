"""Contract tests for wizard_validators resource hygiene.

Regression: a 2026-08 deep review found two leaks —
  1. validate_api_key never closed its AsyncOpenAI client (httpx pool
     leaked on every exit path);
  2. discover_kube_contexts killed the kubectl child on timeout but
     never reaped it (zombie + leaked stdout pipe fd).
"""

import asyncio
import sys
import types

import pytest

from chaos_agent.config import wizard_validators


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeModels:
    def __init__(self, outcome):
        self._outcome = outcome

    async def list(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class _FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI; records close() calls."""

    instances: list = []
    outcome = None  # set per-test: return value or exception for list()

    def __init__(self, **kwargs):
        self.closed = False
        self.models = _FakeModels(type(self).outcome)
        type(self).instances.append(self)

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    fake_module = types.SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return _FakeAsyncOpenAI


# ---------------------------------------------------------------------------
# validate_api_key — client must be closed on every exit path
# ---------------------------------------------------------------------------


class TestApiKeyClientLifecycle:
    async def test_client_closed_on_success(self, fake_openai):
        fake_openai.outcome = types.SimpleNamespace(
            data=[types.SimpleNamespace(id="qwen-max")],
        )
        result = await wizard_validators.validate_api_key(
            "sk-test", "https://api.example.com/v1",
        )
        assert result.status == "ok"
        assert len(fake_openai.instances) == 1
        assert fake_openai.instances[0].closed is True

    async def test_client_closed_on_auth_error(self, fake_openai):
        fake_openai.outcome = RuntimeError("401 Unauthorized")
        result = await wizard_validators.validate_api_key(
            "sk-bad", "https://api.example.com/v1",
        )
        assert result.status == "error" and result.block is True
        assert fake_openai.instances[0].closed is True

    async def test_client_closed_on_generic_error(self, fake_openai):
        fake_openai.outcome = ConnectionError("connection refused")
        result = await wizard_validators.validate_api_key(
            "sk-test", "https://api.example.com/v1",
        )
        assert result.status == "warn"
        assert fake_openai.instances[0].closed is True


# ---------------------------------------------------------------------------
# discover_kube_contexts — timed-out child must be killed AND reaped
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.sleep(3600)  # never finishes; wait_for must trip

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return -9


class TestKubectlTimeoutReaping:
    async def test_timeout_kills_and_reaps_child(self, monkeypatch):
        fake_proc = _FakeProc()

        async def _fake_exec(*args, **kwargs):
            return fake_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "chaos_agent.utils.blade_paths.resolve_exec_path",
            lambda name: "/usr/local/bin/kubectl",
        )

        contexts = await wizard_validators.discover_kube_contexts("/tmp/kc")

        assert contexts == []
        assert fake_proc.killed is True, "timeout path must kill the child"
        assert fake_proc.waited is True, (
            "kill() must be followed by wait() — otherwise the child "
            "stays a zombie and its stdout pipe fd leaks"
        )
