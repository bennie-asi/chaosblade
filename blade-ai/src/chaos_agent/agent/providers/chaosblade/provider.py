"""ChaosBlade execution backend provider.

Backend semantics: faults are injected with ``blade create`` and undone with
``blade destroy <uid>``. Two *delivery* variants share this backend (and this
provider):

- ``host_blade`` — the local (host/agent-installed) blade binary runs
  ``blade create k8s ...`` against the API server. The "host" in the method
  name is the delivery *location*, not the fault target: it still creates a
  K8s experiment.
- ``kubectl_exec`` — fallback used when the local blade binary is unavailable /
  incompatible: ``kubectl exec <tool-pod> -- blade create ...``. Recovery for
  this variant must also go through ``kubectl exec`` (a host ``blade destroy``
  cannot find the CRD-created experiment record).

This provider fully owns every per-backend behaviour for the ChaosBlade
carrier: tool binding (``tools``), injection detection (``detect``), the
kubectl-exec delivery-pod extraction (``extract_kubectl_exec_pod_name`` /
``recovery_vehicle``), Layer-1 verification (``layer1_verify`` — host-blade
vs kubectl-exec dispatch), the post-injection verifier note
(``verify_prompt_note``), the recover Layer-2 framing
(``recover_layer2_context``), and deterministic recovery (``recover`` /
``layer1_destroy``). There are no ChaosBlade branches left anywhere outside
this module.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import AIMessage, ToolMessage

from .declaration import (
    CARRIER_ID,
    SUPPORTED_ACTIONS,
    SUPPORTED_TARGETS,
)
from chaos_agent.agent.providers.base import (
    ProviderPrompts,
    RecoverResult,
    coerce_tool_args_dict,
)
from chaos_agent.agent.providers.message_scanning import (
    KUBECTL_COMMAND_SUBCOMMANDS,
    build_tool_call_args_lookup,
)
from chaos_agent.transports import PROFILE_HOST, PROFILE_K8S

# NOTE: this module must keep importing ONLY providers-internal + stdlib
# modules at module level — "providers-internal" now includes the sibling
# modules of this chaosblade subpackage (relative ``from .verify`` etc.), the
# carrier-neutral flat module ``providers.message_scanning``, plus the
# top-level ``providers.base`` / ``providers.registry``.
# phase-12 note: the spec layer no longer imports this module at all
# (``fault_registry`` reads registered declaration data; settings'
# auto-detection reads the declaration surface too), so no transitional
# re-export remains — but the conservative lazy-import discipline for
# target_guard symbols (phase-7 T5) is kept: relaxing it buys nothing and
# re-risks cycles.

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from chaos_agent.agent.result.verdict import Layer1Result

    from chaos_agent.agent.target_guard.types import EffectiveTarget

logger = logging.getLogger(__name__)

# UID shape inside a FAILED blade_create result (``UID: <uid>`` / JSON) — a
# terminal create failure never counts as an active experiment, but its CRD
# may still exist and needs cleanup, so the provenance gate must accept it.
_FAILED_CREATE_UID_RE = re.compile(
    r'(?:UID:\s*|"uid"\s*:\s*")([a-fA-F0-9][a-fA-F0-9-]{7,})'
)


# ---------------------------------------------------------------------------
# kubectl-exec delivery-pod extraction — this carrier's delivery-form domain
# knowledge, migrated from nodes/execute/_injection_detection.py (phase-3 T3)
# ---------------------------------------------------------------------------

# Pod name pattern: lowercase alphanumeric with hyphens (Kubernetes naming)
_POD_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# kubectl exec flags that consume a following value. Anything else starting
# with '-' is treated as a boolean flag (-i/-t/-q/--stdin/--tty/...).
_EXEC_FLAGS_WITH_VALUE = frozenset(
    {
        "-n",
        "--namespace",
        "-c",
        "--container",
        "--profile",
        "--context",
        "--kubeconfig",
        "--pod-running-timeout",
    }
)


def _parse_pod_name_from_v_args(v_args: str) -> str | None:
    """Extract the pod name from kubectl exec v_args.

    kubectl accepts the pod name either before or after exec flags —
    ``<pod> -n <ns> -- cmd`` and ``-n <ns> <pod> -- cmd`` are both valid —
    so scan for the first positional token before the ``--`` separator
    instead of assuming it comes first. Task-2d612caa: an ``-n``-prefixed
    call escaped extraction, Layer 1 lost the original injection pod and
    read an unrelated tool pod's empty local DB as experiment failure.

    Returns:
        Pod name if valid, None if v_args is empty or no positional pod
        token appears before ``--``.
    """
    if not v_args:
        return None
    tokens = v_args.strip().split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            break
        if tok.startswith("-"):
            # --flag=value carries its own value; the flags in
            # _EXEC_FLAGS_WITH_VALUE consume the following token.
            if "=" not in tok and tok in _EXEC_FLAGS_WITH_VALUE:
                i += 1
            i += 1
            continue
        # First positional token is the pod slot — accept it only if it
        # looks like a pod name.
        if _POD_NAME_RE.match(tok):
            return tok
        return None
    return None


def _find_pod_name_from_aimessages(
    messages: list, *, v_args_hint: str = ""
) -> str | None:
    """Fallback: scan AIMessages for kubectl exec blade create tool calls.

    Used when ToolMessage lacks tool_call_id (older session format).
    Returns the pod name from the most recent matching AIMessage.
    """
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in reversed(tool_calls):
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {})
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {})
            if name != "kubectl":
                continue
            subcommand = args.get("subcommand", "")
            v_args = args.get("v_args", "") or ""
            if subcommand == "exec" and v_args_hint in v_args and "create" in v_args:
                pod_name = _parse_pod_name_from_v_args(v_args)
                if pod_name:
                    return pod_name
    return None


def extract_kubectl_exec_pod_name(messages: list) -> str | None:
    """Extract the tool pod name used for kubectl exec blade injection.

    When the LLM injects a fault via `kubectl exec <pod> -n chaosblade -- blade create ...`,
    the pod name is the first token in the v_args field of the AIMessage's tool_calls.

    This function scans messages in reverse to find the most recent kubectl exec
    blade create call that succeeded (ChaosBlade success JSON in ToolMessage),
    then extracts the pod name from the corresponding AIMessage's v_args.

    Returns:
        Pod name string if found, None otherwise.
    """
    lookup = build_tool_call_args_lookup(messages)

    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") != "kubectl":
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        # Must be a successful ChaosBlade injection
        if not (
            isinstance(data, dict)
            and data.get("success") is True
            and data.get("code") == 200
            and isinstance(data.get("result"), str)
            and data["result"]
        ):
            continue

        tc_id = getattr(msg, "tool_call_id", "")
        if tc_id and tc_id in lookup:
            args = lookup[tc_id]
            subcommand = args.get("subcommand", "")
            v_args = args.get("v_args", "") or ""
            if subcommand == "exec" and "blade" in v_args and "create" in v_args:
                pod_name = _parse_pod_name_from_v_args(v_args)
                if pod_name:
                    return pod_name
            continue
        elif tc_id:
            continue

        # No tool_call_id (older session format) — scan AIMessages directly
        pod_name = _find_pod_name_from_aimessages(messages, v_args_hint="blade")
        if pod_name:
            return pod_name

    return None


# ---------------------------------------------------------------------------
# Issue-time injection-parameter parsing — this carrier's tool-call-form
# domain knowledge, migrated from nodes/execute/execute_loop.py and
# utils/fault_type.py (phase-7 T3)
# ---------------------------------------------------------------------------

# Regex for: blade create k8s <scope>-<target> <action>
# e.g. "blade create k8s pod-network drop --percent 100 ..."
_BLADE_CREATE_K8S_RE = re.compile(r"blade\s+create\s+k8s\s+(\w+)-(\w+)\s+(\w+)")


def _parse_blade_create_from_v_args(v_args: str) -> dict | None:
    """Parse scope/target/action/flags from kubectl exec blade create v_args.

    Returns dict with scope/target/action, plus ``flags`` if present, or None
    if v_args does not contain a ``blade create k8s`` command.
    """
    match = _BLADE_CREATE_K8S_RE.search(v_args)
    if not match:
        return None
    result = {
        "scope": match.group(1),
        "target": match.group(2),
        "action": match.group(3),
    }
    flags_str = v_args[match.end() :].strip()
    if flags_str:
        result["flags"] = flags_str
    return result


def parse_blade_flags(flags_str: str) -> dict[str, str]:
    """Parse key parameters from blade flags string.

    Extracts structured parameter values from the raw flags string
    for verifier/recover_verifier consumption.

    Returns dict with only the parameters found, e.g.:
      {"path": "/tmp", "percent": "85", "timeout": "600"}
    """
    # Key parameters that affect verification strategy
    KEY_PARAMS = {
        "path",
        "percent",
        "size",
        "timeout",
        "time",
        "cpu-percent",
        "mem-percent",
    }
    result: dict[str, str] = {}
    if not flags_str:
        return result
    try:
        tokens = shlex.split(flags_str)
    except ValueError:
        return result
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--"):
            key, separator, inline_value = token[2:].partition("=")
            if key in KEY_PARAMS and separator:
                result[key] = inline_value
                i += 1
                continue
            if key in KEY_PARAMS and i + 1 < len(tokens):
                result[key] = tokens[i + 1]
                i += 2
                continue
        i += 1
    return result


# ---------------------------------------------------------------------------
# Guard-side target classification — this carrier's tool-classification
# domain knowledge, migrated from target_guard/classifier.py (phase-7 T5).
# Pure move: behaviour is byte-identical (the guard is a security layer).
# ---------------------------------------------------------------------------

# ChaosBlade ``--target`` to k8s scope mapping. Used to detect whether
# a blade_create call's k8s effect is on a pod, node, or unknown.
# Pod-attached resources (container, jvm, mysql in pod) all resolve
# to scope=pod. Host-level chaos (cpu/mem/disk/network without k8s
# prefix) resolves to scope=node.


def _classify_blade_create(args: dict, raw_command: str) -> EffectiveTarget:
    """Classify a ``blade_create`` tool_call.

    Schema (approximate, matches ChaosBlade k8s plugin):
        scope: "pod" / "node" / "container" (sometimes the fault_target)
        target: ChaosBlade --target (cpu/mem/network/jvm/...)
        action: ChaosBlade action (fullload/burn/loss/...)
        namespace: pod namespace
        names: list[str] of pod / node names
        labels: dict label selector
    """
    # Lazy import: target_guard symbols can't be imported at module level from
    # a provider module (see the NOTE at the top of this file).
    from chaos_agent.agent.target_guard.types import (
        ConfidenceLevel,
        EffectiveTarget,
        SCOPE_UNKNOWN,
    )

    fault_target = str(args.get("target") or args.get("blade_target") or "").lower()
    fault_action = str(args.get("action") or args.get("blade_action") or "").lower()
    raw_scope = str(args.get("scope") or args.get("blade_scope") or "").lower()

    # Host scope (bare-metal / VM faults) — identity is the host name, not a
    # k8s namespace/labels selector. Recognised explicitly so host carriers
    # don't fall through to the k8s scope resolution below and mis-resolve
    # to scope=node. Deeper host-drift comparison is layered on in P1/P2.
    # Lazy import: fault_registry imports this provider at module level, so a
    # module-level import here would be circular.
    from chaos_agent.agent.spec.fault_registry import is_host_scope

    if is_host_scope(raw_scope):
        host_names_raw = args.get("names") or args.get("host_name") or []
        if isinstance(host_names_raw, str):
            host_names_raw = [n.strip() for n in host_names_raw.split(",") if n.strip()]
        host_names = tuple(str(n) for n in host_names_raw if n)
        host_name = host_names[0] if host_names else str(args.get("host_name") or "")
        return EffectiveTarget(
            scope="host",
            namespace="",
            names=host_names,
            host_name=host_name,
            fault_target=fault_target,
            fault_action=fault_action,
            confidence=ConfidenceLevel.HIGH if host_name else ConfidenceLevel.LOW,
            raw_command=raw_command,
        )

    # Resolve k8s scope: prefer explicit ``scope`` field if it
    # canonicalises to a known kind; otherwise fall back to
    # fault_target → scope mapping. Lazy import: the classifier lazily
    # imports the provider registry, so a module-level import here would be
    # circular (phase-7 T5).
    from chaos_agent.agent.target_guard.classifier import canonicalise_kind

    scope = canonicalise_kind(raw_scope) if raw_scope else ""
    if not scope or scope not in {"pod", "node"}:
        scope = BLADE_TARGET_TO_SCOPE.get(fault_target, SCOPE_UNKNOWN)

    namespace = str(args.get("namespace") or "").strip()
    # Cluster-scoped resources (node) keep namespace=""; namespace-scoped
    # default to "default" if absent.
    if not namespace and scope != "node":
        namespace = "default"

    names_raw = args.get("names") or []
    if isinstance(names_raw, str):
        names_raw = [n.strip() for n in names_raw.split(",") if n.strip()]
    names = tuple(str(n) for n in names_raw if n)

    labels_raw = args.get("labels") or {}
    if isinstance(labels_raw, str):
        labels_raw = _parse_label_string(labels_raw)
    labels = {str(k): str(v) for k, v in (labels_raw or {}).items()}

    confidence = ConfidenceLevel.HIGH if (names or labels) else ConfidenceLevel.LOW

    return EffectiveTarget(
        scope=scope,
        namespace=namespace,
        names=names,
        labels=labels,
        fault_target=fault_target,
        fault_action=fault_action,
        confidence=confidence,
        raw_command=raw_command,
    )


def _parse_label_string(s: str) -> dict:
    """Parse ``k1=v1,k2=v2`` into a dict. Tolerates whitespace."""
    out: dict = {}
    for pair in s.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k.strip()] = v.strip()
    return out


# ChaosBlade ``--target`` to k8s scope mapping. Used to detect whether
# a blade_create call's k8s effect is on a pod, node, or unknown.
# Pod-attached resources (container, jvm, mysql in pod) all resolve
# to scope=pod. Host-level chaos (cpu/mem/disk/network without k8s
# prefix) resolves to scope=node.
# Migrated from target_guard/classifier.py (phase-7 T5 batch B) so the
# vocabulary lives beside both classifiers that consume it — this one
# and the inline-blade parser below.
BLADE_TARGET_TO_SCOPE: dict[str, str] = {
    "pod": "pod",
    "node": "node",
    "container": "pod",  # container belongs to a pod
    # In-pod middleware / runtime fault targets
    "jvm": "pod",
    "mysql": "pod",
    "redis": "pod",
    "kafka": "pod",
    "rocketmq": "pod",
    "nginx": "pod",
    # Host-level chaos (no k8s prefix, blade run on host)
    "cpu": "node",
    "mem": "node",
    "memory": "node",
    "disk": "node",
    "network": "node",
    "process": "node",
    "file": "node",
    "script": "node",
    "time": "node",
    "kernel": "node",
}


def classify_inline_blade(
    inner: list[str],
    raw_command: str,
    *,
    fallback_ns: str,
    fallback_pod: str,
) -> "EffectiveTarget":
    """Parse ``blade create k8s pod-cpu fullload --names X -n ns ...``.

    Distinct from ``_classify_blade_create`` (which parses dict args
    from the LangChain tool_call). Here we parse the CLI tokens.

    Migrated from ``target_guard/classifier.py`` (phase-7 T5) — pure move,
    behaviour byte-identical. Public name: the kubectl exec classifier
    (providers/k8s_native/classifier.py) reaches this parser for the
    embedded ``kubectl exec POD -- blade create ...`` delivery form through
    the registry's domain-routing seam
    (``FaultProviderRegistry.classify_inline_blade_command``, phase-14 G3
    — no cross-carrier import) — this carrier owns the blade CLI parsing
    vocabulary.
    """
    # Lazy imports — target_guard symbols must not be imported at module
    # level from a provider module (see the NOTE at the top of this file).
    from chaos_agent.agent.target_guard.classifier import (
        canonicalise_kind,
        parse_labels,
        parse_namespace,
    )
    from chaos_agent.agent.target_guard.types import (
        ConfidenceLevel,
        EffectiveTarget,
        SCOPE_READONLY,
    )

    if len(inner) < 2 or inner[0] != "blade" or inner[1] != "create":
        # Non-create blade commands (status/destroy/query/version/prepare/revoke)
        # don't target new k8s resources — guard drift comparison not applicable.
        return EffectiveTarget(
            scope=SCOPE_READONLY,
            namespace="",
            raw_command=raw_command,
            confidence=ConfidenceLevel.HIGH,
        )

    # blade create [k8s] <target>-<sub> <action> [flags]
    rest = inner[2:]
    is_k8s = len(rest) > 0 and rest[0] == "k8s"
    if is_k8s:
        rest = rest[1:]

    # Next token is something like "pod-cpu" / "node-mem" / "pod-network"
    blade_subtype = rest[0] if rest else ""
    rest = rest[1:] if rest else []
    # Split "pod-cpu" → scope_hint="pod", target_hint="cpu"
    scope_hint = ""
    target_hint = blade_subtype
    if "-" in blade_subtype:
        scope_hint, _, target_hint = blade_subtype.partition("-")

    fault_action = rest[0] if rest else ""

    # Parse flags inside the inner cmd
    ns = parse_namespace(rest, default="")
    names = _parse_blade_names(rest)
    labels = parse_labels(rest)
    node_name = _parse_flag_value(rest, "--node")

    # Resolve scope
    if is_k8s and scope_hint:
        scope = canonicalise_kind(scope_hint) or scope_hint
    else:
        scope = BLADE_TARGET_TO_SCOPE.get(target_hint, scope_hint or "pod")

    # Tier 1 detection: outer exec into a tool pod namespace + inner
    # blade k8s command without explicit --namespace. Blade v1.8.0
    # rejects --namespace for some subcommands (e.g. pod-network), so
    # the agent legitimately omits it.
    is_tier1 = (
        is_k8s
        # Class attribute (the ChaosbladeProvider class is defined below
        # this function — resolved at call time). Formerly read the
        # generic-layer TOOL_POD_NAMESPACES constant.
        and fallback_ns in ChaosbladeProvider.tool_pod_namespaces
        and not ns  # no explicit --namespace in inner blade args
    )

    # Cluster-scoped resources don't carry namespace
    if scope == "node":
        effective_ns = ""
    elif ns:
        effective_ns = ns
    elif is_tier1:
        effective_ns = ""
    else:
        effective_ns = "default"

    # Resolve names
    if scope == "node" and node_name:
        effective_names: tuple[str, ...] = (node_name,)
    elif names:
        effective_names = names
    elif labels:
        effective_names = ()
    elif fallback_pod and scope == "pod":
        # Inside ``kubectl exec POD -- blade create k8s pod-cpu ...``
        # if no --names given, it implicitly targets the host pod.
        effective_names = (fallback_pod,)
    else:
        effective_names = ()

    # Host-level blade (no ``k8s`` prefix) inside a kubectl exec carries
    # NO selector: the fault lands on whatever node hosts the exec'd pod.
    # Record the pod so the screener can resolve the node binding
    # DATA-side (pod → nodeName → approved name set). The classifier
    # stays static and never guesses a node name itself.
    exec_pod_name = ""
    exec_pod_namespace = ""
    if (
        not is_k8s
        and scope == "node"
        and fallback_pod
        and not effective_names
        and not labels
    ):
        exec_pod_name = fallback_pod
        exec_pod_namespace = fallback_ns

    return EffectiveTarget(
        scope=scope,
        namespace=effective_ns,
        names=effective_names,
        labels=labels,
        fault_target=target_hint,
        fault_action=fault_action,
        confidence=ConfidenceLevel.HIGH,
        raw_command=raw_command,
        is_tier1_exec=is_tier1,
        exec_pod_name=exec_pod_name,
        exec_pod_namespace=exec_pod_namespace,
    )


def _parse_blade_names(args: list[str]) -> tuple[str, ...]:
    """Parse blade's ``--names X,Y,Z`` into a name tuple."""
    raw = _parse_flag_value(args, "--names")
    if not raw:
        return ()
    return tuple(n.strip() for n in raw.split(",") if n.strip())


