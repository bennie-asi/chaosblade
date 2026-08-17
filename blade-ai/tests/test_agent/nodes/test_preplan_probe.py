"""Tests for the preplan_probe node (fresh pre-task probes).

The probe bundle is published as ONE observation message appended to
``messages`` — persisted with the conversation, visible to every later
agent_loop entry (including replan re-entries) as plain history.
"""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import SystemMessage

from chaos_agent.agent.nodes.gates import preplan_probe as pp
from chaos_agent.agent.nodes.gates.preplan_probe import preplan_probe
from chaos_agent.config.settings import settings

_MODULE = "chaos_agent.agent.nodes.gates.preplan_probe"

# Minimal probe set: only probes with proven in-loop rediscovery cost stay
# here; the safety-domain probes live in the Phase 2 gate exclusively.
_ALL_PROBES = (
    "_probe_operator",
    "_probe_metrics_server",
)


def _ok(name: str):
    return AsyncMock(return_value=("ok", f"{name} fine", {}))


@pytest.fixture
def k8s_state(sample_agent_state, monkeypatch):
    """State resolving to a reachable K8s channel."""
    monkeypatch.setattr(settings, "kubeconfig_path", "/fake/kubeconfig")
    monkeypatch.setattr(settings, "kube_connection_mode", "kubeconfig")
    monkeypatch.setattr(settings, "preplan_probes_enabled", True)
    return sample_agent_state


def _run_probes(state, overrides: dict | None = None):
    """Patch every probe function (ok by default) and run the node."""
    mocks = {name: _ok(name) for name in _ALL_PROBES}
    if overrides:
        mocks.update(overrides)
    patches = [patch(f"{_MODULE}.{name}", new=m) for name, m in mocks.items()]
    for p in patches:
        p.start()
    return mocks, patches


class TestSkipMarkers:
    """Skip paths emit nothing — no message, no state change."""

    @pytest.mark.asyncio
    async def test_disabled_via_settings(self, k8s_state, monkeypatch):
        monkeypatch.setattr(settings, "preplan_probes_enabled", False)
        with patch(f"{_MODULE}._probe_operator") as probe:
            result = await preplan_probe(k8s_state)
        assert result == {}
        probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_mode_skips(self, k8s_state):
        k8s_state["direct"] = True
        with patch(f"{_MODULE}._probe_operator") as probe:
            result = await preplan_probe(k8s_state)
        assert result == {}
        probe.assert_not_called()


class TestK8sHappyPath:
    @pytest.mark.asyncio
    async def test_publishes_one_observation_message(self, k8s_state):
        mocks, patches = _run_probes(k8s_state)
        try:
            result = await preplan_probe(k8s_state)
        finally:
            for p in patches:
                p.stop()

        for m in mocks.values():
            m.assert_awaited_once()
        # Exactly ONE message, appended to the conversation history.
        messages = result["messages"]
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, SystemMessage)
        assert "Pre-task environment probes" in msg.content
        assert "- chaosblade_operator [ok]: _probe_operator fine" in msg.content
        assert "- metrics_server [ok]: _probe_metrics_server fine" in msg.content
        # Hint-not-verdict discipline is carried in the message body.
        assert "planning hints, not safety verdicts" in msg.content
        # No dedicated state field anymore.
        assert "preplan_probes" not in result

    @pytest.mark.asyncio
    async def test_probe_failure_degrades_to_unknown(self, k8s_state):
        """A failing probe never blocks the node — it becomes 'unknown'."""
        mocks, patches = _run_probes(k8s_state, overrides={
            "_probe_metrics_server": AsyncMock(side_effect=RuntimeError("boom")),
        })
        try:
            result = await preplan_probe(k8s_state)
        finally:
            for p in patches:
                p.stop()

        msg = result["messages"][0]
        assert "metrics_server [unknown]: probe failed: boom" in msg.content
        # The other probe still landed in the same message.
        assert "chaosblade_operator [ok]" in msg.content

    @pytest.mark.asyncio
    async def test_probe_timeout_degrades_to_unknown(self, k8s_state, monkeypatch):
        monkeypatch.setattr(settings, "preplan_probe_timeout", 0.05)

        async def _slow(*_args, **_kwargs):
            import asyncio

            await asyncio.sleep(0.5)
            return "ok", "never reached", {}

        mocks, patches = _run_probes(k8s_state, overrides={
            "_probe_metrics_server": _slow,
        })
        try:
            result = await preplan_probe(k8s_state)
        finally:
            for p in patches:
                p.stop()

        msg = result["messages"][0]
        assert "metrics_server [unknown]" in msg.content
        assert "timed out" in msg.content


class TestHostChannel:
    @pytest.mark.asyncio
    async def test_host_channel_emits_nothing(self, sample_agent_state, monkeypatch):
        monkeypatch.setattr(settings, "preplan_probes_enabled", True)
        # Force a host-scope channel regardless of connection fields.
        monkeypatch.setattr(pp, "resolve_channel_name", lambda state: "ssh")
        probes_mocks = {
            name: AsyncMock(return_value=("ok", "should never run", {}))
            for name in _ALL_PROBES
        }
        patches = [patch(f"{_MODULE}.{name}", new=m) for name, m in probes_mocks.items()]
        for p in patches:
            p.start()
        try:
            result = await preplan_probe(sample_agent_state)
        finally:
            for p in patches:
                p.stop()

        # No message, no state change — zero noise on the host channel.
        assert result == {}
        for m in probes_mocks.values():
            m.assert_not_called()


