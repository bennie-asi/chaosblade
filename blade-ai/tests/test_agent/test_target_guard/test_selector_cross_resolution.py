"""Regression tests for selector cross-shape resolution (labels vs names).

The drift policy compares selectors statically: a call that selects the
SAME pods through a different selector shape than the approval could
only be rejected as "resource selection drift" (drift_policy documents
the limitation: "labels-vs-names cross is rejected unless
is_namespace_wide"). Two false-positive shapes:

  A. approved by NAMES, executed by LABELS — the label selector picks
     exactly the approved pods;
  B. approved by LABELS with frozen ``resolved_names``, executed by NEW
     pod names after a rollout churned the pod identities.

Fix shape (DATA-side, aligned with the exec-pod node binding):

  - the classifier stays static;
  - the screener resolves the live labels<->names correspondence with
    one bounded, cached in-band read per (namespace, selector) per task
    (``selector_name_probes``) before the drift comparison;
  - both directions stay strictly inside the approval: the resolved set
    must be a subset of the approved name set (A), or the executed names
    must be live members of the approved labels (B). Anything wider,
    unresolvable, or from a failed probe keeps the fail-closed review.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from chaos_agent.agent.nodes.planning.tool_screener import (
    SCREENER_ROUTE_PASS,
    SCREENER_ROUTE_RETRY,
    _selector_probe_key,
    tool_screener,
)
from chaos_agent.agent.target_guard import freeze_approved_target
from chaos_agent.config.settings import settings

_TRANSPORT = "chaos_agent.transports.execute_via_transport"

_NS = "demo"
_POD_A = "accounting-7d9f-x1"
_POD_B = "accounting-7d9f-x2"
_POD_NEW = "accounting-8b2c-y9"
_LABELS = {"app": "demo"}

# Labels-only pod fault: an inline k8s blade executed through an exec.
_LABELS_EXEC = (
    "tool-pod-x -n demo -- blade create k8s pod-network delay "
    "--time 3000 --labels app=demo -n demo"
)
# Names-only pod fault: explicit --names inside the same carrier.
_NAMES_EXEC = (
    "tool-pod-x -n demo -- blade create k8s pod-network delay "
    f"--time 3000 --names {_POD_NEW} -n demo"
)


def _exec_call(v_args: str, call_id: str) -> dict:
    return {
        "messages": [AIMessage(
            content="",
            tool_calls=[{
                "name": "kubectl",
                "args": {"subcommand": "exec", "v_args": v_args},
                "id": call_id,
            }],
        )],
    }


def _approved_names_only() -> dict:
    return freeze_approved_target(
        target={"namespace": _NS, "names": [_POD_A]},
        params={"scope": "pod"},
        blade_scope="pod", blade_target="network", blade_action="delay",
    )


def _approved_labels_only(resolved: tuple[str, ...]) -> dict:
    return freeze_approved_target(
        target={"namespace": _NS, "labels": dict(_LABELS)},
        params={"scope": "pod"},
        blade_scope="pod", blade_target="network", blade_action="delay",
        resolved_names=resolved,
    )


def _probe_result(names: str):
    return SimpleNamespace(exit_code=0, stdout=names)


@pytest.fixture(autouse=True)
def _enforcing():
    orig = settings.target_guard_enforcing
    settings.target_guard_enforcing = True
    yield
    settings.target_guard_enforcing = orig


class TestNamesApprovedLabelsExecuted:
    """Shape A: names-only approval, labels-only execution."""

    @staticmethod
    def _state(**extra) -> dict:
        state = {**_exec_call(_LABELS_EXEC, "tc-cross-a"),
                 "approved_target": _approved_names_only()}
        state.update(extra)
        return state

    @pytest.mark.asyncio
    async def test_labels_selecting_exactly_approved_pods_pass(self):
        # The false positive: the label selector resolves to exactly the
        # approved name set — same pods, different selector shape.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(_POD_A),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_awaited_once()
        # The probe outcome is persisted so later rounds never re-probe.
        key = _selector_probe_key(_NS, _LABELS)
        assert (key, (_POD_A,)) in delta["selector_name_probes"]

    @pytest.mark.asyncio
    async def test_labels_selecting_more_pods_is_genuine_drift(self):
        # The guard must stay sharp: a selector resolving WIDER than the
        # approval (two pods, one approved) is a real blast-radius drift.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
                return_value="rejected",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(f"{_POD_A} {_POD_B}"),
            ),
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_RETRY
        mock_interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_probe_failure_fails_closed_and_caches(self):
        # An unresolvable selector (network fault severing the API path)
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
        key = _selector_probe_key(_NS, _LABELS)
        assert (key, ()) in delta["selector_name_probes"]

    @pytest.mark.asyncio
    async def test_cached_probe_skips_transport(self):
        # Self-poisoning guard: a positive from an earlier round must be
        # honoured WITHOUT another in-band read.
        key = _selector_probe_key(_NS, _LABELS)
        state = self._state(selector_name_probes=((key, (_POD_A,)),))
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


class TestLabelsApprovedNamesExecuted:
    """Shape B: labels approval with churned pod names."""

    @staticmethod
    def _state(**extra) -> dict:
        state = {**_exec_call(_NAMES_EXEC, "tc-cross-b"),
                 "approved_target": _approved_labels_only((_POD_A,)),
                 # Pre-cached vehicle negative: keeps the (unrelated)
                 # vehicle discovery probe out of these tests so only the
                 # selector probe rides the mocked transport.
                 "vehicle_probe_misses": (_POD_NEW,)}
        state.update(extra)
        return state

    @pytest.mark.asyncio
    async def test_churned_name_under_approved_labels_passes(self):
        # The false positive: the rollout renamed the pod; the new name
        # still carries the approved labels, so it IS the approved target.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(_POD_NEW),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_awaited_once()
        # The state's approval snapshot must NOT be rewritten — the live
        # refresh is local to the screening round.
        assert "approved_target" not in delta

    @pytest.mark.asyncio
    async def test_name_outside_live_labels_is_genuine_drift(self):
        # The guard must stay sharp: a name the approved labels no longer
        # (or never did) select is real identity drift.
        state = self._state()
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
                return_value="rejected",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result("some-other-pod"),
            ),
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_RETRY
        mock_interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_frozen_resolution_hit_never_probes(self):
        # When the frozen resolved_names already covers the executed name,
        # the static check passes — no in-band read may fire.
        state = {
            **_exec_call(
                _NAMES_EXEC.replace(_POD_NEW, _POD_A), "tc-cross-b-hit",
            ),
            "approved_target": _approved_labels_only((_POD_A,)),
        }
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
    async def test_never_resolved_labels_approval_passes(self):
        # Review-caught gate bug: a labels approval whose freeze NEVER
        # resolved names (empty resolved_names) has an empty approved name
        # set — the live probe must still fire, otherwise the churn fix
        # silently doesn't apply to the most common labels approval.
        state = {**_exec_call(_NAMES_EXEC, "tc-cross-b-unresolved"),
                 "approved_target": _approved_labels_only(()),
                 "vehicle_probe_misses": (_POD_NEW,)}
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(_POD_NEW),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_checkpoint_list_shape_cache_is_honoured(self):
        # Checkpoint round-trips turn tuples into lists. A cached entry
        # hydrated as [[key, [names...]]] must still count as a hit —
        # otherwise every round would re-probe (self-poisoning).
        key = _selector_probe_key(_NS, _LABELS)
        state = {
            **_exec_call(_NAMES_EXEC, "tc-cross-b-listcache"),
            "approved_target": _approved_labels_only((_POD_A,)),
            "vehicle_probe_misses": (_POD_NEW,),
            "selector_name_probes": [[key, [_POD_NEW]]],
        }
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


class TestTier1NamespaceFallback:
    """Branch A probe namespace falls back to the approval when the
    tier-1 blade form omits --namespace (blade v1.8.0 rejects it)."""

    @pytest.mark.asyncio
    async def test_tier1_labels_probe_uses_approved_namespace(self):
        exec_args = (
            "tool-pod-x -n chaosblade -- blade create k8s pod-network "
            "delay --time 3000 --labels app=demo"
        )
        state = {
            **_exec_call(exec_args, "tc-cross-tier1"),
            "approved_target": _approved_names_only(),
        }
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(_POD_A),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        mock_transport.assert_awaited_once()
        # The probe must have been issued against the APPROVED namespace,
        # not the empty tier-1 one.
        key = _selector_probe_key(_NS, _LABELS)
        assert (key, (_POD_A,)) in delta["selector_name_probes"]


class TestMultiCallRound:
    """The branch-A pin of one tool_call must not leak into the next
    call of the same screening round — each call is judged on its own
    effective target."""

    @pytest.mark.asyncio
    async def test_pin_does_not_bleed_into_second_call(self):
        # Call 1: labels-only (branch A pins the resolved name). Call 2:
        # names-only with an APPROVED name — passes statically, no probe.
        state = {
            "messages": [AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "kubectl",
                        "args": {"subcommand": "exec",
                                 "v_args": _LABELS_EXEC},
                        "id": "tc-multi-1",
                    },
                    {
                        "name": "kubectl",
                        "args": {"subcommand": "exec",
                                 "v_args": _NAMES_EXEC.replace(
                                     _POD_NEW, _POD_A)},
                        "id": "tc-multi-2",
                    },
                ],
            )],
            "approved_target": _approved_names_only(),
            # Call 2's name is an approved target pod, but the vehicle
            # block would still probe it without a cached negative.
            "vehicle_probe_misses": (_POD_A,),
        }
        with (
            patch(
                "chaos_agent.agent.nodes.planning.tool_screener.interrupt",
            ) as mock_interrupt,
            patch(
                _TRANSPORT, new_callable=AsyncMock,
                return_value=_probe_result(_POD_A),
            ) as mock_transport,
        ):
            delta = await tool_screener(state)
        assert delta["screener_route"] == SCREENER_ROUTE_PASS
        mock_interrupt.assert_not_called()
        # Exactly ONE probe: call 1's selector resolution. Call 2 must be
        # decided statically from the approval alone.
        mock_transport.assert_awaited_once()