def _parse_flag_value(args: list[str], flag: str) -> str:
    """Generic ``--flag value`` / ``--flag=value`` parser."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
        i += 1
    return ""


# ---------------------------------------------------------------------------
# Binary path resolution & create-args construction live in
# ``declaration.py`` (phase-12 spec-import-retirement): the spec layer
# consumes them from the carrier's lightweight declaration surface — this
# heavy implementation module no longer owns them, and no transitional
# re-export remains here (the D4 rewire retired it; consumers import the
# declaration surface directly).
# ---------------------------------------------------------------------------


class ChaosbladeProvider:
    """ChaosBlade backend (k8s + host modes; blade_destroy recovery)."""

    carrier = CARRIER_ID
    injection_methods = ("host_blade", "kubectl_exec")
    has_experiment_uid = True
    # UID-bearing carrier — never the UID-less verdict default.
    uid_less_verdict_default = False
    # phase-14 G7: kind value renamed off the blade-family vocabulary —
    # experiment carriers now identify as "experiment_uid" (the handle
    # carries the experiment UID, which is carrier-neutral wording).
    handle_kind = "experiment_uid"
    is_multi_step = False
    # This backend owns the programmatic Layer-1 recovery (blade destroy +
    # status verification) — see ``layer1_destroy``.
    has_deterministic_recover = True
    # ChaosBlade is detected by experiment-UID scan, not by tool-name / kubectl
    # subcommand, so both injection-vocabulary sets are empty.
    inject_tool_names = frozenset()
    inject_kubectl_subcommands = frozenset()
    # Intent vocabulary this carrier contributes to the FaultFamily aggregate —
    # the OS-subsystem targets / ChaosBlade action verbs. Single source of the
    # per-carrier vocabulary (the family no longer re-declares a flat tuple).
    supported_targets = SUPPORTED_TARGETS
    supported_actions = SUPPORTED_ACTIONS
    # Binaries this backend runs, contributed to the tool guard's Gate-① binary
    # whitelist. ChaosBlade injects and recovers exclusively through ``blade``.
    injection_binaries = frozenset({"blade"})
    # Namespaces hosting ChaosBlade Operator tool pods (operator-mediated
    # delivery: ``kubectl exec <tool-pod> -- blade ...``). Tier-1 exemption:
    # the tool pod's namespace legitimately differs from the target's.
    # Formerly the generic-layer constant TOOL_POD_NAMESPACES (classifier).
    tool_pod_namespaces = frozenset({"chaosblade"})
    # Phase-7 T2 per-tool pass sets (members verified against the tool
    # signatures — every listed name declares the corresponding parameter).
    kubeconfig_scoped_tool_names = frozenset(
        {
            "blade_create",
            "blade_destroy",
            "blade_status",
            "blade_query_k8s",
        }
    )
    audit_scoped_tool_names = frozenset({"blade_create"})
    # One set feeds BOTH L4 log-mirror passes (inject-side in
    # l4/execution.py and recover-side in l4/recovery.py): membership means
    # "runs worth mirroring to the platform log", never phase-scoped — the
    # phase label is written by the consuming pass. Declared as the union of
    # the two historical hardcoded tuples: blade_create (inject's) and
    # blade_destroy (recover's) both ship alongside blade_status.
    log_shipping_tool_names = frozenset(
        {
            "blade_create",
            "blade_destroy",
            "blade_status",
        }
    )

    def matches_channel(self, profile: str) -> bool:
        # ChaosBlade operates on both cluster and bare-host targets.
        return profile in (PROFILE_K8S, PROFILE_HOST)

    def required_params(self, scope: str) -> list[str]:
        from chaos_agent.agent.spec.fault_registry import required_intent_params

        return required_intent_params(scope)

    def tools(self, phase: str) -> list["BaseTool"]:
        """ChaosBlade tools contributed to the factory tool union per phase.

        - PLAN → ``blade_help`` / ``blade_status`` only. Both are read-only
          (help text / list experiments + confirm blade is installed).
          ``blade_create`` is intentionally ABSENT from planning: ChaosBlade
          has no dry-run mode, so binding it here would hand the planner a
          direct path past the confirmation gate. ``blade_destroy`` is likewise
          hidden from Phase 1 (it mutates cluster state).
        - EXECUTE → the full injection surface. ``blade_destroy`` is available
          for ReAct cleanup of a partial/failed create; the target guard
          validates UID provenance before ToolNode can execute it.
        - VERIFY / RECOVER_VERIFY → nothing (ChaosBlade verification is
          deterministic via Layer 1, not LLM tools).
        """
        from chaos_agent.agent.providers.base import EXECUTE, PLAN
        from chaos_agent.agent.providers.chaosblade.cli import (
            blade_create,
            blade_destroy,
            blade_help,
            blade_query_k8s,
            blade_status,
        )

        if phase == PLAN:
            return [blade_help, blade_status]
        if phase == EXECUTE:
            return [
                blade_create,
                blade_destroy,
                blade_help,
                blade_status,
                blade_query_k8s,
            ]
        return []

    def detect(self, messages: list, *, is_host: bool) -> Optional[str]:
        """Reverse-scan for a live (NON-destroyed) ChaosBlade injection.

        Attributes the task to ChaosBlade only when the most recent parseable
        blade UID has NOT been ``blade_destroy``'d. A failed ``blade_create``
        that was subsequently cleaned up leaves a residual ``UID:`` in history;
        excluding destroyed UIDs (parity with the execute node's
        ``_extract_blade_uid_from_messages``) stops it re-claiming an experiment
        that no longer exists (task-76c59364). Recency vs a later native
        injection is arbitrated by the registry using :meth:`injection_recency`.
        """
        from .verify import (
            scan_blade_evidence_index,
            scan_destroyed_uids,
        )

        _idx, method = scan_blade_evidence_index(
            messages,
            destroyed=scan_destroyed_uids(messages),
        )
        return method

    def injection_recency(self, messages: list, *, is_host: bool) -> int:
        """Message index of the live blade evidence, or ``-1``. See registry."""
        from .verify import (
            scan_blade_evidence_index,
            scan_destroyed_uids,
        )

        idx, _method = scan_blade_evidence_index(
            messages,
            destroyed=scan_destroyed_uids(messages),
        )
        return idx

    def build_fault_handle(self, values: dict) -> Optional[dict]:
        """Claim a committed ChaosBlade experiment: the legacy ``experiment_uid``
        field is this backend's attribution fact. The method is echoed for
        recover-side consumers but the UID alone makes the handle valid."""
        values = values or {}
        uid = values.get("experiment_uid") or ""
        if not uid:
            return None
        return {
            "kind": "experiment_uid",
            "value": uid,
            "method": values.get("injection_method") or "",
        }

    def extract_experiment_id(self, messages: list, retired=None) -> str:
        """Live (non-destroyed, non-retired) blade UID in ``messages``."""
        from .verify import (
            extract_experiment_uid_from_messages,
        )

        return extract_experiment_uid_from_messages(messages, retired=retired)

    def destroyed_experiment_ids(self, messages: list) -> set[str]:
        """UIDs this carrier's ``blade_destroy`` tool calls have targeted —
        the destroy half of the experiment lifecycle scan, union-aggregated
        by the registry's ``destroyed_experiment_ids`` seam (channel-
        unfiltered: a destroy is a terminal-state fact regardless of which
        channel issued it)."""
        from .verify import scan_destroyed_uids

        return scan_destroyed_uids(messages)

    def extract_experiment_id_from_session_dict(self, session_messages: list) -> str:
        """Session-dict fallback: recover a UID from the RAW task-file
        message dicts when the best-effort langchain conversion cannot
        carry the payload (``tool_execution`` dicts hold nested ``detail``
        blocks the ToolMessage shape cannot represent).

        Phase-13 D2 hook, consumed by the registry's
        ``recover_experiment_uid_from_session`` dict layer: the
        ``detail.command`` blade/create vocabulary judgement and the
        stdout/stderr/content text-union extraction moved here WHOLESALE
        from task_snapshot's fallback loop — the vocabulary belongs to the
        carrier side, not the generic layer. Newest-first scan, first
        create-shaped ``tool_execution`` wins."""
        from .verify import extract_experiment_uid

        if not isinstance(session_messages, list):
            return ""
        for msg in reversed(session_messages):
            if not isinstance(msg, dict):
                continue
            detail = msg.get("detail") if isinstance(msg.get("detail"), dict) else {}
            command = detail.get("command") if isinstance(detail.get("command"), str) else ""
            if "blade" not in command or "create" not in command:
                continue
            chunks = [
                detail.get("stdout_preview"),
                detail.get("stderr"),
                msg.get("content"),
            ]
            text = "\n".join(c for c in chunks if isinstance(c, str) and c)
            uid = extract_experiment_uid(text)
            if uid:
                return uid
        return ""

    def build_handle_from_messages(
        self, messages: list, retired=None, values: Optional[dict] = None
    ) -> Optional[dict]:
        """Defense-in-depth hydration: claim a live blade UID found in the
        message history (see the Protocol hook — only consulted when the
        durable facts are absent)."""
        uid = self.extract_experiment_id(messages, retired)
        if not uid:
            return None
        return {
            "kind": self.handle_kind,
            "value": uid,
            "method": (values or {}).get("injection_method") or "",
        }

    def created_experiment_ids(self, messages: list, state: dict) -> set[str]:
        """Provenance for the destroy gate: every UID this task's
        ``blade_create`` results ever proved, PLUS the durable state record.

        Two evidence sources, unioned:

        1. ``blade_create`` ToolMessages in the visible history — the primary
           record, covering every UID the task ever created (including
           failed-create CRDs that still need cleanup).
        2. ``state["experiment_uid"]`` when the attribution is this carrier's —
           the framework's durable record of the live experiment. Source 1 is
           not durable: compression removes old ToolMessages BY DESIGN, which
           would empty the whitelist mid-task and leave the agent unable to
           destroy its own injection. The state field is maintained by the
           execution loop (and preserved by the compressed-history restore
           path), so it keeps proving provenance across compaction. A
           ``python_agent`` attribution owns the same legacy field via its own
           provider, so it is not claimed here (mirrors
           :meth:`build_fault_handle`).
        """
        from langchain_core.messages import ToolMessage

        from .verify import extract_experiment_uid

        state = state or {}
        uids: set[str] = set()
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            if (getattr(message, "name", "") or "") != "blade_create":
                continue
            content = message.content if isinstance(message.content, str) else ""
            uid = extract_experiment_uid(content)
            if uid:
                uids.add(uid)
            # Terminal create failures deliberately do not count as active UIDs
            # in extract_experiment_uid, but their CRDs still need cleanup.
            uids.update(
                match.group(1) for match in _FAILED_CREATE_UID_RE.finditer(content)
            )
        if state.get("injection_method") != "python_agent":
            durable_uid = str(state.get("experiment_uid") or "").strip()
            if durable_uid:
                uids.add(durable_uid)
        return uids

    def classify_tool_target(
        self, tool_name: str, tool_args: Any, raw_command: str
    ) -> Optional[EffectiveTarget]:
        """Guard-side classification of this carrier's tools (phase-7 T5).

        Claims the ``blade_create`` injection tool (dict-arg classifier
        above) and this carrier's read-only tools (``blade_help`` /
        ``blade_status`` / ``blade_query_k8s`` — none of them mutates a
        fault target; ``blade_destroy`` is recovery-phase and stays
        unclassified so the phase screeners own it). ``None`` (not this
        carrier's tool) lets the registry scan continue — the kubectl
        tools are claimed by the k8s-native provider, whose classifier
        also delegates the embedded ``kubectl exec ... blade create``
        form back to this carrier's inline-blade parser."""
        # Lazy import — see the NOTE at the top of this file.
        from chaos_agent.agent.target_guard.types import (
            SCOPE_READONLY,
            EffectiveTarget,
        )

        if tool_name == "blade_create":
            return _classify_blade_create(
                coerce_tool_args_dict(tool_args),
                raw_command,
            )
        if tool_name in ("blade_help", "blade_status", "blade_query_k8s"):
            return EffectiveTarget(
                scope=SCOPE_READONLY,
                namespace="",
                raw_command=raw_command,
            )
        return None

    def parse_injection_params(self, tool_name: str, tool_args: dict) -> Optional[dict]:
        """Issue-time key-parameter extraction for this carrier's two
        tool-call forms: the direct ``blade_create`` call and the fallback
        ``kubectl exec ... blade create`` embedded delivery. Migrated from
        the execute loop's hardcoded branches (phase-7 T3)."""
        if tool_name == "blade_create":
            parsed = parse_blade_flags(tool_args.get("flags", ""))
            if parsed:
                logger.info("Blade create params parsed: %s", parsed)
            return parsed or None
        if tool_name == "kubectl" and tool_args.get("subcommand") == "exec":
            v_args = tool_args.get("v_args", "") or ""
            if "blade" in v_args and "create" in v_args:
                embedded = _parse_blade_create_from_v_args(v_args)
                if embedded:
                    logger.info(
                        "Kubectl exec blade params: scope=%s, target=%s, action=%s",
                        embedded["scope"],
                        embedded["target"],
                        embedded["action"],
                    )
                    return parse_blade_flags(embedded.get("flags", "")) or None
        return None

    def issue_time_method(
        self, tool_name: str, tool_args: dict, *, is_host: bool = False
    ) -> Optional[str]:
        """Issue-time attribution for this carrier's two tool-call forms: the
        direct ``blade_create`` call enacts ``host_blade``, and the fallback
        ``kubectl exec/debug ... blade create`` embedded delivery enacts
        ``kubectl_exec`` (the command-mode subcommand vocabulary lives in the
        kubectl tool domain — ``providers.message_scanning``, phase-14 G2 —
        since the embedded form rides that tool). Registered before
        k8s-native, so the embedded ChaosBlade delivery is claimed here
        rather than mis-attributed as a mutating exec. Migrated from the
        execute-side classifier's hardcoded branches (phase-7 T4)."""
        if tool_name == "blade_create":
            return "host_blade"
        if tool_name == "kubectl":
            subcommand = tool_args.get("subcommand", "")
            v_args = tool_args.get("v_args", "") or ""
            if (
                subcommand in KUBECTL_COMMAND_SUBCOMMANDS
                and isinstance(v_args, str)
                and "blade" in v_args
                and "create" in v_args
            ):
                return "kubectl_exec"
        return None

    def issue_disproven(self, messages: list) -> bool:
        """Experiment attribution is RESULT-born (committed only when the UID
        appears in a successful create result), so there is no issue-time
        guesswork to revoke."""
        return False

    async def rollback_handle(self, handle: dict, **kwargs) -> str:
        """Deterministic undo of a committed blade experiment (failure-path
        auto-rollback). ``blade_destroy`` routes host/k8s delivery itself."""
        uid = (handle or {}).get("value") or ""
        if not uid:
            return ""
        from chaos_agent.agent.providers.chaosblade.cli import blade_destroy

        try:
            destroy_result = await blade_destroy.ainvoke(
                {"uid": uid, "kubeconfig": kwargs.get("kubeconfig", "")}
            )
            logger.info("Auto-rollback result: %s", destroy_result)
            return f" (auto-rolled back experiment_uid={uid})"
        except Exception as rb_err:  # noqa: BLE001 — best-effort rollback
            return f" (rollback FAILED: {rb_err})"

    def scan_step_actions(self, steps: list[str], messages: list):
        """Explicitly not claimed (pinned None, phase-8 D4): an
        experiment-UID carrier judges injection completion by the
        experiment evidence chain (the UID), not step-verb heuristics —
        the step self-check is native-carrier territory."""
        return None

    def was_injection_attempted(self, messages: list) -> bool:
        """Explicitly not claimed (pinned False): the native-fallback
        message back-scan is native-carrier territory; THIS backend's
        attempt state is carried by :meth:`was_fault_create_attempted`
        (the experiment judgement)."""
        return False

    def was_fault_create_attempted(
        self,
        messages: list,
        injection_method: str | None = None,
    ) -> bool:
        """Attempted-but-no-UID judgement for this experiment-recording
        carrier — durable-attribution exemption first, then the kubectl
        success / kubectl-native fallback exemptions, then the
        ``blade_create`` tool-name scan. Delegates to the carrier's
        verify-side domain (:mod:`_chaosblade_verify`)."""
        from .verify import (
            was_blade_create_attempted,
        )

        return was_blade_create_attempted(messages, injection_method)

    async def layer1_verify(self, state: dict, **kwargs) -> "Layer1Result":
        """ChaosBlade Layer-1 verification.

        Dispatches between the two delivery variants this backend owns:
        ``kubectl_exec`` uses ``kubectl exec`` into a tool pod (host blade may be
        incompatible); ``host_blade`` (and the UID-only fallback) polls
        ``blade_status`` / ``blade_query_k8s`` locally.
        """
        from .verify import (
            _run_host_blade_layer1,
            _run_layer1_via_kubectl_exec,
        )

        # Identity comes from the caller-resolved dispatch identity
        # (``_verifier_layer1`` passes ``experiment_uid`` explicitly; the
        # legacy ``kwargs['blade_uid']`` alias fallback was retired in
        # phase-14 — same EOL ruling as the recover seam: no pre-handle
        # callers exist).
        experiment_uid = kwargs.get("experiment_uid", "") or ""
        kubeconfig = kwargs.get("kubeconfig", "") or ""
        task_id = kwargs.get("task_id", "")

        if state.get("injection_method") == "kubectl_exec":
            return await _run_layer1_via_kubectl_exec(
                experiment_uid,
                kubeconfig,
                task_id=task_id,
                injection_pod_name=(self.recovery_vehicle(state) or None),
            )
        return await _run_host_blade_layer1(
            experiment_uid,
            kubeconfig,
            task_id=task_id,
            messages=state.get("messages", []),
            injection_method=state.get("injection_method"),
        )

    async def layer1_destroy(
        self,
        uid: str,
        kubeconfig: str = "",
        *,
        messages: list | None = None,
        injection_method: str | None = None,
    ) -> "Layer1Result":
        """Deterministic Layer-1 recovery: ``blade_destroy`` + ``blade_status``
        destroyed-state verification (delegates to ``_chaosblade_recover`` —
        the execution domain this provider owns)."""
        from .recover import run_layer1_destroy

        return await run_layer1_destroy(
            uid,
            kubeconfig,
            messages=messages,
            injection_method=injection_method,
        )

    async def layer1_raw_destroy(self, uid: str, kubeconfig: str = "") -> str:
        """Bare destroy for the finalize retry (delegates to ``_chaosblade_recover``
        — no ``blade_status`` verification; the retry prompt only needs the
        destroy output)."""
        from .recover import raw_destroy

        return await raw_destroy(uid, kubeconfig)

    def extract_kubectl_exec_pod_name(self, messages: list) -> str | None:
        """Tool pod the kubectl-exec delivery ran its successful ``blade
        create`` in (message-history extraction) — the write side of the
        ``recovery_vehicle`` record below. Exposed as an instance method so
        the registry can dispatch the extraction
        (:meth:`FaultProviderRegistry.extract_kubectl_exec_pod_name`,
        phase-8 T4) without the generic execute loop importing this module;
        the module-level function remains for in-package / tests callers."""
        return extract_kubectl_exec_pod_name(messages)

    def recovery_vehicle(self, state: dict) -> str:
        """Tool pod the kubectl-exec delivery ran in (durable state record) —
        recovery must reuse the same in-cluster channel; ``""`` for the
        host-blade delivery (no vehicle)."""
        return str(state.get("kubectl_exec_pod_name") or "")

    def blocks_deterministic_destroy(
        self, state: dict, messages: list | None = None
    ) -> bool:
        """kubectl-exec delivery: the host ``blade destroy`` cannot reach a
        CRD-created experiment, so the LLM-driven Layer 1 flow is the only
        recover vehicle. Decided by :func:`was_kubectl_exec_delivery`, which
        unions the DURABLE ``state["injection_method"]`` record with the
        message scan — recovery runs late in the task, when compaction may
        already have removed the injection evidence the raw scan needs."""
        from .verify import (
            was_kubectl_exec_delivery,
        )

        return was_kubectl_exec_delivery(state, messages)

    def recovery_facts_render(
        self, state: dict, *, spec_params: dict | None = None
    ) -> str:
        """Experiment UID + carrier-parsed parameters for the Layer-1
        recovery context. The parsed-parameter line is skipped when the
        neutral fault spec already carries the parameters (former elif)."""
        lines = []
        uid = state.get("experiment_uid") or ""
        if uid:
            lines.append(f"Blade UID: {uid}\n")
        if not spec_params:
            parsed = state.get("injection_parsed_params") or {}
            if parsed:
                lines.append(f"Injection key parameters: {parsed}\n")
        return "".join(lines)

    def merge_deterministic_recover_verdict(
        self, layer1, state: dict, part_override: dict | None = None
    ):
        """Composite Layer-1 verdict for combo (experiment + kubectl-native)
        recovery: the experiment was destroyed deterministically BEFORE the
        LLM flow; the LLM verdict covers only the native component. Either
        part failing fails the composite — a partially undone fault is still
        active.

        ``part_override`` carries the verdict computed in the SAME node
        invocation (iteration 1): it lives only in the pending result_update
        there, not yet in ``state``."""
        from chaos_agent.agent.result.verdict import (
            Layer1Result as RecoverLayer1Result,
        )

        blade_part = part_override or state.get("combo_blade_part") or {}
        if not blade_part:
            return layer1
        # model_dump keeps enum objects — normalize to the plain string value.
        _bp_raw = blade_part.get("status", "")
        bp_status = str(getattr(_bp_raw, "value", _bp_raw) or "")
        if bp_status == "passed":
            layer1.details = (
                f"[blade experiment destroyed deterministically] {layer1.details}"
            )
            return layer1
        bp_details = str(blade_part.get("details", "") or "")
        _native_status = getattr(layer1.status, "value", layer1.status)
        return RecoverLayer1Result(
            status="failed",
            details=(
                f"Combo recovery failed: blade experiment destroy {bp_status}"
                + (f" ({bp_details[:200]})" if bp_details else "")
                + f"; native part: {_native_status} ({(layer1.details or '')[:200]})"
            ),
            raw_output=layer1.raw_output,
        )

    def layer1_recover_guidance(
        self,
        state: dict,
        experiment_uid: str,
        *,
        combo_native: bool = False,
        combo_part: dict | None = None,
    ) -> str:
        """LLM Layer-1 guidance for the deliveries this backend cannot (or
        already did) destroy programmatically: the in-cluster exec delivery
        (destroy must run inside the tool pod) and the combo case (experiment
        part already destroyed — only the native undo remains)."""
        if not experiment_uid:
            return ""
        if self.blocks_deterministic_destroy(state):
            original_pod = self.recovery_vehicle(state)
            pod_hint = ""
            if original_pod:
                pod_hint = (
                    f"Original injection Pod: `{original_pod}` — prefer this Pod to run the destroy action "
                    f"(its namespace is deployment-specific — locate it across all namespaces if needed).\n"
                    f"If that Pod no longer exists, discover a currently running tool pod across all "
                    f"namespaces by its tool label.\n"
                )
            logger.info(
                f"in-cluster exec delivery detected for uid={experiment_uid}, "
                f"routing to LLM-driven Layer 1 recovery flow"
            )
            return (
                f"\n\n## Experiment Recovery (in-cluster injection channel)\n"
                f"The fault was injected from inside the cluster (the injection tool ran within a tool pod).\n"
                f"Experiment UID: `{experiment_uid}`\n"
                f"{pod_hint}"
                f"To recover, you MUST destroy the experiment through the same in-cluster channel:\n"
                f"run the experiment-destroy command for UID `{experiment_uid}` inside a running tool pod, "
                f"in that pod's own namespace.\n"
                f"The tool pod namespace is deployment-specific — do NOT assume it; use the "
                f"namespace you discover.\n"
            )
        if combo_native:
            # Combo: blade part already handled deterministically — tell the
            # LLM the experiment is dealt with and its only job is the native
            # undo (it must not re-destroy anything).
            _bp_raw_status = (combo_part or {}).get("status", "unknown")
            _bp_status = str(
                getattr(_bp_raw_status, "value", _bp_raw_status) or "unknown"
            )
            _bp_details = (combo_part or {}).get("details", "") or ""
            return (
                f"\n\n## Experiment Recovery (blade component — handled by the framework)\n"
                f"The blade experiment (UID `{experiment_uid}`) was destroyed deterministically by "
                f"the framework BEFORE this phase — destroy status: {_bp_status}.\n"
                + (
                    f"WARNING: the deterministic blade destroy FAILED ({_bp_details[:300]}) — "
                    f"report this in your Details; the experiment component may still be active.\n"
                    if _bp_status != "passed"
                    else ""
                )
                + "Do NOT attempt to destroy any experiment yourself.\n"
                "Your ONLY remaining job: undo the kubectl-native injection component — "
                "reverse the native mutations recorded in the injection context (e.g. revert "
                "patches/labels, kill injected processes, remove tc/iptables rules).\n"
            )
        return ""

    def layer2_facts_note(self, state: dict) -> str:
        """Parsed operation parameters plus the auto-expiry note derived from
        the experiment's ``--timeout`` flag (a short-timeout experiment may
        have self-destructed before manual recovery — an unexplained clean
        verification is then expected)."""
        parsed = state.get("injection_parsed_params") or {}
        if not parsed:
            return ""
        note = f"Injection key parameters: {parsed}\n"
        timeout_val = parsed.get("timeout")
        if timeout_val:
            try:
                timeout_sec = int(str(timeout_val).strip())
                if timeout_sec < 600:
                    note += (
                        f"ℹ Duration note: Original --timeout was {timeout_sec}s. "
                        f"The fault may have already auto-expired before manual recovery. "
                        f"If recovery verification shows no residual fault effects, this is expected.\n"
                    )
            except (ValueError, TypeError):
                pass
        return note

    def verify_prompt_note(
        self, injection_method: str, *, injection_pod_name: str | None = None
    ) -> str:
        """Post-injection verifier note for the ``kubectl_exec`` delivery.

        The ``host_blade`` delivery needs no special note (standard kubectl
        verification), so it returns ``""`` and the verifier falls back to its
        default minimal-container note."""
        if injection_method != "kubectl_exec":
            return ""

        note = (
            "\n### Injection Method Note\n"
            "The fault was injected via `kubectl exec` (the standard `blade_create` tool "
            "was unavailable). This means the injection method may differ from the skill "
            "case's recommended approach. You MUST:\n"
            "1. Check whether the ACTUAL injection method produces the same fault effects "
            "described in the skill case's verification steps\n"
            "2. If the expected fault effect differs, note this as a WARNING "
            "and adapt your verification accordingly\n\n"
            "### BusyBox Quick Reference (commands via kubectl exec run in a BusyBox container)\n"
            "- iostat: NO -x flag. Use `iostat -d -k 1 3` (device stats) + `iostat -c 1 3` (CPU/iowait)\n"
            "  NOTE: cumulative counters may overflow (values near 9e18); use interval deltas, NOT absolute values\n"
            "- ps: NO -w flag. Use `ps` (bare) or `ps -o pid,args`, NOT `ps -w` or `ps -o PID,USER,TIME,COMMAND`\n"
            "- grep: NO -E flag (no extended regex). Use `grep -e pattern1 -e pattern2` or basic regex\n"
            "- mount: output differs; use `cat /proc/mounts` as alternative\n"
            "- top: may not exist. Use `top -bn1` (batch mode) or `cat /proc/stat`\n"
            "- df: `df -h` works normally on BusyBox\n"
            "- find: limited but functional. Avoid complex predicates\n"
            "- awk/sed: BusyBox versions have fewer features; prefer simple grep/cut\n"
        )
        if injection_pod_name:
            note += (
                f"\nTool pod `{injection_pod_name}` is available (its namespace is "
                f"deployment-specific — locate it across all namespaces if needed):\n"
                f"  - Injection-tool commands (status, destroy)\n"
                f"  - Cluster API checks (describe node, top node, get events)\n"
                f"LIMITATION: This pod does NOT mount /host. `df -h` inside it shows "
                f"the overlay filesystem, not the host disk.\n"
            )
        note += (
            "\n### BusyBox Compatibility (MANDATORY)\n"
            "You are running verification commands inside a BusyBox container (executed in a tool pod). "
            "Common Linux flags/commands may NOT be available — check the BusyBox Quick Reference above BEFORE "
            'issuing any command. Do NOT guess flags. If a command returns "unrecognized option" or '
            '"bad usage", do NOT retry similar commands — switch to the BusyBox alternative immediately.\n'
            "If kubectl exec commands consistently fail, fall back to `kubectl describe` for Pod-level "
            "metrics (restart count, conditions, events) as an alternative.\n"
        )
        return note

    def recover_layer2_context(
        self,
        state: dict,
        layer1,
        *,
        is_deterministic: bool,
        experiment_uid: str,
        is_host_scope: bool,
    ) -> tuple[str, str]:
        """Recover Layer-2 framing for the ChaosBlade backend.

        Three shapes: kubectl-exec experiment with no deterministic Layer 1
        (``skipped``), LLM-driven recovery execution (kubectl-exec), and the
        deterministic ``blade_destroy`` case."""
        if layer1.status == "skipped":
            layer1_context = (
                "## Layer 1 Result\n"
                "Layer 1 skipped: ChaosBlade experiment was created via kubectl exec and "
                "recovery is being handled through the LLM-driven recovery flow.\n\n"
            )
            layer2_instruction = (
                "This fault was injected through the in-cluster tool-pod channel. "
                "Verify the fault effect has been removed using the bound cluster query tools.\n"
            )
            return layer1_context, layer2_instruction

        if not is_deterministic:
            layer1_context = (
                f"## Layer 1 Result (Recovery Execution)\n"
                f"This fault was injected through the in-cluster tool-pod channel. "
                f"Recovery actions executed: {layer1.status}\n"
                f"Details: {layer1.details}\n\n"
            )
            layer2_instruction = (
                "PHASE TRANSITION: Layer 1 (recovery execution) is COMPLETE. "
                "You are now in Layer 2 (VERIFICATION). "
                "DO NOT execute more recovery actions — only VERIFY the fault effect is removed. "
                "Use the bound cluster query tools only to CHECK status, not to modify resources. "
                "Output RECOVERY_VERIFICATION_RESULT format, NOT RECOVERY_EXECUTION_RESULT.\n"
            )
            return layer1_context, layer2_instruction

        layer1_context = (
            f"## Layer 1 Result (already completed)\n"
            f"Deterministic recovery for UID {experiment_uid}: {layer1.status}\n"
            f"Details: {layer1.details}\n"
            f"Raw output: {layer1.raw_output[:500]}\n\n"
        )
        layer2_instruction = (
            "PHASE TRANSITION: Layer 1 PASSED (the recovery action reported success and the "
            "experiment status check confirms Destroyed). "
            "You are now in Layer 2 (VERIFICATION). "
            "Verify the fault effect has ACTUALLY been removed from the target's runtime state. "
            + (
                "Use the host diagnostic tool to check the host's runtime state directly. "
                if is_host_scope
                else "Use the bound cluster query tools to check the target resource. "
            )
            + "Output RECOVERY_VERIFICATION_RESULT format, NOT RECOVERY_EXECUTION_RESULT.\n"
        )
        return layer1_context, layer2_instruction

    async def recover(
        self, state: dict, handle: Optional[dict], **kwargs
    ) -> RecoverResult:
        """Deterministic ChaosBlade recovery verdict (no LLM).

        Two sub-variants keyed on whether the experiment was created via
        ``kubectl exec`` — decided by the ``blocks_deterministic_destroy``
        hook (which unions the DURABLE ``state["injection_method"]`` record
        with the message scan: recovery runs late in the task, when compaction
        may already have removed the injection evidence the raw scan needs).

        - ``kubectl_exec`` delivery — a host ``blade destroy`` cannot reach a
          CRD-created experiment; without an LLM to run ``kubectl exec`` we
          cannot destroy it. Report ``skipped``/unrecovered with the guidance
          warning.
        - local ``blade_destroy`` — run the canonical Layer-1 recovery
          (blade_destroy + blade_status) and map its verdict.
        """
        from .recover import run_layer1_destroy
        from chaos_agent.agent.result.verdict import (
            FailureCategory,
            Layer1Result,
            layer1_to_dict,
        )

        # Identity from the recovery handle (value = experiment UID) — the
        # handle is the single identity source since phase-6 (the legacy
        # ``kwargs['blade_uid']`` fallback for pre-handle callers was
        # retired; old checkpoints are no longer supported).
        experiment_uid = str((handle or {}).get("value") or "")
        kubeconfig = kwargs.get("kubeconfig", "") or ""
        messages = kwargs.get("messages", []) or []
        is_kubectl_exec = self.blocks_deterministic_destroy(state, messages)
        # Combo flag read before the kubectl_exec early-return so both
        # branches can surface the native-component leak warning.
        _combo = bool(state.get("combo_native_issued"))

        layer2 = {
            "status": "skipped",
            "details": "No LLM available for specific verification",
        }

        if is_kubectl_exec:
            layer1 = Layer1Result(
                status="skipped",
                details=f"ChaosBlade experiment (uid={experiment_uid}) was created via kubectl exec, "
                f"host blade_destroy cannot destroy it — LLM-based recovery required",
            )
            warnings = (
                f"ChaosBlade experiment (uid={experiment_uid}) created via kubectl exec cannot be "
                f"destroyed from host (blade_destroy). Use LLM-based recovery "
                f"(blade-ai recover with LLM) to destroy via kubectl exec: "
                f"kubectl exec <tool-pod> -n <tool-pod-namespace> -- blade destroy {experiment_uid} "
                "(discover the tool pod across all namespaces first: "
                "kubectl get pods -A -l app=otel-c-tool)"
                + (
                    ". Combo injection: a kubectl-native component was injected "
                    "alongside the experiment — after destroying the experiment, "
                    "the native mutation must ALSO be undone (revert the original "
                    "kubectl mutation)."
                    if _combo
                    else ""
                ),
            )
            return RecoverResult(
                recovered=False,
                level="unrecovered",
                layer1=layer1_to_dict(layer1),
                layer2=layer2,
                warnings=warnings,
                experiment_uid=experiment_uid,
                handle=handle,
                failure=(
                    FailureCategory.RECOVERY_FAILED,
                    f"Layer1={layer1.status}, Layer2=skipped, details={layer1.details[:200]}",
                ),
            )

        layer1 = await run_layer1_destroy(
            experiment_uid,
            kubeconfig,
            messages=messages,
            injection_method=state.get("injection_method"),
        )
        # COMBO injection: a kubectl-native component was injected alongside
        # the blade experiment. The no-LLM path can ONLY destroy the blade
        # experiment — the native component cannot be undone here, so even a
        # successful blade destroy leaves the fault partially active.
        recovered = layer1.is_passed() and not _combo
        _warnings: list[str] = []
        if layer1.is_passed() and not _combo:
            _warnings.append(
                "Layer 2 (fault-specific) recovery verification was skipped. "
                "Only blade_destroy + blade_status verification was performed."
            )
        if _combo:
            _warnings.append(
                f"Combo injection: besides the blade experiment (uid={experiment_uid}), a "
                f"kubectl-native component was injected. The deterministic (no-LLM) "
                f"recovery path can ONLY destroy the blade experiment — the native "
                f"component was NOT undone. Use LLM-based recovery (blade-ai recover "
                f"with LLM) to reverse the native mutations."
            )
        warnings = tuple(_warnings)
        return RecoverResult(
            recovered=recovered,
            level="recovered" if recovered else "unrecovered",
            layer1=layer1_to_dict(layer1),
            layer2=layer2,
            warnings=warnings,
            experiment_uid=experiment_uid,
            handle=handle,
            failure=None
            if recovered
            else (
                FailureCategory.RECOVERY_FAILED,
                f"Layer1={layer1.status}, Layer2=skipped, details={layer1.details[:200]}",
            ),
        )

    def prompt_fragments(self) -> ProviderPrompts:
        return ProviderPrompts()


__all__ = ["ChaosbladeProvider"]
