"""Classify a tool_call into the resource it would actually act on.

Companion to ``guard.py`` — the classifier produces an
``EffectiveTarget``; the guard compares that to ``ApprovedTarget``
and emits a ``GuardDecision``.

Since phase-7 T5 this module is the GENERIC layer of the target-guard
classification: the top-level entry point (:func:`infer_effective_target`)
plus the cross-carrier shared helpers (kind canonicalisation, namespace /
label-selector parsing). Every carrier's OWN tool vocabulary — the
``blade_create`` dict-arg classifier and inline ``kubectl exec ... blade``
CLI parser (``providers/chaosblade/provider.py``), the kubectl command-line family
(``providers/k8s_native/classifier.py``), the python-agent and host-shell
classifiers — is enacted through ``FaultProviderRegistry.classify_tool_target``
so this layer holds no carrier tool-name branch or carrier vocabulary table.

Verdict coverage policy (unchanged semantics, wherever the classifier runs):

  - **READONLY**: known read-only tools (this layer's generic table, or a
    provider claiming its own read-only tools). Sentinel
    ``scope="__readonly__"`` — no comparison needed.
  - **BANNED**: calls outside the target-scoped operation model (e.g.
    ``_execute_skill_script`` without the operator opt-in). Sentinel
    ``scope="__banned__"``.
  - **UNKNOWN**: anything else — unrecognised tool name, unrecognised
    subcommand, malformed args. Sentinel ``scope="__unknown__"`` so the
    guard can emit ``REJECT_UNKNOWN``.
"""

from __future__ import annotations

import logging
from typing import Any

