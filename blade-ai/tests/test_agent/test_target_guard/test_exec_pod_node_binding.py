"""Regression tests for the exec-pod node binding (task-ccfadf7d).

Incident: an APPROVED node-scope fault (``blade create mem load`` on
node ``cn-shanghai-cloudspe.10.0.0.119``) was executed the documented
way — ``kubectl exec <chaosblade-tool pod on that node> -- blade create
mem load ...`` — and the screener logged ``reject_drift [identity_drift]
resource selection drift: approved.names=[...] vs effective.<no-selector>``.
The target never changed: a HOST-level blade command inside a kubectl
exec carries no selector because the fault lands on whatever node hosts
the exec'd pod — the pod IS the node binding.

Fix shape (DATA-side, aligned with the vehicle exemption):

  - the classifier stays static: it records the exec'd pod's identity on
    ``EffectiveTarget`` (``exec_pod_name`` / ``exec_pod_namespace``) for
    the selector-less node-scoped inline blade shape and never guesses a
    node name;
  - the screener resolves the pod's nodeName with one bounded, cached
    in-band read (``exec_pod_node_bindings``) and pins it as the
    effective name before the drift comparison;
  - a binding that resolves OUTSIDE the approved name set is genuine
    drift; one that cannot be resolved keeps the fail-closed review.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from chaos_agent.agent.nodes.planning.tool_screener import (
    SCREENER_ROUTE_PASS,
    SCREENER_ROUTE_RETRY,
    tool_screener,
)
from chaos_agent.agent.target_guard import freeze_approved_target
from chaos_agent.agent.target_guard.classifier import infer_effective_target
from chaos_agent.config.settings import settings

_TRANSPORT = "chaos_agent.transports.execute_via_transport"

_APPROVED_NODE = "cn-shanghai-cloudspe.10.0.0.119"
_TOOL_POD = "chaosblade-tool-krp7v"

# The incident tool_call, verbatim from the task record.
_INCIDENT_EXEC = (
    f"{_TOOL_POD} -n chaosblade -- blade create mem load "
    "--mode ram --mem-percent 80 --timeout 600"
)


def _exec_command(v_args: str) -> dict:
    return {"command": ["exec", *v_args.split()]}


class TestClassifierRecordsExecPodBinding:
    """The classifier attaches the exec pod as structured node-binding
    data for the selector-less host-level blade shape — and ONLY there."""

    def test_incident_shape_records_binding(self):
        eff = infer_effective_target("kubectl", _exec_command(_INCIDENT_EXEC))
        assert eff.scope == "node"
        assert eff.names == ()
        assert eff.blade_target == "mem"
        assert eff.exec_pod_name == _TOOL_POD
        assert eff.exec_pod_namespace == "chaosblade"

    def test_explicit_node_flag_needs_no_binding(self):
        # An explicit --node already pins the identity: no exec-pod
        # fallback may shadow it.
        eff = infer_effective_target(
            "kubectl",
            _exec_command(
                f"{_TOOL_POD} -n chaosblade -- blade create node-cpu "
                f"fullload --node {_APPROVED_NODE}",
            ),
        )
        assert eff.scope == "node"
        assert eff.names == (_APPROVED_NODE,)
        assert eff.exec_pod_name == ""

    def test_k8s_blade_shape_keeps_existing_semantics(self):
        # ``blade create k8s ...`` targets via the API server, not via
        # the pod's host — the binding must not fire for it.
        eff = infer_effective_target(
            "kubectl",
            _exec_command(
                f"{_TOOL_POD} -n chaosblade -- blade create k8s node-mem "
                f"load --names {_APPROVED_NODE}",
            ),
        )
        assert eff.names == (_APPROVED_NODE,)
        assert eff.exec_pod_name == ""

    def test_readonly_inner_blade_never_reaches_binding(self):
        eff = infer_effective_target(
            "kubectl",
            _exec_command(f"{_TOOL_POD} -n chaosblade -- blade status uid-x"),
        )
        assert eff.exec_pod_name == ""


class TestScreenerNodeBinding:
    @pytest.fixture(autouse=True)
    def _enforcing(self):
        orig = settings.target_guard_enforcing
        settings.target_guard_enforcing = True
        yield
        settings.target_guard_enforcing = orig

    @staticmethod
    def _state(**extra) -> dict:
        state = {
            "messages": [AIMessage(
                content="",
                tool_calls=[{
                    "name": "kubectl",
                    "args": {"subcommand": "exec", "v_args": _INCIDENT_EXEC},
                    "id": "tc-node-binding",
                }],
            )],
            "approved_target": freeze_approved_target(
                target={"namespace": "", "names": [_APPROVED_NODE]},
                params={"scope": "node"},
                blade_scope="node", blade_target="mem", blade_action="load",
            ),
        }
        state.update(extra)
        return state

    @staticmethod
    def _probe_result(node: str):
        return SimpleNamespace(exit_code=0, stdout=node)

    @pytest.mark.asyncio
    async def test_binding_on_approved_node_passes(self):
        # The false positive of task-ccfadf7d: the tool pod runs ON the
        # approved node, so the selector-less host blade lands exactly
        # where the user approved. Pin the resolved node and pass.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=self._probe_result(_APPROVED_NODE),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_awaited_once()
        # The binding is persisted so later rounds never re-probe.
        assert (_TOOL_POD, _APPROVED_NODE) in delta["exec_pod_node_bindings"]

    @pytest.mark.asyncio
    async def test_binding_on_other_node_is_genuine_drift(self):
        # The guard must stay sharp: the same command shape through a tool
        # pod on a DIFFERENT node is real drift (DaemonSet pods exist on
        # every node — the pod name alone never proves the target).
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
                return_value="rejected",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=self._probe_result("some-other-node"),
            ),
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_RETRY
        mock_interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_probe_failure_fails_closed_and_caches(self):
        # An unresolvable binding (network fault severing the API path)
        # keeps the drift review — and the negative is cached so the
        # severed path is never retried per screener round.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
                return_value="rejected",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                side_effect=RuntimeError("api path severed"),
            ),
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_RETRY
        mock_interrupt.assert_called_once()
        assert (_TOOL_POD, "") in delta["exec_pod_node_bindings"]

    @pytest.mark.asyncio
    async def test_cached_binding_skips_probe(self):
        # Self-poisoning guard: a positive from an earlier round must be
        # honoured WITHOUT another in-band read.
        state = self._state(
            exec_pod_node_bindings=((_TOOL_POD, _APPROVED_NODE),),
        )
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(_TRANSPORT, new_callable=AsyncMock) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_negative_skips_probe_and_keeps_review(self):
        state = self._state(exec_pod_node_bindings=((_TOOL_POD, ""),))
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
                return_value="rejected",
            ) as mock_interrupt,
            patch(_TRANSPORT, new_callable=AsyncMock) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_RETRY
        mock_interrupt.assert_called_once()
        mock_transport.assert_not_called()
