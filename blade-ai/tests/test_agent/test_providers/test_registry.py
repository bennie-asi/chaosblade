"""Phase 0 skeleton tests for the FaultProvider registry.

These exercise the registry mechanics only (register / resolve / applicable /
scope-bridge) with lightweight fake providers — no built-in providers are wired
yet, so there is no behaviour to preserve here. Phase 1 adds the concrete
ChaosBlade / K8sNative providers and a conformance suite over them.
"""

from __future__ import annotations

import pytest

from chaos_agent.agent.providers import (
    FaultProvider,
    FaultProviderRegistry,
    ProviderPrompts,
    RecoverResult,
)
from chaos_agent.agent.result.verdict import Layer1Result, Layer1Status


class _FakeProvider:
    """Minimal FaultProvider implementation for registry tests."""

    has_experiment_uid = False
    # Phase-7 T1: required for runtime_checkable protocol conformance (the
    # registry's claim-4 fallback reads it via getattr with a False default).
    uid_less_verdict_default = False
    handle_kind = ""
    is_multi_step = False
    has_deterministic_recover = False
    inject_tool_names: frozenset[str] = frozenset()
    inject_kubectl_subcommands: frozenset[str] = frozenset()
    supported_targets: tuple[str, ...] = ()
    supported_actions: tuple[str, ...] = ()
    injection_binaries: frozenset[str] = frozenset()
    # Phase-7 T2: the three per-tool pass sets — runtime_checkable protocol
    # conformance requires the attributes to exist (the registry's
    # union_tool_names reads them via getattr with an empty default).
    kubeconfig_scoped_tool_names: frozenset[str] = frozenset()
    audit_scoped_tool_names: frozenset[str] = frozenset()
    log_shipping_tool_names: frozenset[str] = frozenset()
    # Phase-8 Form A: same conformance requirement for the Tier-1
    # tool-pod-namespace exemption set.
    tool_pod_namespaces: frozenset[str] = frozenset()

    # Phase-8 Form B: same conformance requirement for the optional
    # vocabulary hooks — the fake stays inert (a non-participating
    # backend; the generic layer getattr-skips a missing hook).
    def scan_step_actions(self, steps, messages):
        return None

    def was_injection_attempted(self, messages):
        return False

    def __init__(
        self,
        carrier: str,
        methods: tuple[str, ...],
        *,
        profiles: tuple[str, ...] = ("k8s",),
    ) -> None:
        self.carrier = carrier
        self.injection_methods = methods
        self._profiles = profiles

    def matches_channel(self, profile: str) -> bool:
        return profile in self._profiles

    def required_params(self, scope: str) -> list[str]:
        return ["scope", "target", "action"]

    def tools(self, phase):
        return []

    def detect(self, messages, *, is_host):
        return self.injection_methods[0] if messages else None

    def injection_recency(self, messages, *, is_host):
        return 0 if messages else -1

    def build_fault_handle(self, values):
        return None

    def build_handle_from_messages(self, messages, retired=None, values=None):
        return None

    def extract_experiment_id(self, messages, retired=None):
        return ""

    def created_experiment_ids(self, messages, state):
        return set()

    def parse_injection_params(self, tool_name, tool_args):
        # Phase-7 T3: issue-time extraction hook — default None pinned
        # structurally (runtime_checkable conformance requires the method).
        return None

    def issue_time_method(self, tool_name, tool_args, *, is_host):
        # Phase-7 T4: issue-time attribution hook — default None pinned
        # structurally (runtime_checkable conformance requires the method).
        return None

    def classify_tool_target(self, tool_name, tool_args, raw_command):
        # Phase-7 T5: guard-side classification hook — default None pinned
        # structurally (runtime_checkable conformance requires the method).
        return None

    def issue_disproven(self, messages):
        return False

    async def rollback_handle(self, handle, **kwargs):
        return ""

    def was_fault_create_attempted(self, messages, injection_method=None):
        # UID-less fake — the protocol default pinned structurally (the
        # conformance isinstance check requires the attribute to exist).
        return False

    async def layer1_verify(self, state, **kwargs) -> Layer1Result:
        return Layer1Result(status=Layer1Status.SKIPPED, details=f"fake:{self.carrier}")

    async def layer1_raw_destroy(self, uid, kubeconfig="") -> str:
        return ""

    async def layer1_destroy(
        self, uid, kubeconfig="", *, messages=None, injection_method=None
    ) -> Layer1Result:
        return Layer1Result(status=Layer1Status.SKIPPED, details=f"fake:{self.carrier}")

    def recovery_vehicle(self, state):
        return ""

    def blocks_deterministic_destroy(self, state, messages=None):
        return False

    def recovery_facts_render(self, state, *, spec_params=None):
        return ""

    def merge_deterministic_recover_verdict(self, layer1, state, part_override=None):
        return layer1

    def layer1_recover_guidance(
        self, state, experiment_uid, *, combo_native=False, combo_part=None
    ):
        return ""

    def layer2_facts_note(self, state):
        return ""

    def verify_prompt_note(self, injection_method, *, injection_pod_name=None) -> str:
        return ""

    def recover_layer2_context(
        self, state, layer1, *, is_deterministic, blade_uid, is_host_scope
    ) -> tuple[str, str]:
        return "", ""

    async def recover(self, state, handle) -> RecoverResult:
        return RecoverResult(level="skipped")

    def prompt_fragments(self) -> ProviderPrompts:
        return ProviderPrompts()


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty registry; teardown restores the built-in
    set so the process-default (established at ``providers`` import) is left in
    place for later test files that rely on a populated registry."""
    FaultProviderRegistry.clear()
    yield
    FaultProviderRegistry.clear()
    FaultProviderRegistry.register_builtins()


def test_fake_provider_satisfies_protocol():
    # runtime_checkable Protocol — structural conformance check.
    assert isinstance(_FakeProvider("chaosblade", ("host_blade",)), FaultProvider)


def test_register_and_all_providers_preserve_order():
    a = _FakeProvider("chaosblade", ("host_blade", "kubectl_exec"))
    b = _FakeProvider("k8s_native", ("kubectl_native",))
    FaultProviderRegistry.register(a)
    FaultProviderRegistry.register(b)
    assert FaultProviderRegistry.all_providers() == (a, b)


def test_resolve_by_method_maps_each_claimed_method():
    a = _FakeProvider("chaosblade", ("host_blade", "kubectl_exec"))
    b = _FakeProvider("k8s_native", ("kubectl_native",))
    FaultProviderRegistry.register(a)
    FaultProviderRegistry.register(b)

    assert FaultProviderRegistry.resolve_by_method("host_blade") is a
    assert FaultProviderRegistry.resolve_by_method("kubectl_exec") is a
    assert FaultProviderRegistry.resolve_by_method("kubectl_native") is b


def test_resolve_by_method_unknown_or_none_returns_none():
    FaultProviderRegistry.register(_FakeProvider("chaosblade", ("host_blade",)))
    assert FaultProviderRegistry.resolve_by_method("does_not_exist") is None
    assert FaultProviderRegistry.resolve_by_method(None) is None
    assert FaultProviderRegistry.resolve_by_method("") is None


def test_register_overwrites_same_carrier_and_reindexes():
    old = _FakeProvider("chaosblade", ("host_blade",))
    new = _FakeProvider("chaosblade", ("kubectl_exec",))
    FaultProviderRegistry.register(old)
    FaultProviderRegistry.register(new)
    # Only one provider under the carrier; the index reflects the new methods.
    assert FaultProviderRegistry.all_providers() == (new,)
    assert FaultProviderRegistry.resolve_by_method("kubectl_exec") is new
    assert FaultProviderRegistry.resolve_by_method("host_blade") is None


def test_duplicate_method_last_registration_wins(caplog):
    a = _FakeProvider("chaosblade", ("shared_method",))
    b = _FakeProvider("k8s_native", ("shared_method",))
    FaultProviderRegistry.register(a)
    FaultProviderRegistry.register(b)
    # b registered last → wins the ambiguous method.
    assert FaultProviderRegistry.resolve_by_method("shared_method") is b


def test_applicable_filters_by_channel_profile():
    cb = _FakeProvider("chaosblade", ("host_blade",), profiles=("k8s", "host"))
    kn = _FakeProvider("k8s_native", ("kubectl_native",), profiles=("k8s",))
    host = _FakeProvider("host_shell", ("host_native",), profiles=("host",))
    for p in (cb, kn, host):
        FaultProviderRegistry.register(p)

    assert FaultProviderRegistry.applicable("k8s") == [cb, kn]
    assert FaultProviderRegistry.applicable("host") == [cb, host]


def test_resolve_by_scope_bridges_via_fault_family():
    # The built-in k8s_chaosblade family declares carrier_types starting with
    # "chaosblade" and owns scope "pod"; register a provider under that carrier
    # and confirm the scope→family→carrier→provider bridge resolves it as a
    # candidate (and as the primary).
    prov = _FakeProvider("chaosblade", ("host_blade",))
    FaultProviderRegistry.register(prov)
    assert prov in FaultProviderRegistry.resolve_by_scope("pod")
    assert FaultProviderRegistry.resolve_primary_by_scope("pod") is prov


def test_resolve_by_scope_unknown_scope_returns_empty():
    FaultProviderRegistry.register(_FakeProvider("chaosblade", ("host_blade",)))
    assert FaultProviderRegistry.resolve_by_scope("no_such_scope") == []
    assert FaultProviderRegistry.resolve_by_scope(None) == []
    assert FaultProviderRegistry.resolve_primary_by_scope("no_such_scope") is None
    assert FaultProviderRegistry.resolve_primary_by_scope(None) is None


def test_clear_empties_registry():
    FaultProviderRegistry.register(_FakeProvider("chaosblade", ("host_blade",)))
    FaultProviderRegistry.clear()
    assert FaultProviderRegistry.all_providers() == ()
    assert FaultProviderRegistry.resolve_by_method("host_blade") is None


# -- fault-handle orchestration (carrier-neutral entry points) ---------------


class TestHandleOrchestration:
    """The registry's three carrier-neutral seams over the built-in backends:
    legacy hydration (``derive_handle_from_legacy``), experiment-id extraction
    (``extract_experiment_uid``) and rollback dispatch (``rollback_handle``)."""

    def test_derive_handle_prefers_method_attributed_provider(self):
        """A ``python_agent`` attribution must claim the UID via the
        Python-agent backend even though ChaosBlade registers first and also
        claims bare ``blade_uid`` facts."""
        FaultProviderRegistry.register_builtins()
        handle = FaultProviderRegistry.derive_handle_from_legacy(
            {
                "experiment_uid": "uid-py",
                "injection_method": "python_agent",
            }
        )
        assert handle == {
            "kind": "experiment_uid",
            "value": "uid-py",
            "method": "python_agent",
        }

    def test_derive_handle_native_method_needs_no_uid(self):
        FaultProviderRegistry.register_builtins()
        handle = FaultProviderRegistry.derive_handle_from_legacy(
            {
                "injection_method": "kubectl_native",
            }
        )
        assert handle == {"kind": "native", "method": "kubectl_native"}

    def test_derive_handle_unattributed_uid_falls_back_to_registration_order(self):
        FaultProviderRegistry.register_builtins()
        handle = FaultProviderRegistry.derive_handle_from_legacy(
            {
                "experiment_uid": "uid-x",
            }
        )
        assert handle == {"kind": "experiment_uid", "value": "uid-x", "method": ""}

    def test_derive_handle_no_facts_returns_none(self):
        FaultProviderRegistry.register_builtins()
        assert FaultProviderRegistry.derive_handle_from_legacy({}) is None

    def test_extract_experiment_uid_scans_blade_evidence(self):
        from langchain_core.messages import ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-77"}',
                name="blade_create",
                tool_call_id="c1",
            ),
        ]
        assert (
            FaultProviderRegistry.extract_experiment_uid(
                msgs,
                is_host=False,
            )
            == "uid-77"
        )
        # UID-less channel facts never produce an id.
        assert FaultProviderRegistry.extract_experiment_uid([], is_host=False) == ""

    @pytest.mark.asyncio
    async def test_rollback_handle_dispatches_by_kind(self, monkeypatch):
        FaultProviderRegistry.register_builtins()
        # Native kinds decline a synchronous rollback (recover graph's job).
        assert (
            await FaultProviderRegistry.rollback_handle(
                {"kind": "native", "method": "kubectl_native"},
            )
            == ""
        )
        # Unknown kinds decline too — never fed to a wrong backend.
        assert await FaultProviderRegistry.rollback_handle({"kind": "bogus"}) == ""
        # The blade_uid kind dispatches to ChaosBlade's blade_destroy.
        from chaos_agent.agent.providers.chaosblade import cli as blade_tools_mod

        class _FakeDestroy:
            async def ainvoke(self, args):
                return f"destroyed {args['uid']}"

        monkeypatch.setattr(blade_tools_mod, "blade_destroy", _FakeDestroy())
        suffix = await FaultProviderRegistry.rollback_handle(
            {"kind": "experiment_uid", "value": "uid-1"},
            kubeconfig="",
        )
        assert suffix == " (auto-rolled back experiment_uid=uid-1)"

    @pytest.mark.asyncio
    async def test_rollback_handle_prefers_method_attribution_over_kind_order(self):
        """Two backends sharing a kind: an attributed handle must reach its
        owning backend even when another kind-owner registered first; a
        legacy (method-less) handle falls back to registration order."""
        calls: list[str] = []

        class _RecordingProvider(_FakeProvider):
            async def rollback_handle(self, handle, **kwargs):
                calls.append(self.carrier)
                return f"rolled back by {self.carrier}"

        first = _RecordingProvider("chaosblade", ("host_blade",))
        first.handle_kind = "experiment_uid"
        second = _RecordingProvider("python_agent_backend", ("python_agent",))
        second.handle_kind = "experiment_uid"
        FaultProviderRegistry.register(first)
        FaultProviderRegistry.register(second)

        suffix = await FaultProviderRegistry.rollback_handle(
            {"kind": "experiment_uid", "value": "uid-9", "method": "python_agent"},
        )
        assert suffix == "rolled back by python_agent_backend"
        assert calls == ["python_agent_backend"]

        calls.clear()
        suffix = await FaultProviderRegistry.rollback_handle(
            {"kind": "experiment_uid", "value": "uid-9"},
        )
        assert suffix == "rolled back by chaosblade"
        assert calls == ["chaosblade"]


class TestRecoverDispatchMatrix:
    """``resolve_fault_dispatch`` ownership order, pinned as the neutral
    equivalent of the legacy routing (``blade_uid`` → experiment carrier,
    else method backend, else the UID-less default)."""

    def test_pure_experiment_routes_to_experiment_carrier(self):
        FaultProviderRegistry.register_builtins()
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"experiment_uid": "uid-1", "injection_method": "host_blade"}
        )
        assert provider.carrier == "chaosblade"
        assert identity == {
            "kind": "experiment_uid",
            "value": "uid-1",
            "method": "host_blade",
        }

    def test_explicit_experiment_handle_in_state_is_reused(self):
        """A state-carried experiment-kind handle IS the claim — no rebuild
        from legacy fields (a rebuilt one would lose the precise method)."""
        FaultProviderRegistry.register_builtins()
        handle = {"kind": "experiment_uid", "value": "uid-1", "method": "host_blade"}
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"experiment_uid": "uid-1", "fault_handle": handle}
        )
        assert provider.carrier == "chaosblade"
        assert identity is handle

    def test_native_method_routes_to_native_backend_with_native_handle(self):
        FaultProviderRegistry.register_builtins()
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"injection_method": "kubectl_native"}
        )
        assert provider.carrier == "k8s_native"
        assert identity == {"kind": "native", "method": "kubectl_native"}

        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"injection_method": "host_native"}
        )
        assert provider.carrier == "host_shell"
        assert identity == {"kind": "native", "method": "host_native"}

    def test_combo_routes_to_experiment_carrier_despite_native_attribution(self):
        """Combo ownership: the experiment claim outranks the native
        attribution — the deterministic destroy must reach the experiment
        carrier (leaking it would orphan a live experiment)."""
        FaultProviderRegistry.register_builtins()
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {
                "experiment_uid": "uid-combo",
                "injection_method": "kubectl_native",
                "combo_native_issued": True,
            }
        )
        assert provider.carrier == "chaosblade"
        assert identity == {
            "kind": "experiment_uid",
            "value": "uid-combo",
            "method": "kubectl_native",
        }

    def test_python_agent_experiment_routes_to_first_registered_carrier(self):
        """Legacy contract: a claimed experiment routes to the FIRST
        registered experiment carrier regardless of method attribution
        (mirrors the pre-dispatch ``if blade_uid:`` routing)."""
        FaultProviderRegistry.register_builtins()
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"experiment_uid": "uid-py", "injection_method": "python_agent"}
        )
        assert provider.carrier == "chaosblade"
        assert identity == {
            "kind": "experiment_uid",
            "value": "uid-py",
            "method": "python_agent",
        }

    def test_no_facts_default_to_uidless_verdict_backend(self):
        FaultProviderRegistry.register_builtins()
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch({})
        assert provider.carrier == "k8s_native"
        assert identity is None

    def test_message_history_uid_routes_to_experiment_carrier(self):
        """Defense seam (claim 2): with the durable facts absent (heavily
        compacted legacy checkpoints) a live experiment UID recovered from
        the message history routes to the experiment carrier's destroy —
        the legacy ``if blade_uid:`` contract, now living inside the
        dispatch so every downstream re-dispatch agrees."""
        from langchain_core.messages import ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-mh"}',
                name="blade_create",
                tool_call_id="c1",
            ),
        ]
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"messages": msgs}
        )
        assert provider.carrier == "chaosblade"
        assert identity == {"kind": "experiment_uid", "value": "uid-mh", "method": ""}

    def test_message_history_uid_outranks_native_attribution(self):
        """A native attribution with a message-history UID is combo evidence
        (claim 2 outranks claims 3/4): the live experiment still needs the
        deterministic destroy."""
        from langchain_core.messages import ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-mh"}',
                name="blade_create",
                tool_call_id="c1",
            ),
        ]
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {"injection_method": "kubectl_native", "messages": msgs}
        )
        assert provider.carrier == "chaosblade"
        # The durable native method is echoed into the recovered handle
        # (combo evidence: experiment claim + native attribution).
        assert identity == {
            "kind": "experiment_uid",
            "value": "uid-mh",
            "method": "kubectl_native",
        }

    def test_state_experiment_claim_outranks_message_history(self):
        """The message scan is the WEAKEST evidence source: a durable
        experiment claim (claim 1) wins before the scan is consulted."""
        from langchain_core.messages import ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-stale"}',
                name="blade_create",
                tool_call_id="c1",
            ),
        ]
        provider, identity = FaultProviderRegistry.resolve_fault_dispatch(
            {
                "experiment_uid": "uid-durable",
                "injection_method": "host_blade",
                "messages": msgs,
            }
        )
        assert provider.carrier == "chaosblade"
        assert identity == {
            "kind": "experiment_uid",
            "value": "uid-durable",
            "method": "host_blade",
        }


class TestResolveByHandleKind:
    def test_method_attribution_outranks_kind_registration_order(self):
        FaultProviderRegistry.register_builtins()
        provider = FaultProviderRegistry.resolve_by_handle_kind(
            {"kind": "experiment_uid", "value": "u", "method": "python_agent"}
        )
        assert provider.carrier == "chaosblade_python"

    def test_kind_fallback_for_methodless_legacy_handles(self):
        FaultProviderRegistry.register_builtins()
        provider = FaultProviderRegistry.resolve_by_handle_kind(
            {"kind": "experiment_uid", "value": "u"}
        )
        assert provider.carrier == "chaosblade"

    def test_mismatched_method_falls_back_to_kind(self):
        """A combo-built experiment handle carries the NATIVE method — the
        kind must still resolve to the experiment carrier, never to the
        native backend whose kind differs."""
        FaultProviderRegistry.register_builtins()
        provider = FaultProviderRegistry.resolve_by_handle_kind(
            {"kind": "experiment_uid", "value": "u", "method": "kubectl_native"}
        )
        assert provider.carrier == "chaosblade"

    def test_empty_or_unknown_returns_none(self):
        FaultProviderRegistry.register_builtins()
        assert FaultProviderRegistry.resolve_by_handle_kind(None) is None
        assert FaultProviderRegistry.resolve_by_handle_kind({}) is None
        assert FaultProviderRegistry.resolve_by_handle_kind({"kind": "bogus"}) is None


class TestIsExperimentHandle:
    """Phase-7 T6: pinning membership is the owning provider's declaration.

    The consumer (``build_recovery_handle``) must not compare kind strings —
    it asks the registry, which asks the handle-owning provider's
    ``has_experiment_uid``. These lock the judgement itself; the pinned
    consumer shape is locked in test_operation_result."""

    def test_builtin_blade_handle_is_an_experiment_handle(self):
        FaultProviderRegistry.register_builtins()
        assert FaultProviderRegistry.is_experiment_handle(
            {"kind": "experiment_uid", "value": "u"}
        )

    def test_declared_experiment_kind_pins_by_registration_alone(self):
        """The scenario the T6 generalisation exists for: a future carrier
        with a NON-blade_uid ``handle_kind`` needs zero consumer changes to
        be recognised as an experiment handle."""
        p = _FakeProvider("future_exp", ("future_method",))
        p.handle_kind = "my_experiment"
        p.has_experiment_uid = True
        FaultProviderRegistry.register(p)
        assert FaultProviderRegistry.is_experiment_handle(
            {"kind": "my_experiment", "value": "exp-1"}
        )

    def test_uidless_native_kind_is_not_an_experiment_handle(self):
        p = _FakeProvider("native_like", ("some_method",))
        p.handle_kind = "native"
        FaultProviderRegistry.register(p)
        assert not FaultProviderRegistry.is_experiment_handle(
            {"kind": "native", "value": "u"}
        )

    def test_empty_or_unknown_handle_is_not_an_experiment_handle(self):
        FaultProviderRegistry.register_builtins()
        assert not FaultProviderRegistry.is_experiment_handle(None)
        assert not FaultProviderRegistry.is_experiment_handle({})
        assert not FaultProviderRegistry.is_experiment_handle({"kind": "bogus"})


class TestDeriveHandleFromMessages:
    def test_blade_uid_in_history_is_claimed(self):
        from langchain_core.messages import ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-88"}',
                name="blade_create",
                tool_call_id="c1",
            ),
        ]
        assert FaultProviderRegistry.derive_handle_from_messages(msgs, {}) == {
            "kind": "experiment_uid",
            "value": "uid-88",
            "method": "",
        }

    def test_destroyed_uid_is_not_claimed(self):
        from langchain_core.messages import AIMessage, ToolMessage

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-88"}',
                name="blade_create",
                tool_call_id="c1",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "blade_destroy", "args": {"uid": "uid-88"}, "id": "c2"}
                ],
            ),
        ]
        assert FaultProviderRegistry.derive_handle_from_messages(msgs, {}) is None

    def test_empty_history_yields_none(self):
        FaultProviderRegistry.register_builtins()
        assert FaultProviderRegistry.derive_handle_from_messages([], {}) is None


def test_extract_kubectl_exec_pod_name_dispatches_to_the_delivery_owner():
    """Phase-8 T4 seam: the kubectl-exec delivery-pod extraction is
    dispatched through the registry (``resolve_by_method("kubectl_exec")``
    -> the ChaosBlade backend's instance method), replacing the execute
    loop's direct lazy import of the concrete module. Byte-equivalent to
    the module-level function the verifier suite pins, and safely ``None``
    when the resolved owner does not implement the hook (the
    resolve-then-getattr defensive shape, same as ``rollback_handle``)."""
    from langchain_core.messages import AIMessage, ToolMessage

    from chaos_agent.agent.providers.chaosblade.provider import (
        extract_kubectl_exec_pod_name as module_fn,
    )

    FaultProviderRegistry.register_builtins()
    msgs = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "kubectl",
                    "args": {
                        "subcommand": "exec",
                        "v_args": (
                            "otel-c-tool-abc123 -n chaosblade -- blade "
                            "create k8s pod-cpu fullload"
                        ),
                    },
                    "id": "tc-1",
                }
            ],
        ),
        ToolMessage(
            content='{"code":200,"success":true,"result":"a0f2357a939a9bb8"}',
            name="kubectl",
            tool_call_id="tc-1",
        ),
    ]
    try:
        provider = FaultProviderRegistry.resolve_by_method("kubectl_exec")
        assert provider is not None
        assert (
            FaultProviderRegistry.extract_kubectl_exec_pod_name(msgs)
            == provider.extract_kubectl_exec_pod_name(msgs)
            == module_fn(msgs)
            == "otel-c-tool-abc123"
        )

        # A registry whose ``kubectl_exec`` owner does not implement the hook
        # (or no owner at all) yields None instead of raising.
        FaultProviderRegistry.clear()
        FaultProviderRegistry.register(
            _FakeProvider("chaosblade", ("kubectl_exec",))
        )
        assert FaultProviderRegistry.extract_kubectl_exec_pod_name(msgs) is None
        FaultProviderRegistry.clear()
        assert FaultProviderRegistry.extract_kubectl_exec_pod_name(msgs) is None
    finally:
        # Phase-8 registry-state hygiene: restore the builtins even when an
        # assertion fails — a leaked empty/fake registry poisons later tests
        # (the same debt the phase-8 full-suite run exposed in test_factory).
        FaultProviderRegistry.clear()
        FaultProviderRegistry.register_builtins()


class TestDestroyedExperimentIdsSeam:
    """phase-13 —— 销毁扫描接缝（spec: detection-import-boundary）。

    ``destroyed_experiment_ids`` 是 union 语义的死亡过滤接缝
    （``created_experiment_ids`` provenance union 的对偶）：任一
    UID-bearing 载体发出的 destroy 都计入，无通道过滤——替代通用层
    （replan seam）对 ``detection.scan_destroyed_uids`` 的直连。
    """

    def test_seam_equals_carrier_scan_on_blade_messages(self):
        """spec「销毁扫描结果集合相等」：接缝（逐 provider union）对
        blade_destroy 消息集返回与载体权威函数相同的集合（双 blade 系
        provider 都扫同一 ``blade_destroy`` 词汇，union 后仍相等）。"""
        from langchain_core.messages import AIMessage, ToolMessage

        from chaos_agent.agent.providers.chaosblade.verify import (
            scan_destroyed_uids,
        )

        FaultProviderRegistry.register_builtins()
        msgs = [
            ToolMessage(
                content='{"code":200,"success":true,"result":"uid-1"}',
                name="blade_create",
                tool_call_id="c1",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "blade_destroy",
                        "args": {"uid": "uid-1"},
                        "id": "d1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "blade_destroy",
                        "args": {"uid": "uid-2"},
                        "id": "d2",
                    }
                ],
            ),
        ]
        assert (
            FaultProviderRegistry.destroyed_experiment_ids(msgs)
            == scan_destroyed_uids(msgs)
            == {"uid-1", "uid-2"}
        )

    def test_second_carrier_destroy_unioned(self):
        """spec「第二 UID-bearing 载体的销毁被仲裁纳入」：注册带销毁扫描
        hook 的 fake provider，其扫到的 UID 并入 union（逐 provider 聚合
        可扩展，无通道过滤——fake 的销毁不依赖任何通道上下文）。"""

        class _DestroyScanningFake(_FakeProvider):
            has_experiment_uid = True

            def destroyed_experiment_ids(self, messages):
                return {"fake-destroyed-uid"}

        FaultProviderRegistry.register_builtins()
        FaultProviderRegistry.register(
            _DestroyScanningFake("fake_exp", ("fake_experiment",))
        )
        assert FaultProviderRegistry.destroyed_experiment_ids([]) == {
            "fake-destroyed-uid"
        }

    def test_uid_less_and_hook_less_providers_contribute_nothing(self):
        """UID-less 载体（无销毁概念）与未实现 hook 的 provider 贡献空集
        ——getattr-skip 模式，第三方 backend 不实现 hook 也能安全共存。"""

        class _HooklessExperimentFake(_FakeProvider):
            # has_experiment_uid=True 但不实现 destroyed_experiment_ids
            # ——getattr-skip 路径。
            has_experiment_uid = True

        FaultProviderRegistry.register(
            _HooklessExperimentFake("hookless_exp", ("hookless_exp_method",))
        )
        assert FaultProviderRegistry.destroyed_experiment_ids([]) == set()


class TestRecoverExperimentUidFromSessionSeam:
    """phase-13 —— session 恢复接缝（spec: detection-import-boundary, D2）。

    任务文件持久化的消息是纯 dict（无通道上下文）；接缝编排三层：
    langchain 转换 → 逐 UID-bearing provider 提取（无通道过滤）→
    载体自有 dict fallback（``extract_experiment_id_from_session_dict``）。
    替代通用层（task_snapshot）对 verify/detection 的直连。
    """

    # T1 快照的同款钉扎值（真实 UUID 形态——提取器的 regex 契约）。
    _OS_UID = "aaaaaaaa-1111-4222-8333-444444444444"
    _PY_UID = "bbbbbbbb-1111-4222-8333-444444444444"

    def test_seam_equals_snapshot_pinned_values(self):
        """spec「session 恢复逐值相等」：混合 fixture 两方向（「最靠后者
        胜出」语义）+ dict-only fallback + 空输入——与 phase-13 T1 快照
        （tasks 1.2）钉扎值逐值相等，T4 改道的对照组。"""
        os_create = '{{"code":200,"success":true,"result":"{}"}}'.format(
            self._OS_UID
        )
        py_create = '{{"code":200,"success":true,"result":"{}"}}'.format(
            self._PY_UID
        )

        FaultProviderRegistry.register_builtins()

        # 混合两方向：逐 provider 首 claim 必须复现「最靠后者胜出」。
        py_last = [
            {
                "type": "tool",
                "name": "blade_create",
                "content": os_create,
                "tool_call_id": "c1",
            },
            {
                "type": "tool",
                "name": "blade_python_create",
                "content": py_create,
                "tool_call_id": "c2",
            },
        ]
        os_last = list(reversed(py_last))
        assert (
            FaultProviderRegistry.recover_experiment_uid_from_session(py_last)
            == self._PY_UID
        )
        assert (
            FaultProviderRegistry.recover_experiment_uid_from_session(os_last)
            == self._OS_UID
        )

        # dict-only：langchain 转换后无 name → 全家桶不认 → 接缝第三层
        # （载体自有 dict hook）命中。
        dict_only = [
            {
                "type": "tool_execution",
                "detail": {
                    "command": "blade create k8s pod-cpu fullload",
                    "stdout_preview": os_create,
                },
            }
        ]
        assert (
            FaultProviderRegistry.recover_experiment_uid_from_session(dict_only)
            == self._OS_UID
        )

        # 空输入与非 list 输入。
        assert FaultProviderRegistry.recover_experiment_uid_from_session([]) == ""
        assert FaultProviderRegistry.recover_experiment_uid_from_session(None) == ""
        assert (
            FaultProviderRegistry.recover_experiment_uid_from_session("not-a-list")
            == ""
        )

    def test_channel_missing_does_not_miss_host_channel_evidence(self):
        """spec「channel 缺失时不漏 python 家族提取」（tasks 4.5）：接缝
        签名无通道参数——host-only UID-bearing provider 的证据仍被咨询
        并提取；对照带通道过滤的 ``extract_experiment_uid(is_host=False)``
        同证据被滤除——证明无通道过滤是接缝的结构性行为，而非靠双通道
        provider 的巧合覆盖。"""

        class _HostOnlyExtractingFake(_FakeProvider):
            has_experiment_uid = True

            def __init__(self):
                super().__init__(
                    "host_only_exp", ("host_only_method",), profiles=("host",)
                )

            def extract_experiment_id(self, messages, retired=None):
                return "host-only-uid" if messages else ""

        FaultProviderRegistry.register_builtins()
        FaultProviderRegistry.register(_HostOnlyExtractingFake())
        session = [{"type": "human", "content": "evidence"}]

        # 无通道上下文（任务文件不记录通道）：host-only provider 被咨询
        # 并 claim——不漏检。
        assert (
            FaultProviderRegistry.recover_experiment_uid_from_session(session)
            == "host-only-uid"
        )

        # 反事实对照：同证据在 K8S 通道过滤下被滤除（有通道接缝返回空）。
        from langchain_core.messages import HumanMessage

        assert (
            FaultProviderRegistry.extract_experiment_uid(
                [HumanMessage(content="evidence")], is_host=False
            )
            == ""
        )

    def test_uid_less_and_hook_less_providers_contribute_nothing(self):
        """UID-less 载体与未实现任何 session 提取路径的 provider 贡献
        空——两层（extract_experiment_id / dict hook）均为 getattr-skip
        可选路径，第三方 backend 不实现 hook 也能安全共存。"""

        class _HooklessExperimentFake(_FakeProvider):
            # has_experiment_uid=True：两层 hook 都会被咨询，但
            # extract_experiment_id 继承默认返回 ""，dict hook 不存在
            # （getattr-skip 路径）。
            has_experiment_uid = True

        FaultProviderRegistry.register(
            _HooklessExperimentFake("hookless_exp", ("hookless_exp_method",))
        )
        session = [
            {
                "type": "tool",
                "name": "blade_create",
                "content": '{"code":200,"success":true,"result":"x"}',
                "tool_call_id": "c1",
            }
        ]
        assert FaultProviderRegistry.recover_experiment_uid_from_session(session) == ""
