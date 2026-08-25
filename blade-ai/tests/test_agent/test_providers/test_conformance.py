"""Phase 3 conformance suite for every registered FaultProvider.

Where ``test_builtin_providers.py`` locks in the behaviour-equivalent migration
of individual chokepoints, this suite parametrises over ALL built-in providers
and asserts the *structural contract* every backend must honour — so adding a
new execution backend either satisfies the contract or fails here loudly.

Three contract pillars (mirrors plan §六 阶段 3):

1. Interface completeness — the runtime-checkable Protocol plus the stable-id
   invariants (non-empty ``carrier`` / ``injection_methods``; unique carriers).
2. ``injection_method`` unique mapping — every claimed method resolves back to
   exactly its own provider via ``resolve_by_method`` (the LIVE production path,
   used by ``_verifier_layer1``), and no method is claimed by two providers.
3. carrier <-> FaultFamily meshing — every family declares ``carrier_types``
   (an ordered candidate list) whose entries are real provider carriers, and
   ``resolve_by_scope`` returns those candidate providers (in precedence order)
   for every scope the family owns. ``resolve_primary_by_scope`` returns the
   first. See ``docs/design/fault-provider-contract.md`` for the candidate
   semantics (a single scope may be served by several backends, so the bridge
   is intentionally multi-valued).
"""

from __future__ import annotations

import pytest

from chaos_agent.agent.providers import (
    EXECUTE,
    PLAN,
    RECOVER_VERIFY,
    VERIFY,
    FaultProvider,
    FaultProviderRegistry,
    ProviderPrompts,
)
from chaos_agent.agent.providers.chaosblade.provider import ChaosbladeProvider
from chaos_agent.agent.providers.chaosblade.python_provider import ChaosbladePythonProvider
from chaos_agent.agent.providers.host_shell.provider import HostShellProvider
from chaos_agent.agent.providers.k8s_native.provider import K8sNativeProvider
from chaos_agent.agent.spec.fault_registry import (
    aggregate_cluster_scoped,
    all_families,
    family_for_scope,
)

# The built-in backends, in registration/precedence order. A new provider added
# to ``register_builtins`` should be appended here so the whole suite covers it.
BUILTIN_PROVIDERS = (
    ChaosbladeProvider, K8sNativeProvider, HostShellProvider,
    ChaosbladePythonProvider,
)
_ALL_PHASES = (PLAN, EXECUTE, VERIFY, RECOVER_VERIFY)
_KNOWN_PROFILES = ("k8s", "host")


def _provider_id(cls) -> str:
    return cls().carrier


@pytest.fixture(autouse=True)
def _isolate_registry():
    FaultProviderRegistry.clear()
    yield
    FaultProviderRegistry.clear()
    FaultProviderRegistry.register_builtins()


# ---------------------------------------------------------------------------
# Pillar 1 — interface completeness (per-provider)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_satisfies_protocol(provider_cls):
    assert isinstance(provider_cls(), FaultProvider)


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_carrier_is_stable_nonempty_id(provider_cls):
    carrier = provider_cls().carrier
    assert isinstance(carrier, str) and carrier.strip() == carrier and carrier


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_injection_methods_nonempty_tuple_of_strings(provider_cls):
    methods = provider_cls().injection_methods
    assert isinstance(methods, tuple) and methods
    assert all(isinstance(m, str) and m for m in methods)


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_matches_channel_is_a_subset_of_known_profiles(provider_cls):
    prov = provider_cls()
    # At least one known profile is served, and nothing outside the known set.
    served = [p for p in _KNOWN_PROFILES if prov.matches_channel(p)]
    assert served
    assert prov.matches_channel("bogus") is False


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_capability_attrs_are_bools(provider_cls):
    prov = provider_cls()
    assert isinstance(prov.has_experiment_uid, bool)
    assert isinstance(prov.is_multi_step, bool)


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_tools_returns_a_list_for_every_phase(provider_cls):
    prov = provider_cls()
    for phase in _ALL_PHASES:
        tools = prov.tools(phase)
        assert isinstance(tools, list)
    # An unknown phase contributes nothing (never raises).
    assert prov.tools("no_such_phase") == []


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_prompt_fragments_returns_provider_prompts(provider_cls):
    assert isinstance(provider_cls().prompt_fragments(), ProviderPrompts)


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_required_params_always_carries_the_intent_triple(provider_cls):
    prov = provider_cls()
    # Whatever the scope, the (scope, target, action) triple is mandatory.
    for scope in ("pod", "node", "host"):
        assert {"scope", "target", "action"}.issubset(prov.required_params(scope))


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_required_params_gates_namespace_by_cluster_scope(provider_cls):
    prov = provider_cls()
    cluster_scoped = aggregate_cluster_scoped()
    # A namespaced scope requires namespace; a cluster-scoped one never does.
    assert "namespace" in prov.required_params("pod")
    for scope in ("node", "host"):
        if scope in cluster_scoped:
            assert "namespace" not in prov.required_params(scope)