class TestOperatorFallbackAttribution:
    """_probe_operator attaches node attribution to the fallback hint.

    Node attribution is the load-bearing fact: the fallback carrier for any
    fault family is the tool pod on the target node (inject-ccfadf7d spent
    ~5 LLM rounds rediscovering exactly this).
    """

    _DETECT = (
        "chaos_agent.agent.nodes.execute._injection_detection."
        "discover_tool_pods_cluster_wide_with_nodes"
    )
    _PODS = [
        ("chaosblade-tool-abc", "chaosblade", "node-a"),
        ("chaosblade-tool-def", "chaosblade", "node-b"),
    ]

    @staticmethod
    def _op_result(passed: bool):
        from types import SimpleNamespace

        return SimpleNamespace(passed=passed, message="operator not ready", fix="")

    @pytest.mark.asyncio
    async def test_fallback_lists_pods_with_nodes(self):
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(False)),
        ), patch(self._DETECT, AsyncMock(return_value=self._PODS)):
            status, summary, detail = await pp._probe_operator("/fake/kc")
        assert status == "warning"
        assert "chaosblade-tool-abc on node-a" in summary
        assert "chaosblade-tool-def on node-b" in summary
        # Broken operator -> heuristic consequence statement only; never an
        # imperative planning directive (path choice stays with the planner).
        assert "will not be reconciled" in summary
        assert "planning directive" not in summary
        assert detail["tool_pods"][0] == {
            "pod": "chaosblade-tool-abc", "namespace": "chaosblade",
            "node": "node-a",
        }
        # No target_node -> pod/container scope: no exec-carrier claims.
        assert "PRIMARY injection" not in summary

    @pytest.mark.asyncio
    async def test_target_node_carrier_is_highlighted(self):
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(False)),
        ), patch(self._DETECT, AsyncMock(return_value=self._PODS)):
            _status, summary, detail = await pp._probe_operator(
                "/fake/kc", target_node="node-b",
            )
        assert "ON the target node node-b: chaosblade/chaosblade-tool-def" in summary
        assert detail["tool_pod_on_target"] == {
            "pod": "chaosblade-tool-def", "namespace": "chaosblade",
            "node": "node-b",
        }
        # Carrier presence is stated as fact; no imperative path ordering.
        assert "planning directive" not in summary

    @pytest.mark.asyncio
    async def test_target_node_missing_from_roster_is_flagged(self):
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(False)),
        ), patch(self._DETECT, AsyncMock(return_value=self._PODS)):
            _status, summary, _detail = await pp._probe_operator(
                "/fake/kc", target_node="node-x",
            )
        assert "none on target node node-x" in summary
        # Unavailability is reported as a fact; the planner decides the path.
        assert "currently unavailable there" in summary
        assert "planning directive" not in summary

    @pytest.mark.asyncio
    async def test_no_tool_pods_anywhere_reports_fallback_unavailable(self):
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(False)),
        ), patch(self._DETECT, AsyncMock(return_value=[])):
            _status, summary, _detail = await pp._probe_operator(
                "/fake/kc", target_node="node-x",
            )
        assert "no tool pods found anywhere" in summary
        assert "planning directive" not in summary

    @pytest.mark.asyncio
    async def test_discovery_failure_reports_unverified(self):
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(False)),
        ), patch(self._DETECT, AsyncMock(side_effect=RuntimeError("boom"))):
            _status, summary, _detail = await pp._probe_operator(
                "/fake/kc", target_node="node-x",
            )
        # Unknown is reported as unknown — the planner verifies in-loop.
        assert "carrier availability currently unknown" in summary
        assert "planning directive" not in summary

    @pytest.mark.asyncio
    async def test_operator_healthy_skips_fallback_discovery(self):
        discover = AsyncMock(return_value=self._PODS)
        with patch(
            "chaos_agent.preflight.check_chaosblade_operator",
            AsyncMock(return_value=self._op_result(True)),
        ), patch(self._DETECT, discover):
            status, summary, _detail = await pp._probe_operator(
                "/fake/kc", target_node="node-a",
            )
        discover.assert_not_awaited()
        assert status == "ok"
        assert "tool pods" not in summary
        # Healthy operator -> no consequence statement, no directive.
        assert "planning directive" not in summary
        assert "will not be reconciled" not in summary


class TestTargetNodePlumbing:
    """Node-scope specs name the target node; the probe receives it."""

    @pytest.mark.asyncio
    async def test_node_scope_spec_passes_target_node(self, k8s_state):
        from chaos_agent.agent.spec.fault_spec import FaultSpec

        k8s_state["fault_spec"] = FaultSpec(
            scope="node", names=("node-b",),
            blade_target="mem", blade_action="load",
        ).to_dict()
        mocks, patches = _run_probes(k8s_state)
        try:
            await preplan_probe(k8s_state)
        finally:
            for p in patches:
                p.stop()
        assert mocks["_probe_operator"].await_args.kwargs["target_node"] == "node-b"

    @pytest.mark.asyncio
    async def test_pod_scope_spec_passes_no_target_node(self, k8s_state):
        from chaos_agent.agent.spec.fault_spec import FaultSpec

        k8s_state["fault_spec"] = FaultSpec(
            scope="pod", namespace="cms-demo", labels={"app": "myapp"},
            blade_target="cpu", blade_action="fullload",
        ).to_dict()
        mocks, patches = _run_probes(k8s_state)
        try:
            await preplan_probe(k8s_state)
        finally:
            for p in patches:
                p.stop()
        assert mocks["_probe_operator"].await_args.kwargs["target_node"] == ""
