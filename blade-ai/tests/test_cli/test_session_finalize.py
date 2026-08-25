"""auto_rollback: failure-path rollback dispatched by fault handle.

The seam must behave identically for every carrier: blade-family handles
dispatch a UID destroy through the provider registry, UID-less native
handles decline (the recover graph's job), and errors surface as a
readable suffix instead of crashing finalize.
"""

import pytest

from chaos_agent.cli.session_finalize import auto_rollback

CONFIG = {"configurable": {"thread_id": "t-1"}}


class _FakeSnapshot:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    def __init__(self, values=None, raise_exc=None):
        self._values = values
        self._raise = raise_exc

    async def aget_state(self, config):
        if self._raise is not None:
            raise self._raise
        return _FakeSnapshot(self._values)


@pytest.mark.asyncio
async def test_blade_handle_dispatches_destroy(monkeypatch):
    """Legacy checkpoint shape: only the UID fact survives (no fault_handle),
    hydration derives the blade handle and the registry dispatches destroy."""
    from chaos_agent.agent.providers.chaosblade import cli as blade_tools_mod

    calls = []

    class _FakeDestroy:
        async def ainvoke(self, args):
            calls.append(args)
            return "destroyed"

    monkeypatch.setattr(blade_tools_mod, "blade_destroy", _FakeDestroy())
    graph = _FakeGraph({"experiment_uid": "uid-1", "kubeconfig": "/tmp/kc"})
    suffix = await auto_rollback(graph, CONFIG)
    assert suffix == " (auto-rolled back experiment_uid=uid-1)"
    assert calls == [{"uid": "uid-1", "kubeconfig": "/tmp/kc"}]


@pytest.mark.asyncio
async def test_carried_handle_wins_over_stale_legacy_uid(monkeypatch):
    """An already-projected handle wins over re-derivation: dispatch must
    follow the HANDLE's value even when a stale legacy UID lingers."""
    from chaos_agent.agent.providers.chaosblade import cli as blade_tools_mod

    captured = {}

    class _CapturingDestroy:
        async def ainvoke(self, args):
            captured.update(args)
            return "destroyed"

    monkeypatch.setattr(blade_tools_mod, "blade_destroy", _CapturingDestroy())
    graph = _FakeGraph({
        "experiment_uid": "stale-uid",
        "fault_handle": {"kind": "experiment_uid", "value": "live-uid", "method": "host_blade"},
        "kubeconfig": "",
    })
    suffix = await auto_rollback(graph, CONFIG)
    assert suffix == " (auto-rolled back experiment_uid=live-uid)"
    assert captured["uid"] == "live-uid"


@pytest.mark.asyncio
async def test_native_handle_declines_rollback():
    """UID-less native carriers decline the synchronous failure-path rollback;
    their safety net is the explicit recover graph."""
    graph = _FakeGraph({"injection_method": "kubectl_native"})
    assert await auto_rollback(graph, CONFIG) == ""


@pytest.mark.asyncio
async def test_no_attribution_is_a_noop():
    graph = _FakeGraph({"safety_status": "rejected"})
    assert await auto_rollback(graph, CONFIG) == ""


@pytest.mark.asyncio
async def test_state_read_failure_surfaces_suffix():
    graph = _FakeGraph(raise_exc=RuntimeError("checkpoint gone"))
    suffix = await auto_rollback(graph, CONFIG)
    assert suffix.startswith(" (rollback FAILED:")


def test_server_inject_routes_use_shared_auto_rollback():
    """The HTTP inject routes must dispatch through the single tested seam —
    a route-local twin copy of the rollback logic is how the CLI and server
    paths drifted before (guard against re-inlining)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in (
        "src/chaos_agent/server/routes/inject.py",
        "src/chaos_agent/server/routes/inject_stream.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "from chaos_agent.cli.session_finalize import auto_rollback" in text, (
            f"{rel}: must dispatch via the shared auto_rollback seam"
        )
        assert "rollback_handle(" not in text, (
            f"{rel}: must not inline a direct registry rollback dispatch"
        )
        assert "blade_destroy" not in text, (
            f"{rel}: must not inline a carrier-specific destroy"
        )