from .types import (
    ConfidenceLevel,
    EffectiveTarget,
    SCOPE_BANNED,
    SCOPE_ESCAPE,
    SCOPE_READONLY,
    SCOPE_UNKNOWN,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinel scopes — the guard knows these aren't real k8s kinds. Canonical
# home is types.py since phase-7 T5 (see the migration note there); imported
# above for the constructions below and re-exported for the guard-side
# consumers that historically imported them from this module.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Compliant forms, paired one-to-one with the causes recorded below.
#
# Every rejection must carry BOTH halves: ``reject_detail`` says what went
# wrong, ``reject_suggestion`` says what to do about THAT. The guard only falls
# back to a generic template when neither is recorded — so a cause without its
# own fix silently borrows a fix written for a different cause, and the two then
# contradict each other. task-866648cc is what that costs: a rejection whose
# reason named one subsystem while its suggestion pointed at another, and the
# model spent nine minutes acting on the wrong half.
#
# The split is by WHAT THE MODEL MUST CHANGE, not by subcommand:
#   - a name that does not exist   → change the name (arguments cannot help)
#   - a target that was not stated → add the positional argument
#   - an ambiguous target          → qualify it as <kind>/<name>
# Telling a model to "state the target" when the TOOL NAME is wrong sends it
# back to re-issue the same non-existent call with more arguments — a retry
# loop rather than a repair.
#
# ``SCOPE_UNKNOWN`` never becomes a hard floor (see ``guard_gateway``), so these
# only ever improve the repair hint; they cannot widen what the guard permits.
# For ``SCOPE_BANNED`` the opposite holds — an EMPTY suggestion is load-bearing
# there (it is what reports a boundary rather than a reshapeable call), so bans
# with no drill form deliberately keep none.
# ---------------------------------------------------------------------------

_FIX_UNKNOWN_TOOL = (
    "This is not a tool that exists in this phase — no argument will make it "
    "valid. Re-issue the operation with one of the tools bound for the current "
    "phase."
)


# ---------------------------------------------------------------------------
# Kind canonicalisation — kubectl accepts singular / plural / short
# forms interchangeably. The guard MUST normalise both sides
# (approved + effective) to the canonical singular form, otherwise
# legitimate same-target calls get rejected for cosmetic mismatch.
# ---------------------------------------------------------------------------

# Maps every accepted spelling (singular/plural/short) to canonical
# singular. Group/version suffixes (``.apps`` / ``.v1.apps``) are
# stripped before lookup so ``deployment.apps`` matches ``deployment``.
KIND_ALIASES: dict[str, str] = {
    # Core
    "pod": "pod", "pods": "pod", "po": "pod",
    # ``container`` is not a real k8s kind, but ChaosBlade uses
    # scope=container for in-container chaos. The container lives
    # inside a pod and the guard tracks pod identity — so canonicalise
    # to "pod". Without this alias, ``blade_create(scope="container")``
    # would fall through to BLADE_TARGET_TO_SCOPE[target] and a
    # container-cpu call would mis-resolve to scope="node" (host CPU)
    # and false-positive as drift.
    "container": "pod", "containers": "pod",
    "node": "node", "nodes": "node", "no": "node",
    "service": "service", "services": "service", "svc": "service",
    "namespace": "namespace", "namespaces": "namespace", "ns": "namespace",
    "configmap": "configmap", "configmaps": "configmap", "cm": "configmap",
    "secret": "secret", "secrets": "secret",
    "persistentvolumeclaim": "pvc", "pvc": "pvc", "pvcs": "pvc",
    "persistentvolume": "pv", "pv": "pv", "pvs": "pv",
    "serviceaccount": "serviceaccount", "serviceaccounts": "serviceaccount", "sa": "serviceaccount",
    "endpoints": "endpoints", "ep": "endpoints",
    "event": "event", "events": "event", "ev": "event",
    # apps/v1
    "deployment": "deployment", "deployments": "deployment", "deploy": "deployment",
    "daemonset": "daemonset", "daemonsets": "daemonset", "ds": "daemonset",
    "statefulset": "statefulset", "statefulsets": "statefulset", "sts": "statefulset",
    "replicaset": "replicaset", "replicasets": "replicaset", "rs": "replicaset",
    "replicationcontroller": "replicationcontroller", "replicationcontrollers": "replicationcontroller", "rc": "replicationcontroller",
    # batch
    "job": "job", "jobs": "job",
    "cronjob": "cronjob", "cronjobs": "cronjob", "cj": "cronjob",
    # networking
    "ingress": "ingress", "ingresses": "ingress", "ing": "ingress",
    "networkpolicy": "networkpolicy", "networkpolicies": "networkpolicy", "netpol": "networkpolicy",
    # autoscaling
    "horizontalpodautoscaler": "hpa", "horizontalpodautoscalers": "hpa", "hpa": "hpa",
    # rbac
    "role": "role", "roles": "role",
    "rolebinding": "rolebinding", "rolebindings": "rolebinding",
    "clusterrole": "clusterrole", "clusterroles": "clusterrole",
    "clusterrolebinding": "clusterrolebinding", "clusterrolebindings": "clusterrolebinding",
    # storage
    "storageclass": "storageclass", "storageclasses": "storageclass", "sc": "storageclass",
    # custom resources — operator may install many; we recognise common ChaosBlade ones explicitly
    "chaosblade": "chaosblade", "chaosblades": "chaosblade",
}


def canonicalise_kind(raw: str) -> str:
    """Normalise a kind string to canonical singular form.

    Strips the ``.group`` / ``.group.version`` suffix kubectl
    sometimes accepts (e.g. ``deployment.apps``). Lowercases. Falls
    back to the input unchanged when no alias is known — caller
    treats unknown kinds as ``__unknown__`` via the guard rather
    than silently coercing.
    """
    if not raw:
        return ""
    # Strip .group / .group.version suffix
    head = raw.split(".", 1)[0].lower().strip()
    return KIND_ALIASES.get(head, head)


# ---------------------------------------------------------------------------
# Namespace parsing — handles all 5 kubectl flag forms.
# ---------------------------------------------------------------------------

_NS_FLAG_LONG = "--namespace"
_NS_FLAG_SHORT = "-n"


def parse_namespace(args: list[str], default: str = "default") -> str:
    """Extract the namespace from a kubectl arg list.

    Handles:
      - ``-n ns``
      - ``-n=ns``
      - ``--namespace ns``
      - ``--namespace=ns``
      - flag in any position (before OR after the subcommand)

    Stops at the ``--`` separator — anything after it belongs to an
    INNER command (``kubectl exec POD -- prog ...``) whose own ``-n``
    flag must not leak into the outer kubectl's namespace inference.

    Returns the explicit namespace, or ``default`` if no flag found.
    The caller should pass ``default=""`` for cluster-scoped
    subcommands (node/cordon/taint/etc) so missing namespace doesn't
    get auto-promoted to "default".
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return default
        # Equals form: -n=ns / --namespace=ns
        if a.startswith(_NS_FLAG_SHORT + "=") or a.startswith(_NS_FLAG_LONG + "="):
            return a.split("=", 1)[1]
        # Spaced form: -n ns / --namespace ns
        if a == _NS_FLAG_SHORT or a == _NS_FLAG_LONG:
            if i + 1 < len(args):
                return args[i + 1]
            return default  # malformed: flag with no value
        i += 1
    return default


# ---------------------------------------------------------------------------
# Label selector parsing — -l / --selector
# ---------------------------------------------------------------------------


def parse_labels(args: list[str]) -> dict[str, str]:
    """Extract the label selector from ``-l``/``--selector``/``--labels`` flags.

    Returns a dict of {key: value}. Operator-style selectors
    (``key!=value``, ``key in (v1,v2)``) are flattened to {key: raw}
    so equality-comparison stays simple — the guard treats any
    non-trivial selector difference as drift anyway.
    Missing flag returns {}.

    Recognises both kubectl flags (``-l``, ``--selector``) and the
    ChaosBlade CLI flag (``--labels``) so that inline ``blade create``
    commands inside ``kubectl exec`` are correctly classified.

    Stops at the ``--`` separator so a ``kubectl exec POD -- prog -l x``
    doesn't leak the inner program's ``-l`` flag into the outer
    kubectl's label-selector inference.
    """
    selector: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        raw_selector = ""
        if a in ("-l", "--selector", "--labels"):
            if i + 1 < len(args):
                raw_selector = args[i + 1]
                i += 1
        elif a.startswith("-l=") or a.startswith("--selector=") or a.startswith("--labels="):
            raw_selector = a.split("=", 1)[1]
        if raw_selector:
            for pair in raw_selector.split(","):
                pair = pair.strip()
                # Operator-style (``!=`` / ``>=`` / ``<=`` / ``in`` /
                # ``notin``) preserve verbatim so the guard treats
                # ``app!=demo`` as a single distinguishable selector
                # entry instead of decomposing ``app!`` as the key.
                if ("!=" in pair or ">=" in pair or "<=" in pair
                        or " in " in pair or " notin " in pair):
                    selector[pair] = pair
                elif "=" in pair:
                    k, _, v = pair.partition("=")
                    selector[k.strip()] = v.strip()
                else:
                    # bare key — preserve verbatim
                    selector[pair] = pair
        i += 1
    return selector


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def infer_effective_target(
    tool_name: str,
    tool_args: dict[str, Any] | str | list[str] | None,
    *,
    skill_script_allowed: bool = False,
) -> EffectiveTarget:
    """Top-level classifier — produce an EffectiveTarget for one tool_call.

    Args:
        tool_name: LangChain tool name (e.g. ``blade_create``,
            ``kubectl``, ``_execute_skill_script``,
            ``read_knowledge_resource``).
        tool_args: The tool's parsed arguments. Shape depends on tool:
            - ``blade_create``: dict with scope/target/action/namespace/names/labels
            - ``kubectl``: dict with ``command`` (list[str]) OR ``args``
              (str shell-quoted) OR list[str] directly
            - ``_execute_skill_script``: dict with script path / args
            - others: depends; classifier returns READONLY for known
              read-only tools and UNKNOWN for everything else.
        skill_script_allowed: Whether the operator has opted into
            allowing ``_execute_skill_script`` (default False = banned).
            Tied to ``settings.skill_script_default_allow`` at the
            caller side.

    Returns:
        EffectiveTarget — see ``types.EffectiveTarget`` for fields.
        Sentinel scopes ``__readonly__`` / ``__banned__`` /
        ``__unknown__`` signal special verdicts to the guard.
    """
    raw_command = _format_raw_command(tool_name, tool_args)

    # Known read-only tools. Guard maps these to READONLY verdict.
    # ``read_file`` / ``save_fault_plan`` touch the local FS only (not the
    # cluster) — safe for both phases. Carrier-owned read-only tools
    # (blade_help / blade_status / blade_query_k8s / blade_python_prepare /
    # blade_python_revoke / host_read) are claimed by their providers'
    # ``classify_tool_target`` hooks via the registry dispatch below
    # (phase-7 T5) — the vocabulary lives with its carrier.
    # NOTE: ``kubectl`` / ``kubectl_read`` are intentionally NOT in this
    # short-circuit — they are claimed by the k8s-native provider's hook,
    # whose classifier rules on the inner command too (``exec``/``debug``
    # with a read-only inner → READONLY; mutating inner → pod/escape,
    # which the screeners reject).
    if tool_name in ("read_knowledge_resource", "read_skill_resource",
                     "activate_skill", "submit_fault_intent",
                     "read_file", "save_fault_plan",
                     "finish_planning", "propose_plan_change",
                     "submit_verification", "submit_recover_verification",
                     "request_replan",
                     "time_wait",
                     # Progress ledger write: a pure control-signal note with no
                     # cluster side effect (touches no fault target), same class
                     # as request_replan / time_wait.
                     "update_progress"):
        return EffectiveTarget(
            scope=SCOPE_READONLY,
            namespace="",
            raw_command=raw_command,
        )

    # Skill script — banned by default; opt-in flag flips it to a
    # READONLY pass-through. Reasoning:
    #   - Default ``skill_script_default_allow=False`` returns BANNED
    #     so the screener blocks the call in enforcing mode.
    #   - When the operator flips the flag to True, they have decided
    #     the bundled skill scripts are trusted. We can't inspect the
    #     script's effect on k8s resources, so we treat the call as
    #     READONLY for guard purposes — pass-through with an INFO log
    #     for audit. (Previous behaviour returned UNKNOWN which the
    #     guard still rejected, making the flag a no-op.)
    if tool_name in ("_execute_skill_script", "execute_skill_script"):
        if not skill_script_allowed:
            return EffectiveTarget(
                scope=SCOPE_BANNED,
                namespace="",
                raw_command=raw_command,
                confidence=ConfidenceLevel.HIGH,
                reject_detail=(
                    "skill-script execution is disabled "
                    "(skill_script_default_allow=false); its effect on cluster "
                    "resources cannot be inspected"
                ),
                reject_suggestion=(
                    "Express the drill with the kubectl / blade tools instead — "
                    "the guard can classify their targets and compare them "
                    "against the approved one. Enabling the flag is an operator "
                    "decision that accepts an unclassifiable call, not "
                    "something to work around here."
                ),
            )
        return EffectiveTarget(
            scope=SCOPE_READONLY,
            namespace="",
            raw_command=raw_command,
            confidence=ConfidenceLevel.HIGH,
        )

    # Carrier dispatch (phase-7 T5): each provider classifies its OWN tools
    # (injection, read-only, and embedded deliveries riding another carrier's
    # tool) through the registry — this generic layer holds no carrier
    # tool-name branch or carrier vocabulary table. Channel-independent: the
    # guard rules on the tool_call itself.
    from chaos_agent.agent.providers.registry import FaultProviderRegistry

    classified = FaultProviderRegistry.classify_tool_target(
        tool_name, tool_args, raw_command,
    )
    if classified is not None:
        return classified

    # Unknown tool — default-deny. Forces operator to add explicit
    # classification rather than silently allowing new tools.
    return EffectiveTarget(
        scope=SCOPE_UNKNOWN,
        namespace="",
        raw_command=raw_command,
        confidence=ConfidenceLevel.UNKNOWN,
        reject_detail=(
            f"unrecognized tool '{tool_name}' (default-deny; add explicit "
            "classification)"
        ),
        reject_suggestion=_FIX_UNKNOWN_TOOL,
    )


# ---------------------------------------------------------------------------
# Carrier classifier migrations (phase-7 T5) — every carrier's tool
# classification now lives in its provider domain, enacted through the
# registry dispatch in :func:`infer_effective_target`:
#   - blade_create (dict args) + inline ``kubectl exec ... blade`` CLI
#     parser + BLADE_TARGET_TO_SCOPE → providers/chaosblade/provider.py
#   - kubectl command-line family (60+ subcommands, vocab constants,
#     escape/vehicle/facts logic) → providers/k8s_native/classifier.py
#   - blade_python_create → providers/chaosblade/python_provider.py
#   - host_inject → providers/host_shell/provider.py
# Pure moves — behaviour is byte-identical (the guard is a security layer).
# ---------------------------------------------------------------------------








def _format_raw_command(tool_name: str, tool_args: Any) -> str:
    """Build a short, audit-friendly representation of the tool call."""
    if isinstance(tool_args, dict):
        parts = [f"{k}={v!r}" for k, v in tool_args.items()]
        return f"{tool_name}({', '.join(parts)})"
    if isinstance(tool_args, list):
        return f"{tool_name}({' '.join(str(x) for x in tool_args)})"
    if isinstance(tool_args, str):
        return f"{tool_name}({tool_args})"
    return f"{tool_name}(?)"


__all__ = [
    "KIND_ALIASES",
    "SCOPE_BANNED",
    "SCOPE_ESCAPE",
    "SCOPE_READONLY",
    "SCOPE_UNKNOWN",
    "canonicalise_kind",
    "infer_effective_target",
    "parse_labels",
    "parse_namespace",
]