# ---------------------------------------------------------------------------
# Pillar 2 — injection_method unique mapping (registry-wide)
# ---------------------------------------------------------------------------


def test_carriers_are_globally_unique():
    carriers = [cls().carrier for cls in BUILTIN_PROVIDERS]
    assert len(carriers) == len(set(carriers))


def test_every_injection_method_is_claimed_by_exactly_one_provider():
    seen: dict[str, str] = {}
    for cls in BUILTIN_PROVIDERS:
        prov = cls()
        for method in prov.injection_methods:
            assert method not in seen, (
                f"injection_method {method!r} claimed by both "
                f"{seen.get(method)!r} and {prov.carrier!r}"
            )
            seen[method] = prov.carrier


def test_resolve_by_method_round_trips_every_claimed_method():
    FaultProviderRegistry.register_builtins()
    for cls in BUILTIN_PROVIDERS:
        prov = cls()
        for method in prov.injection_methods:
            resolved = FaultProviderRegistry.resolve_by_method(method)
            assert resolved is not None
            assert resolved.carrier == prov.carrier


def test_method_index_covers_exactly_the_union_of_claimed_methods():
    FaultProviderRegistry.register_builtins()
    claimed = {
        m for cls in BUILTIN_PROVIDERS for m in cls().injection_methods
    }
    for method in claimed:
        assert FaultProviderRegistry.resolve_by_method(method) is not None
    # Unknown methods never resolve.
    assert FaultProviderRegistry.resolve_by_method("definitely_not_a_method") is None


# ---------------------------------------------------------------------------
# Pillar 3 — carrier <-> FaultFamily meshing (candidate-based)
# ---------------------------------------------------------------------------


def test_every_family_declares_aligned_carrier_types():
    """Each family's ``carrier_types`` is a non-empty tuple of real provider
    carriers (name alignment invariant that makes the scope bridge resolvable)."""
    families = all_families()
    assert families  # at least the built-in k8s + host families
    builtin_carriers = {cls().carrier for cls in BUILTIN_PROVIDERS}
    for family in families:
        assert isinstance(family.carrier_types, tuple)
        assert family.carrier_types
        for carrier in family.carrier_types:
            assert isinstance(carrier, str) and carrier.strip() == carrier and carrier
            assert carrier in builtin_carriers


def test_resolve_by_scope_returns_registered_candidates_in_precedence_order():
    """The bridge: ``resolve_by_scope`` returns the registered providers for a
    family's ``carrier_types`` in precedence order for every scope it owns, and
    ``resolve_primary_by_scope`` returns the first candidate."""
    FaultProviderRegistry.register_builtins()
    for family in all_families():
        expected = [
            FaultProviderRegistry.get(c)
            for c in family.carrier_types
            if FaultProviderRegistry.get(c) is not None
        ]
        assert expected  # name alignment guarantees at least one built-in
        for scope in family.scopes:
            assert FaultProviderRegistry.resolve_by_scope(scope) == expected
            assert FaultProviderRegistry.resolve_primary_by_scope(scope) is expected[0]


def test_resolve_by_scope_skips_unregistered_carriers():
    """A carrier listed by a family but absent from the registry is skipped,
    not surfaced as ``None`` — proving the candidate filter is registration-aware."""
    # Register only the host_shell backend; the k8s family's carriers
    # (chaosblade / k8s_native) are absent, the host family's chaosblade is
    # absent but host_shell is present.
    FaultProviderRegistry.register(HostShellProvider())
    # host family carrier_types = ("chaosblade", "host_shell") → only host_shell.
    host_candidates = FaultProviderRegistry.resolve_by_scope("host")
    assert [p.carrier for p in host_candidates] == ["host_shell"]
    # k8s family carriers all absent → empty.
    assert FaultProviderRegistry.resolve_by_scope("pod") == []


