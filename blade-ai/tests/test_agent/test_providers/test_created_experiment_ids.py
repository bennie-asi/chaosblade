"""Task B: experiment-id provenance lives in the provider layer.

The tool screener's destroy gate consults a carrier-neutral registry aggregate
(``FaultProviderRegistry.created_experiment_ids``); each backend scans its OWN
create results and claims its OWN durable record. These tests pin the
per-backend evidence semantics and the aggregate union.
"""

from langchain_core.messages import ToolMessage

from chaos_agent.agent.providers import FaultProviderRegistry
from chaos_agent.agent.providers.chaosblade.provider import ChaosbladeProvider
from chaos_agent.agent.providers.chaosblade.python_provider import (
    ChaosbladePythonProvider,
)
from chaos_agent.agent.providers.host_shell.provider import HostShellProvider
from chaos_agent.agent.providers.k8s_native.provider import K8sNativeProvider

OWN_UID = "a1b2c3d4e5f60718"
FAILED_UID = "deadbeef-1234-5678-9abc-def012345678"
PY_UID = "f00dface12345678"


def _create_result(uid: str) -> ToolMessage:
    return ToolMessage(
        content=f'{{"code": 200, "success": true, "result": "{uid}"}}',
        name="blade_create",
        tool_call_id="tc-create",
    )


def _failed_create_result(uid: str) -> ToolMessage:
    return ToolMessage(
        content=f'{{"code": 500, "success": false, "error": "UID: {uid} CRD stuck"}}',
        name="blade_create",
        tool_call_id="tc-create-fail",
    )


def _py_create_result(uid: str) -> ToolMessage:
    return ToolMessage(
        content=f'{{"code": 200, "success": true, "result": "{uid}"}}',
        name="blade_python_create",
        tool_call_id="tc-py-create",
    )


class TestChaosbladeProvenance:
    def test_collects_own_create_results_and_failed_crds(self):
        uids = ChaosbladeProvider().created_experiment_ids(
            [_create_result(OWN_UID), _failed_create_result(FAILED_UID)], {}
        )
        assert uids == {OWN_UID, FAILED_UID}

    def test_claims_durable_record_when_attribution_is_its_own(self):
        uids = ChaosbladeProvider().created_experiment_ids(
            [], {"experiment_uid": f" {OWN_UID} "}
        )
        assert uids == {OWN_UID}

    def test_durable_record_not_claimed_for_python_agent(self):
        # python_agent owns the durable experiment_uid via its own provider.
        uids = ChaosbladeProvider().created_experiment_ids(
            [], {"experiment_uid": OWN_UID, "injection_method": "python_agent"}
        )
        assert uids == set()

    def test_ignores_other_tools_results(self):
        msg = ToolMessage(content="irrelevant", name="kubectl", tool_call_id="t")
        assert ChaosbladeProvider().created_experiment_ids([msg], {}) == set()


class TestPythonAgentProvenance:
    def test_collects_own_tool_results(self):
        uids = ChaosbladePythonProvider().created_experiment_ids(
            [_py_create_result(PY_UID)], {}
        )
        assert uids == {PY_UID}

    def test_ignores_os_carrier_create_results(self):
        # blade_create evidence belongs to the ChaosBlade provider.
        assert ChaosbladePythonProvider().created_experiment_ids(
            [_create_result(OWN_UID)], {}
        ) == set()

    def test_claims_durable_record_only_for_python_agent(self):
        provider = ChaosbladePythonProvider()
        assert provider.created_experiment_ids(
            [], {"experiment_uid": PY_UID, "injection_method": "python_agent"}
        ) == {PY_UID}
        assert provider.created_experiment_ids(
            [], {"experiment_uid": PY_UID}
        ) == set()


class TestUidLessProvenance:
    def test_native_carriers_prove_nothing(self):
        assert K8sNativeProvider().created_experiment_ids(
            [_create_result(OWN_UID)], {"experiment_uid": OWN_UID}
        ) == set()
        assert HostShellProvider().created_experiment_ids(
            [_create_result(OWN_UID)], {"experiment_uid": OWN_UID}
        ) == set()


class TestRegistryProvenanceAggregate:
    def test_unions_across_backends(self):
        uids = FaultProviderRegistry.created_experiment_ids(
            [_create_result(OWN_UID), _py_create_result(PY_UID)],
            {"injection_method": "python_agent", "experiment_uid": PY_UID},
        )
        assert {OWN_UID, PY_UID} <= uids

    def test_screener_facade_matches_registry(self):
        from chaos_agent.agent.nodes.planning.tool_screener import (
            _experiment_uids_created_by_current_task,
        )

        msgs = [_create_result(OWN_UID), _failed_create_result(FAILED_UID)]
        state = {"experiment_uid": OWN_UID}
        assert _experiment_uids_created_by_current_task(msgs, state) == (
            FaultProviderRegistry.created_experiment_ids(msgs, state)
        )

    def test_none_state_is_tolerated(self):
        from chaos_agent.agent.nodes.planning.tool_screener import (
            _experiment_uids_created_by_current_task,
        )

        assert _experiment_uids_created_by_current_task([_create_result(OWN_UID)]) == {
            OWN_UID
        }
