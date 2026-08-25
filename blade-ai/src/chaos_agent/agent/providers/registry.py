"""FaultProvider registry.

New fault execution backends are added by:
1. Implementing the :class:`~chaos_agent.agent.providers.base.FaultProvider`
   protocol.
2. Calling ``FaultProviderRegistry.register(MyProvider())``.
3. Declaring the matching :class:`FaultFamily` (so its ``carrier_types`` list
   includes the provider's ``carrier``) in ``fault_registry.py``.

No existing chokepoint (factory tool union, injection detection, Layer 1,
recovery, intent completeness) needs to change — each resolves the active
provider through this registry instead of a hardcoded ``if injection_method``.

Two resolution modes
---------------------
- ``resolve_by_method`` — POST-injection. The concrete ``injection_method`` is
  known (detected from message history), so the exact backend is resolvable for
  Layer-1 verification and recovery.
- ``resolve_by_scope`` — PRE-injection. Only the intent scope is known; the
  registry bridges scope → ``FaultFamily.carrier_types`` → the candidate
  providers (ordered by precedence) so intent completeness / prompt fragments
  can consult the likely backends. ``resolve_primary_by_scope`` returns the
  single most-likely one.
- ``applicable`` — PRE-injection, channel-based. Which backends can operate
  against a "k8s"/"host" profile (for prompt fragments / required-params union).
- ``all_providers`` — build-time. The factory unions every provider's tools per
  phase (tools are bound once at graph compile, not per request).

Mirrors ``TransportRegistry`` (class-level registry, ``register`` overwrites).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from chaos_agent.agent.providers.base import FaultProvider

if TYPE_CHECKING:
    from chaos_agent.agent.target_guard.types import EffectiveTarget

logger = logging.getLogger(__name__)


def _session_messages_to_langchain(messages: list[dict]) -> list:
    """Best-effort conversion of SessionStore message dicts back to messages.

    Moved from ``agent/result/task_snapshot.py`` in phase-13 (D2): the
    conversion is part of the session-recovery ORCHESTRATION this registry
    seam owns, not task-snapshot-private logic. Consumed by
    :meth:`FaultProviderRegistry.recover_experiment_uid_from_session` and
    (re-export-free) by task_snapshot's inject-context builder.
    """
    if not messages:
        return []

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    tool_call_names: dict[str, str] = {}
    out: list = []

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type") or ""
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        msg_id = msg.get("id") if isinstance(msg.get("id"), str) else None

        if msg_type == "ai":
            tool_calls = msg.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tc_id = tc.get("id")
                    tc_name = tc.get("name")
                    if isinstance(tc_id, str) and isinstance(tc_name, str):
                        tool_call_names[tc_id] = tc_name
            kwargs = {"content": content}
            if msg_id:
                kwargs["id"] = msg_id
            if isinstance(tool_calls, list) and tool_calls:
                kwargs["tool_calls"] = tool_calls
            try:
                out.append(AIMessage(**kwargs))
            except Exception:
                out.append(AIMessage(content=content))
            continue

        if msg_type == "tool":
            tool_call_id = msg.get("tool_call_id") or f"session_tool_{idx}"
            name = msg.get("name") or tool_call_names.get(tool_call_id, "")
            kwargs = {"content": content, "tool_call_id": tool_call_id}
            if isinstance(name, str) and name:
                kwargs["name"] = name
            if msg_id:
                kwargs["id"] = msg_id
            try:
                out.append(ToolMessage(**kwargs))
            except Exception:
                continue
            continue

        if msg_type == "tool_execution":
            detail = msg.get("detail") if isinstance(msg.get("detail"), dict) else {}
            source = detail.get("source") if isinstance(detail.get("source"), str) else ""
            stdout = detail.get("stdout_preview") if isinstance(detail.get("stdout_preview"), str) else ""
            text = stdout or content
            kwargs = {
                "content": text,
                "tool_call_id": msg.get("tool_call_id") or f"session_exec_{idx}",
            }
            if source:
                kwargs["name"] = source
            try:
                out.append(ToolMessage(**kwargs))
            except Exception:
                continue
            continue

        try:
            if msg_type == "human":
                out.append(HumanMessage(content=content, id=msg_id))
            elif msg_type == "system":
                out.append(SystemMessage(content=content, id=msg_id))
        except Exception:
            continue

    return out


class FaultProviderRegistry:
    """Class-level registry of fault execution backends, keyed by ``carrier``."""

    _providers: dict[str, FaultProvider] = {}
    # injection_method → provider, rebuilt on every register for O(1) resolve.
    _method_index: dict[str, FaultProvider] = {}

    @classmethod
    def register(cls, provider: FaultProvider) -> None:
        """Register (or replace) a provider by ``carrier``.

        Rebuilds the injection-method index. A method claimed by two providers
        is a programming error (the runtime dispatch would be ambiguous) — we
        log a warning and let the last registration win, matching
        ``TransportRegistry``'s overwrite-on-duplicate contract.
        """
        cls._providers[provider.carrier] = provider
        cls._reindex()

    @classmethod
    def _reindex(cls) -> None:
        index: dict[str, FaultProvider] = {}
        for provider in cls._providers.values():
            for method in provider.injection_methods:
                if method in index and index[method] is not provider:
                    logger.warning(
                        "injection_method %r claimed by both %r and %r; "
                        "last registration wins",
                        method,
                        index[method].carrier,
                        provider.carrier,
                    )
                index[method] = provider
        cls._method_index = index

    @classmethod
    def all_providers(cls) -> tuple[FaultProvider, ...]:
        """All registered providers (registration order)."""
        return tuple(cls._providers.values())

    @classmethod
    def get(cls, carrier: str) -> Optional[FaultProvider]:
        """Retrieve a provider by its ``carrier`` id, or ``None``."""
        return cls._providers.get(carrier)

    @classmethod
    def resolve_by_method(cls, injection_method: str | None) -> Optional[FaultProvider]:
        """POST-injection: resolve the backend for a detected ``injection_method``.

        Returns ``None`` for unknown / ``None`` methods so callers keep their
        existing fallback behaviour during the incremental migration.
        """
        if not injection_method:
            return None
        return cls._method_index.get(injection_method)

    @classmethod
    def resolve_by_scope(cls, scope: str | None) -> list[FaultProvider]:
        """PRE-injection: bridge an intent ``scope`` to its *candidate* backends
        via the ``FaultFamily`` registry (``carrier_types`` ↔ provider
        ``carrier``).

        A scope maps to CANDIDATES, not a single backend: pre-injection the LLM
        has not yet chosen a backend, and one scope may be served by several
        (e.g. a k8s scope by ``chaosblade`` OR ``k8s_native``). Returns the
        registered providers for ``family.carrier_types`` in precedence order,
        skipping carriers with no registered provider. Empty list when no family
        owns the scope or none of its carriers are registered. Use
        :meth:`resolve_primary_by_scope` for the single most-likely backend.
        """
        if not scope:
            return []
        # Lazy import: keep the registry importable without pulling the spec
        # package at module load (matches the codebase's lazy-import style).
        from chaos_agent.agent.spec.fault_registry import family_for_scope

        family = family_for_scope(scope)
        if family is None:
            return []
        resolved: list[FaultProvider] = []
        for carrier in family.carrier_types:
            provider = cls._providers.get(carrier)
            if provider is not None:
                resolved.append(provider)
        return resolved

    @classmethod
    def resolve_primary_by_scope(cls, scope: str | None) -> Optional[FaultProvider]:
        """PRE-injection: the single most-likely backend for ``scope`` (the first
        registered candidate from :meth:`resolve_by_scope`), or ``None``."""
        candidates = cls.resolve_by_scope(scope)
        return candidates[0] if candidates else None

    @classmethod
    def applicable(cls, profile: str) -> list[FaultProvider]:
        """PRE-injection: providers that can operate against a channel ``profile``
        ("k8s"|"host"), in registration order."""
        return [p for p in cls._providers.values() if p.matches_channel(profile)]

    @classmethod
    def union_required_params(
        cls, scope: str | None, *, profile: str | None = None
    ) -> list[str]:
        """PRE-injection: order-preserving union of every registered provider's
        ``required_params(scope)``.

        Intent completeness must consult only candidate backends compatible
        with the current environment profile. Pre-injection the concrete
        backend is unknown, so parameters are the union across candidates for
        the current scope, not every provider registered in the process. This
        prevents a future cloud provider's ``region`` requirement from leaking
        into a Kubernetes or host dialogue.
        Self-bootstraps the built-ins on an empty registry (mirrors
        :meth:`detect_method`)."""
        if not cls._providers:
            cls.register_builtins()
        # Preserve the historical public API when the caller has not resolved
        # an environment yet. New prompt/context callers pass ``profile`` and
        # receive the narrower, environment-compatible union.
        candidates = list(cls._providers.values())
        if profile is not None:
            candidates = cls.resolve_by_scope(scope)
            candidates = [p for p in candidates if p.matches_channel(profile)]
            if not candidates:
                candidates = cls.applicable(profile)
            # A resolved-but-unsupported profile must not inherit every
            # provider's parameters. The caller is expected to fail closed.
            if not candidates:
                return []
        if not candidates:
            candidates = list(cls._providers.values())

        out: list[str] = []
        for provider in candidates:
            for param in provider.required_params(scope or ""):
                if param not in out:
                    out.append(param)
        return out

    @classmethod
    def union_tool_names(cls, attr: str) -> frozenset[str]:
        """Union of a tool-name set attribute across every registered provider.

        Consumption seam for the execute loop's per-tool passes — kubeconfig
        safety-net injection (``kubeconfig_scoped_tool_names``), task_id
        audit binding (``audit_scoped_tool_names``) and L4 SLS log shipping
        (``log_shipping_tool_names``): each pass iterates the union of the
        corresponding provider attribute, so a newly registered provider's
        tools join every pass without touching the generic layer. Providers
        that omit an attribute contribute nothing (getattr default).
        Self-bootstraps the built-ins on an empty registry (mirrors
        :meth:`detect_method`).
        """
        if not cls._providers:
            cls.register_builtins()
        union: set[str] = set()
        for provider in cls._providers.values():
            union.update(getattr(provider, attr, frozenset()) or frozenset())
        return frozenset(union)

    @classmethod
    def classify_tool_target(
        cls, tool_name: str, tool_args: Any, raw_command: str
    ) -> "EffectiveTarget | None":
        """Guard-side target classification for a tool call, enacted by the
        first provider that recognises the call, else ``None``.

        TargetGuard seam (phase-7 T5): the generic classifier
        (``classifier.infer_effective_target``) dispatches here so it holds
        no carrier tool-name branch or carrier vocabulary table — each
        provider classifies its OWN tools (injection, read-only, and any
        embedded delivery riding another carrier's tool). ``tool_args``
        keeps its raw shape; ``raw_command`` is the pre-computed audit
        rendering. Channel-independent (the guard rules on the tool_call
        itself). Providers that omit the hook contribute nothing (getattr
        default); registration order decides overlapping claims.
        """
        if not cls._providers:
            cls.register_builtins()
        for provider in cls._providers.values():
            hook = getattr(provider, "classify_tool_target", None)
            if hook is None:
                continue
            target = hook(tool_name, tool_args, raw_command)
            if target is not None:
                return target
        return None

    @classmethod
    def classify_inline_blade_command(
        cls,
        inner: list[str],
        raw_command: str,
        *,
        fallback_ns: str,
        fallback_pod: str,
    ) -> "EffectiveTarget":
        """Domain-routing seam for an embedded ``blade ...`` CLI
        classification (phase-14 G3, design D3).

        The kubectl-native classifier parsing ``kubectl exec POD -- blade
        create ...`` reaches the blade carrier's inline CLI parser through
        THIS seam instead of a cross-carrier import: the registry is the
        providers package's legitimate vertical routing point, so no
        carrier sub-package reaches sideways into another. The
        ``inner[0] == "blade"`` gate already ran at the caller — this ROUTES
        (a single carrier owns the blade CLI parsing vocabulary), it does
        not arbitrate. Lazy import keeps the registration-time import order
        untouched (see the NOTE in chaosblade.py).
        """
        from chaos_agent.agent.providers.chaosblade.provider import (
            classify_inline_blade,
        )

        return classify_inline_blade(
            inner, raw_command, fallback_ns=fallback_ns, fallback_pod=fallback_pod
        )

    @classmethod
    def parse_injection_params(cls, tool_name: str, tool_args: dict) -> "dict | None":
        """Structured injection parameters for a freshly issued tool call,
        parsed by the first provider that recognises the call, else ``None``.

        Issue-time extraction seam (phase-7 T3): the execute loop consults
        the registry before dispatch, so each backend recognises its own
        tool-call forms (the ChaosBlade provider owns both the direct
        ``blade_create`` call and the ``kubectl exec`` embedded delivery).
        A provider returning ``None`` means "not my carrier" — the scan
        continues; an empty dict means "my carrier, no key parameters".
        Providers that omit the hook contribute nothing (getattr default).
        """
        if not cls._providers:
            cls.register_builtins()
        for provider in cls._providers.values():
            parse = getattr(provider, "parse_injection_params", None)
            if parse is None:
                continue
            parsed = parse(tool_name, tool_args)
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def issue_time_method(
        cls, tool_name: str, tool_args: dict, *, is_host: bool
    ) -> Optional[str]:
        """Issue-time attribution for a freshly-issued tool call, enacted by
        the first provider that recognises the call, else ``None``.

        Direction B seam (phase-7 T4): the execute loop records the
        ``injection_method`` the moment the tool call is ISSUED, so each
        backend recognises its own carrier forms (the ChaosBlade provider
        claims both ``blade_create`` and the ``kubectl exec ... blade
        create`` embedded delivery; k8s-native claims object-write verbs and
        mutating execs; host-shell claims its tools only on a host channel).
        A provider returning ``None`` means "not my carrier" — the scan
        continues. Registration order is load-bearing: ChaosBlade precedes
        k8s-native so an embedded blade delivery is never mis-attributed as
        a mutating exec. Providers that omit the hook contribute nothing
        (getattr default)."""
        if not cls._providers:
            cls.register_builtins()
        for provider in cls._providers.values():
            hook = getattr(provider, "issue_time_method", None)
            if hook is None:
                continue
            method = hook(tool_name, tool_args, is_host=is_host)
            if method is not None:
                return method
        return None

    # -- built-in backends + detection orchestration -----------------------

    @classmethod
    def register_builtins(cls) -> None:
        """Register the built-in execution backends in precedence order.

        Order is load-bearing for :meth:`detect_method` AND
        :meth:`issue_time_method`: ChaosBlade (which owns the UID-bearing
        ``host_blade`` / ``kubectl_exec`` methods) must be probed before the
        UID-less ``k8s_native`` and ``host_shell`` backends, matching the
        original ``_detect_injection_method`` branch order — otherwise an
        embedded ``kubectl exec ... blade create`` delivery would be
        mis-attributed as a mutating exec. Lazy imports break the
        ``registry ← concrete provider ← base`` import cycle and follow the
        codebase's deferred-import style. Idempotent: ``register`` overwrites,
        so calling twice is harmless.

        This is the single ordered bootstrap of the built-in set. The providers
        package invokes it at import time (see ``providers/__init__``) so callers
        never have to remember to bootstrap; it also remains the explicit
        re-registration entry after a ``clear()`` (test fixtures) and the lazy
        self-bootstrap used by :meth:`detect_method` on an empty registry.
        """
        from chaos_agent.agent.providers.chaosblade.provider import ChaosbladeProvider
        from chaos_agent.agent.providers.chaosblade.python_provider import (
            ChaosbladePythonProvider,
        )
        from chaos_agent.agent.providers.host_shell.provider import HostShellProvider
        from chaos_agent.agent.providers.k8s_native.provider import K8sNativeProvider

        # Ordered built-in set — precedence is significant (see docstring).
        # ``chaosblade_python`` is order-insensitive: it is detected by its own
        # injection TOOL name, which no other backend scans, so it never
        # competes for attribution and can sit last.
        for provider_cls in (
            ChaosbladeProvider,
            K8sNativeProvider,
            HostShellProvider,
            ChaosbladePythonProvider,
        ):
            cls.register(provider_cls())

    @classmethod
    def detect_method(cls, messages: list, *, is_host: bool) -> Optional[str]:
        """Resolve the runtime ``injection_method`` by RECENCY, not raw
        precedence: among channel-scoped providers that recognise their carrier
        in ``messages``, the one whose injection evidence is MOST RECENT wins.

        This implements "attribute the LAST successful injection": after a
        replan switches from a failed ``blade_create`` to a kubectl-native
        fallback, the later native injection out-ranks the earlier (stale)
        blade UID instead of being hijacked by it (task-76c59364). Registration
        precedence is retained only as a TIE-BREAKER (equal recency → the
        earlier-registered provider, i.e. ChaosBlade, wins) so all existing
        single-carrier attributions are unchanged.

        Candidates are scoped by CHANNEL first: ``is_host`` maps to a channel
        profile and only providers whose ``matches_channel`` accepts it are
        probed. A host backend (``host_shell``) is therefore never a candidate
        for a k8s injection, and vice versa — the channel is a hard, known fact.

        Self-bootstraps the built-in backends on an empty registry, mirroring
        ``TransportRegistry``'s ``_ensure_default``; an explicitly-populated
        registry (e.g. test fixtures) is left untouched.
        """
        if not cls._providers:
            cls.register_builtins()
        from chaos_agent.transports import PROFILE_HOST, PROFILE_K8S

        profile = PROFILE_HOST if is_host else PROFILE_K8S
        best_method: Optional[str] = None
        best_key: tuple[int, int] | None = None
        for rank, provider in enumerate(cls._providers.values()):
            if not provider.matches_channel(profile):
                continue
            method = provider.detect(messages, is_host=is_host)
            if not method:
                continue
            # Recency is the message index of this provider's injection
            # evidence. Providers predating the seam (e.g. test doubles) have no
            # ``injection_recency`` — fall back to 0 so ties resolve by the
            # registration-order rank below (legacy precedence behaviour).
            recency_fn = getattr(provider, "injection_recency", None)
            recency = (
                recency_fn(messages, is_host=is_host) if recency_fn is not None else 0
            )
            # Higher recency wins; equal recency → lower rank (earlier
            # registration precedence) wins via ``-rank``.
            key = (recency, -rank)
            if best_key is None or key > best_key:
                best_key = key
                best_method = method
        return best_method

    # -- fault-handle orchestration (carrier-neutral) -----------------------

    @classmethod
    def derive_handle_from_legacy(cls, values: dict) -> Optional[dict]:
        """Hydration seam: derive the fault handle from legacy attribution facts.

        The single place carrier fields are READ to build a handle. Used by
        ``materialize_fault_handle`` when a checkpoint / persisted snapshot
        predates ``fault_handle``: the attributed provider (by
        ``injection_method``) is asked first, then every provider may claim
        its own legacy fields in registration order. Returns the first
        non-empty handle, or ``None`` when no backend owns the facts.
        """
        if not cls._providers:
            cls.register_builtins()
        values = dict(values or {})
        provider = cls.resolve_by_method(values.get("injection_method"))
        candidates = [provider] + [
            p for p in cls._providers.values() if p is not provider
        ]
        for candidate in candidates:
            build = getattr(candidate, "build_fault_handle", None)
            if build is None:
                continue
            handle = build(values)
            if handle:
                return handle
        return None

    @classmethod
    def resolve_by_handle_kind(cls, handle: Optional[dict]) -> Optional[FaultProvider]:
        """POST-attribution: resolve the provider owning a fault handle.

        The handle's ``method`` attribution (when present) names the owning
        provider — prefer it over kind-only matching, so a third backend
        sharing a kind can never steal another backend's handle. Fall back to
        the first provider registering the ``kind`` (legacy handles carry no
        method). ``None`` when the handle is empty or no provider owns it."""
        if not cls._providers:
            cls.register_builtins()
        kind = (handle or {}).get("kind")
        if not kind:
            return None
        provider = cls.resolve_by_method((handle or {}).get("method"))
        if provider is None or getattr(provider, "handle_kind", "") != kind:
            provider = next(
                (
                    p
                    for p in cls._providers.values()
                    if getattr(p, "handle_kind", "") == kind
                ),
                None,
            )
        return provider

    @classmethod
    def is_experiment_handle(cls, handle: Optional[dict]) -> bool:
        """Whether ``handle`` belongs to a UID-bearing experiment carrier.

        Pinning membership is the owning provider's declaration, not a
        ``kind`` string comparison in the consumer: ``has_experiment_uid``
        carriers pin their UID into the recovery-handle contract, so a
        future experiment carrier (any ``handle_kind``) is covered by
        registration alone (phase-7 T6)."""
        provider = cls.resolve_by_handle_kind(handle)
        return provider is not None and bool(
            getattr(provider, "has_experiment_uid", False)
        )

    @classmethod
    def resolve_fault_dispatch(
        cls, values: dict
    ) -> tuple[FaultProvider, Optional[dict]]:
        """Fault identity dispatch: resolve ``(provider, identity_handle)``
        for a no-LLM verify / recover entry.

        Shared by the verify and recover chains (phase-4 T5): both must
        resolve the SAME provider for the same state. Identity ownership is
        NOT attribution ownership — a combo task (experiment + native
        mutation) attributes to the native backend, yet its experiment still
        needs the deterministic destroy / status poll. Ownership order
        (strongest first):

        1. **Live experiment claim** — the first-registered UID-bearing
           backend that claims the attribution facts. Registration order
           (not method attribution) preserves the legacy contract that a
           claimed experiment routes to the OS experiment carrier; the
           identity handle IS that experiment handle.
        2. **Message-history experiment claim** — when the durable facts
           are absent (heavily compacted legacy checkpoints), the providers'
           own evidence scan (:meth:`derive_handle_from_messages`) recovers
           a live experiment handle from the history. It routes exactly
           like claim 1 but never overrides a state-based claim.
        3. **Attribution handle** — ``materialize_fault_handle``'s
           carrier-agnostic ownership (native handles resolve to their
           backend through ``method`` / ``kind``).
        4. **Attributed method** — ``resolve_by_method`` on the durable
           ``injection_method``, defaulting to the UID-less native verdict
           backend (nothing to destroy deterministically, no method
           detected).
        """
        if not cls._providers:
            cls.register_builtins()
        values = dict(values or {})
        attribution = values.get("fault_handle")
        attribution = (
            attribution if isinstance(attribution, dict) and attribution else None
        )
        # 1. Live experiment claim (combo-safe: claims outrank attribution).
        for provider in cls._providers.values():
            if not provider.has_experiment_uid:
                continue
            if attribution is not None and getattr(
                provider, "handle_kind", ""
            ) == attribution.get("kind"):
                # An experiment-kind attribution handle already in state IS
                # this claim — prefer it over rebuilding from the legacy
                # fields (it may carry a more precise method attribution).
                if cls.resolve_by_handle_kind(attribution) is provider:
                    return provider, attribution
            handle = provider.build_fault_handle(values)
            if handle:
                return provider, handle
        # 2. Message-history experiment claim (defense seam): a live
        #    experiment handle recovered from the message history routes
        #    like claim 1 (the legacy contract — a live UID reaches the
        #    experiment carrier's destroy) but never overrides a
        #    state-based claim; the scan is the weakest evidence source.
        hint = cls.derive_handle_from_messages(values.get("messages") or [], values)
        if hint is not None:
            provider = cls.resolve_by_handle_kind(hint)
            if provider is not None and provider.has_experiment_uid:
                return provider, hint
        # 3. Attribution handle (hydrated from legacy facts when the
        #    checkpoint predates ``fault_handle``).
        from chaos_agent.agent.state import materialize_fault_handle

        attribution = attribution or materialize_fault_handle(values)
        if attribution is not None:
            provider = cls.resolve_by_handle_kind(attribution)
            if provider is not None:
                return provider, attribution
        # 4. Attributed method, then the UID-less verdict-default carrier
        #    (declared via ``uid_less_verdict_default`` — the registry never
        #    names a provider class; getattr defaults to False for backends
        #    that omit the property).
        provider = cls.resolve_by_method(values.get("injection_method"))
        if provider is not None:
            return provider, None
        for candidate in cls._providers.values():
            if getattr(candidate, "uid_less_verdict_default", False):
                return candidate, None
        return None, None

    @classmethod
    def derive_handle_from_messages(
        cls, messages: list, state: Optional[dict] = None
    ) -> Optional[dict]:
        """Message-history hydration seam: build a fault handle from live
        experiment evidence found in ``messages``, when neither the handle
        nor the legacy attribution facts are present (heavily compacted /
        legacy checkpoints).

        Each UID-bearing backend scans for its OWN experiment evidence and
        builds its own handle — the registry never names a carrier. Returns
        the first claim, or ``None`` when nothing is found.
        """
        if not cls._providers:
            cls.register_builtins()
        state = state or {}
        retired = state.get("retired_experiment_uids")
        for provider in cls._providers.values():
            build = getattr(provider, "build_handle_from_messages", None)
            if build is None:
                continue
            handle = build(messages, retired=retired, values=state)
            if handle:
                return handle
        return None

    @classmethod
    def extract_experiment_uid(
        cls, messages: list, retired=None, *, is_host: bool
    ) -> str:
        """Live experiment id present in ``messages``, claimed by the first
        channel-compatible UID-bearing provider, else ``""``.

        Replaces the execute loop's direct call to a carrier-specific
        extractor: the generic attribution sync asks the registry, and each
        provider scans for its own experiment id."""
        if not cls._providers:
            cls.register_builtins()
        from chaos_agent.transports import PROFILE_HOST, PROFILE_K8S

        profile = PROFILE_HOST if is_host else PROFILE_K8S
        for provider in cls._providers.values():
            if not provider.has_experiment_uid:
                continue
            if not provider.matches_channel(profile):
                continue
            extract = getattr(provider, "extract_experiment_id", None)
            if extract is None:
                continue
            uid = extract(messages, retired)
            if uid:
                return uid
        return ""

    @classmethod
    def created_experiment_ids(cls, messages: list, state: dict) -> set[str]:
        """Provenance union: every experiment id ANY provider proves this task
        created (each backend scans its own create results and claims its own
        durable record).

        The single carrier-neutral seam generic nodes consult instead of
        naming a carrier extractor — the tool screener's destroy gate uses it
        to whitelist ``blade destroy`` UIDs (a task may only destroy
        experiments it created itself, failed-create CRDs included)."""
        if not cls._providers:
            cls.register_builtins()
        uids: set[str] = set()
        for provider in cls._providers.values():
            collect = getattr(provider, "created_experiment_ids", None)
            if collect is None:
                continue
            uids.update(collect(messages, state))
        return uids

    @classmethod
    def destroyed_experiment_ids(cls, messages: list) -> set[str]:
        """Terminal-state union: every experiment id ANY UID-bearing provider
        proves has been sent to a destroy (each backend scans its own destroy
        tool calls).

        Death-filter companion of :meth:`created_experiment_ids` (provenance
        union): destruction is a terminal-state FACT — a destroy issued by any
        carrier kills that experiment regardless of channel, so the
        aggregation is a UNION with NO channel filtering (the replan seam's
        compression-boundary fallback applies the same unconditional
        full-history scan). Providers that omit the hook contribute nothing
        (getattr default)."""
        if not cls._providers:
            cls.register_builtins()
        uids: set[str] = set()
        for provider in cls._providers.values():
            if not provider.has_experiment_uid:
                continue
            scan = getattr(provider, "destroyed_experiment_ids", None)
            if scan is None:
                continue
            uids.update(scan(messages))
        return uids

    @classmethod
    def recover_experiment_uid_from_session(
        cls, session_messages: list, *, retired=None
    ) -> str:
        """Recover the experiment UID from persisted SESSION message dicts
        (task-file format), claimed by the first UID-bearing provider, else
        ``""``.

        Session-recovery seam (phase-13 D2): the task file persists messages
        as plain dicts — some carrying nested ``detail`` payloads the
        langchain conversion cannot fully represent — and the recovery runs
        with NO channel context (the task file does not record one). The
        seam orchestrates three layers, mirroring the single carrier-family
        function the task-snapshot reader used to call directly:

        1. best-effort langchain conversion (:func:`_session_messages_to_langchain`;
           a failed conversion falls through to the dict layer, never aborts);
        2. per-provider ``extract_experiment_id`` over the converted history —
           NO channel filtering (the ``derive_handle_from_messages``
           precedent: recovery cannot recover the channel, and the UID
           shapes are strict enough that cross-channel mis-extraction is
           negligible);
        3. dict-literal fallback via ``extract_experiment_id_from_session_dict``
           — each provider reads its own command vocabulary off the raw dict
           payloads (``detail.command`` etc.); that vocabulary judgement
           belongs to the carrier side, not the generic layer.

        ``retired`` defaults to ``None`` (session files carry no retired set);
        the parameter is kept for future callers holding one.
        """
        if not cls._providers:
            cls.register_builtins()
        if not isinstance(session_messages, list) or not session_messages:
            return ""
        try:
            langchain_messages = _session_messages_to_langchain(session_messages)
        except Exception:
            logger.debug(
                "Session message conversion failed; falling to dict layer",
                exc_info=True,
            )
            langchain_messages = []
        if langchain_messages:
            for provider in cls._providers.values():
                if not provider.has_experiment_uid:
                    continue
                extract = getattr(provider, "extract_experiment_id", None)
                if extract is None:
                    continue
                try:
                    uid = extract(langchain_messages, retired)
                except Exception:
                    logger.debug(
                        "Provider %r session-history extraction failed",
                        provider.carrier,
                        exc_info=True,
                    )
                    uid = ""
                if uid:
                    return uid
        for provider in cls._providers.values():
            if not provider.has_experiment_uid:
                continue
            fallback = getattr(
                provider, "extract_experiment_id_from_session_dict", None
            )
            if fallback is None:
                continue
            try:
                uid = fallback(session_messages)
            except Exception:
                logger.debug(
                    "Provider %r session-dict extraction failed",
                    provider.carrier,
                    exc_info=True,
                )
                uid = ""
            if uid:
                return uid
        return ""

    @classmethod
    async def rollback_handle(cls, handle: dict, **kwargs) -> str:
        """Dispatch a failure-path auto-rollback to the provider owning the
        handle's ``kind``; returns a human-readable status suffix
        (``""`` = nothing rolled back). Backends without a deterministic undo
        decline, so a UID-less fault is never fed to a carrier-specific
        destroy."""
        provider = cls.resolve_by_handle_kind(handle)
        if provider is None:
            return ""
        rollback = getattr(provider, "rollback_handle", None)
        if rollback is None:
            return ""
        return await rollback(handle, **kwargs)

    @classmethod
    def extract_kubectl_exec_pod_name(cls, messages: list) -> Optional[str]:
        """Dispatch the kubectl-exec delivery-pod extraction to the backend
        that owns that delivery (resolved by its ``kubectl_exec`` method —
        the ChaosBlade backend), so the generic execute loop records the
        delivery pod without importing a concrete provider module
        (phase-8 T4). Returns ``None`` when the owner is absent or does not
        implement the hook (resolve-then-getattr, the :meth:`rollback_handle`
        pattern)."""
        provider = cls.resolve_by_method("kubectl_exec")
        if provider is None:
            return None
        extract = getattr(provider, "extract_kubectl_exec_pod_name", None)
        if extract is None:
            return None
        return extract(messages)

    # -- test / lifecycle helpers ------------------------------------------

    @classmethod
    def clear(cls) -> None:
        """Drop all registrations. For tests that install fixtures."""
        cls._providers = {}
        cls._method_index = {}


__all__ = ["FaultProviderRegistry"]