def test_family_for_scope_owns_every_aggregated_scope():
    """Vocabulary integrity: every scope surfaced to intent has an owning family
    (so ``resolve_by_scope`` at least reaches a family before the carrier hop)."""
    from chaos_agent.agent.spec.fault_registry import aggregate_scopes

    for scope in aggregate_scopes():
        assert family_for_scope(scope) is not None


# ---------------------------------------------------------------------------
# Pillar 4 — recover handle closed loop (phase-3: build → materialize →
# resolve → dispatch, per carrier)
# ---------------------------------------------------------------------------

# Pre-handle attribution facts per carrier (the checkpoint shape each
# backend must still hydrate), with the delivery variants for carriers that
# inject through more than one channel (chaosblade: in-cluster exec vs host
# binary — one per channel profile).
_LEGACY_FACTS = {
    "chaosblade": (
        {"experiment_uid": "uid-cb-k8s", "injection_method": "kubectl_exec"},
        {"experiment_uid": "uid-cb-host", "injection_method": "host_blade"},
    ),
    "k8s_native": ({"injection_method": "kubectl_native"},),
    "host_shell": ({"injection_method": "host_native"},),
    "chaosblade_python": (
        {"experiment_uid": "uid-py", "injection_method": "python_agent"},
    ),
}


def _legacy_facts(provider_cls):
    return _LEGACY_FACTS[provider_cls().carrier]


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_build_fault_handle_matches_declared_kind(provider_cls):
    prov = provider_cls()
    for facts in _legacy_facts(provider_cls):
        handle = prov.build_fault_handle(facts)
        assert handle and handle["kind"] == prov.handle_kind


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_handle_roundtrip_through_materialize_resolve_and_dispatch(provider_cls):
    """The phase-3 closed loop: legacy facts → ``build_fault_handle`` →
    (durable | hydrated) state → ``materialize_fault_handle`` →
    ``resolve_by_handle_kind`` lands back on the owning backend; the recover
    dispatch's identity handle carries exactly what the dispatched backend
    consumes (the experiment UID for UID carriers, the attribution handle
    for UID-less ones)."""
    from chaos_agent.agent.state import materialize_fault_handle

    FaultProviderRegistry.register_builtins()
    prov = provider_cls()
    for facts in _legacy_facts(provider_cls):
        # Work on a copy — the shared module-level fixture tuples must stay
        # pristine for the other parametrised runs (phase-14 G4 retired the
        # in-place hydrate normalisation; build_fault_handle is read-only).
        facts = dict(facts)
        expected_uid = facts.get("experiment_uid") or ""
        built = prov.build_fault_handle(facts)
        # Durable handle wins verbatim; legacy-only state hydrates back to
        # the same ownership.
        assert materialize_fault_handle({"fault_handle": built}) == built
        assert materialize_fault_handle(facts) == built
        # Kind-based resolution lands on the owning backend.
        resolved = FaultProviderRegistry.resolve_by_handle_kind(built)
        assert resolved is not None and resolved.carrier == prov.carrier
        # Recover dispatch consumes the same identity.
        dispatched, identity = FaultProviderRegistry.resolve_fault_dispatch(facts)
        if prov.has_experiment_uid:
            # Registration-order experiment claim: the UID routes to an
            # experiment carrier whose destroy domain owns it (the legacy
            # OS-carrier contract) — the identity IS the experiment handle
            # (kind renamed to "experiment_uid" in phase-14 G7).
            assert identity and identity.get("kind") == "experiment_uid"
            assert identity.get("value") == expected_uid
            assert dispatched.has_experiment_uid
        else:
            assert identity == built
            assert dispatched.carrier == prov.carrier


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_recover_hook_family_contract(provider_cls):
    """Structural contract of the recover hook family: the deterministic
    capability flag agrees with the handle kind (only UID carriers own a
    programmatic destroy), and every hook returns its declared shape on an
    empty state (never raising, never naming a carrier)."""
    prov = provider_cls()
    assert isinstance(prov.has_deterministic_recover, bool)
    assert prov.has_deterministic_recover == (prov.handle_kind == "experiment_uid")
    assert isinstance(prov.blocks_deterministic_destroy({}), bool)
    assert prov.blocks_deterministic_destroy({}) is False
    # Bare retry destroy: declared for every backend, empty for carriers with
    # no programmatic destroy (checked async by the callers only).
    import inspect as _inspect

    assert callable(prov.layer1_raw_destroy)
    assert _inspect.iscoroutinefunction(prov.layer1_raw_destroy)
    assert isinstance(prov.recovery_vehicle({}), str)
    assert prov.recovery_vehicle({}) == ""
    assert isinstance(prov.recovery_facts_render({}, spec_params={}), str)
    assert prov.recovery_facts_render({}, spec_params={}) == ""
    layer1_sentinel = object()
    assert (
        prov.merge_deterministic_recover_verdict(layer1_sentinel, {})
        is layer1_sentinel
    )
    assert isinstance(prov.layer1_recover_guidance({}, ""), str)
    assert prov.layer1_recover_guidance({}, "") == ""
    assert isinstance(prov.layer2_facts_note({}), str)
    assert prov.layer2_facts_note({}) == ""


def test_blocks_deterministic_destroy_marks_incluster_delivery_only():
    """The in-cluster exec delivery is the ONLY state that blocks the
    deterministic destroy; the host-binary delivery always runs it."""
    cb = ChaosbladeProvider()
    assert cb.blocks_deterministic_destroy({"injection_method": "kubectl_exec"}) is True
    assert cb.blocks_deterministic_destroy({"injection_method": "host_blade"}) is False


# ---------------------------------------------------------------------------
# Pillar 5 — verify hook family (phase-4: layer1_verify + the attempted
# judgement, per carrier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_cls",
    BUILTIN_PROVIDERS,
    ids=_provider_id,
)
def test_verify_hook_family_contract(provider_cls):
    """Structural contract of the verify hook family (phase-4 spec,
    verify-chain-provider-protocol R5): the deterministic Layer-1
    verification hook is a coroutine function every backend owns, and the
    attempted-but-no-UID judgement hook exists as a SYNC callable (it runs
    inline inside the verify/recover guards)."""
    import inspect as _inspect

    prov = provider_cls()
    assert isinstance(prov, FaultProvider)
    assert callable(prov.layer1_verify)
    assert _inspect.iscoroutinefunction(prov.layer1_verify)
    assert callable(prov.was_fault_create_attempted)
    assert not _inspect.iscoroutinefunction(prov.was_fault_create_attempted)


def test_fake_provider_satisfies_verify_hook_family():
    """The suite's test double satisfies the same verify hook contract (the
    runtime_checkable Protocol already asserts the surface; this pins the
    coroutine form the verify chain's await relies on)."""
    import inspect as _inspect

    from .test_registry import _FakeProvider

    fake = _FakeProvider("chaosblade", ("host_blade",))
    assert isinstance(fake, FaultProvider)
    assert callable(fake.layer1_verify)
    assert _inspect.iscoroutinefunction(fake.layer1_verify)
    assert callable(fake.was_fault_create_attempted)
    assert not _inspect.iscoroutinefunction(fake.was_fault_create_attempted)


@pytest.mark.parametrize("provider_cls", BUILTIN_PROVIDERS, ids=_provider_id)
def test_uidless_carriers_pin_attempted_false_semantics(provider_cls):
    """Semantic pin (spec R3): UID-less carriers (k8s_native / host_shell)
    MUST keep ``was_fault_create_attempted`` at False — the attempted-but-
    no-UID judgement belongs to the experiment-recording carrier; a True
    from a UID-less carrier would wrongly fire verify's warning branch and
    recover's failed terminal branch. The explicit mirror return (not the
    protocol default) IS the pin — even attempted-looking blade evidence in
    the history must not flip it."""
    from langchain_core.messages import ToolMessage

    prov = provider_cls()
    if prov.has_experiment_uid:
        pytest.skip("pin applies to UID-less carriers only")
    attempted_looking = [
        ToolMessage(
            content='{"code": 500, "success": false, "error": "boom"}',
            name="blade_create",
            tool_call_id="tc-att",
        ),
    ]
    assert prov.was_fault_create_attempted([], None) is False
    assert prov.was_fault_create_attempted(attempted_looking, None) is False
    assert prov.was_fault_create_attempted(attempted_looking, "kubectl_native") is False
