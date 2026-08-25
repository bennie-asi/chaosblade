"""Chaosblade carrier's verify-side domain: creation/delivery judgements,
experiment-UID extraction, and the Layer-1 execution domain.

Physically owned by the provider layer (phase-4 T2/T4): these are the blade
carrier's own combination judgements over the carrier-agnostic scan
primitives (``providers/message_scanning.py`` since phase-14 G1) — "was a
blade experiment create attempted but never landed", "was the live experiment
delivered through kubectl exec", and the experiment-UID extraction from the
blade-family tool evidence (the extraction family and blade-evidence scans
moved here from the retired ``chaosblade/detection.py`` in phase-14 G1 — they
are this carrier's own output-format knowledge, not shared primitives).
Since phase-4
T4 the Layer-1 EXECUTION domain (blade_status / blade_query_k8s parsing, the
kubectl-exec and host-blade runners) also lives here — the two providers whose
``layer1_verify`` dispatches into it import it as a same-package dependency
(no providers→nodes sideways coupling). ``nodes/verify/_verifier_layer1.py``
keeps the STATE orchestration; the transitional aliases it used to re-export
were retired with phase-5.

Symbols:
  Functions: was_blade_create_attempted, was_kubectl_exec_delivery,
             extract_experiment_uid_from_messages
  Experiment-UID extraction & blade-evidence scans (moved from the retired
  chaosblade/detection.py, phase-14 G1):
    Functions: extract_experiment_uid, scan_destroyed_uids,
               scan_blade_evidence_index, scan_kubectl_blade_success
  Layer-1 execution domain (moved from nodes/verify/_verifier_layer1.py):
    Constants: _MAX_DISCOVERY_PROBES, _EXPIRED_STATES, _RUNNING_STATES,
               _TRANSIENT_STATES, _FAILURE_SIGNALS, _TOOL_POD_NAMESPACE
    Type:      _QueryK8sResult
    Parsers:   _extract_json_object, _parse_iso_ts_seconds, _is_early_destroy,
               _parse_blade_status_output, _parse_blade_query_k8s_output,
               _find_blade_query_in_messages, _map_query_k8s_to_layer1
    Runners:   _run_layer1_via_kubectl_exec, _run_host_blade_layer1
"""

import json
import logging
import re
from collections import namedtuple

from langchain_core.messages import ToolMessage

from chaos_agent.agent.providers.message_scanning import (
    KUBECTL_COMMAND_SUBCOMMANDS,
    KUBECTL_WRITE_SUBCOMMANDS,
    build_tool_call_args_lookup,
    exec_inner_command_mutates,
    scan_kubectl_injection_after_blade,
)
from chaos_agent.agent.result.verdict import Layer1Result
from chaos_agent.observability.status_tracker import get_tracker
from chaos_agent.tools.pod_discovery import (
    TOOL_POD_NAMESPACE as _TOOL_POD_NAMESPACE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Multi-strategy experiment-UID extraction (phase-9 T3.1)
#
# Moved from ``utils/blade_uid.py`` so the carrier's output-format knowledge
# lives in the providers package — ``chaosblade.py`` / ``chaosblade_python.py``
# / ``_chaosblade_verify.py`` consume it as a family. Line-for-line
# equivalent to the original (renamed ``extract_blade_uid`` →
# ``extract_experiment_uid``). Phase-14 G1: physically moved from the retired
# ``chaosblade/detection.py`` into THIS module — the extraction family is the
# blade domain's own output-format knowledge and stays inside it.
#
# Real-world `blade create` output appears in many shapes — clean JSON, JSON
# buried in a kubectl-exec stderr preamble, pretty-printed multi-line JSON,
# mixed code 200 / 54000 responses, and occasionally raw `chaosblade-*`
# resource names. A single regex or `json.loads` is brittle against this.
# The semantically-aware strategy runs first so that a code-54000 response
# with `success=false` is correctly rejected (the CRD exists but the
# experiment failed — extracting its UID would mislead the verifier).
#
# Strategy order:
#   1. JSON-aware (``json.JSONDecoder.raw_decode``):
#      - Walks every `{` in `text`, parses JSON segments, applies the
#        blade_create response semantics:
#          * code=200 + success=true        → return result
#          * code=54000 + success!=False    → return result.uid
#          * code=54000 + success=False     → reject AND block fallbacks
#            (the injection failed — extracting the UID would be misleading).
#   2. Loose regex on `"result"` / `"uid"` UUID-shaped fields — catches
#      malformed JSON that the parser bailed on (e.g., truncated stdout,
#      unescaped quotes from kubectl-exec wrapping).
#   3. ChaosBlade resource pattern `chaosblade-[a-f0-9]+` — last resort
#      for cases where blade emitted a resource name instead of a UID
#      (e.g. `kubectl get chaosblades` echo).
# ---------------------------------------------------------------------------

# Standard UUID shape (8-4-4-4-12 hex) embedded in a JSON-style key.
_UUID_RE = re.compile(
    r'"(?:result|uid)"\s*:\s*"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})"'
)

# ChaosBlade resource-name fallback (used when blade emits a resource ref
# rather than a UID — e.g. `chaosblade-1234abcd...`).
_CHAOSBLADE_RESOURCE_RE = re.compile(r'\b(chaosblade-[a-f0-9]{8,})\b')

# Sentinel returned by strategy 1 to mean "saw a 54000+success=false
# response — do NOT fall back to looser strategies." Any non-None, non-str
# object works; an object literal makes identity checks unambiguous.
_FAILED_54000_SENTINEL = object()


def extract_experiment_uid(text: str) -> str | None:
    """Extract a ChaosBlade experiment UID from arbitrary tool output.

    Returns the UID string on success, or None if no usable UID was found
    (including the case where a 54000 response indicates the injection
    actually failed — callers must treat None as "no live experiment").
    """
    if not isinstance(text, str) or not text:
        return None

    uid = _uid_strategy_json_aware(text)
    if uid is _FAILED_54000_SENTINEL:
        # Known-failed injection; do NOT fall back to looser strategies.
        return None
    if uid is not None:
        return uid

    uid = _uid_strategy_regex(text)
    if uid is not None:
        return uid

    return _uid_strategy_chaosblade_resource(text)


def _uid_strategy_json_aware(text: str):
    """Walk every `{` in `text`, parse JSON segments, apply blade semantics.

    Returns one of:
      - str: a UID extracted from a recognized success or 54000 response.
      - None: no JSON object yielded a usable UID.
      - _FAILED_54000_SENTINEL: encountered a 54000+success=false response;
        caller MUST refuse to extract a UID from this output.
    """
    decoder = json.JSONDecoder()
    scan_from = 0
    saw_failed_54000 = False

    while True:
        idx = text.find("{", scan_from)
        if idx < 0:
            break
        try:
            data, end_idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            scan_from = idx + 1
            continue

        if isinstance(data, dict):
            if data.get("success") is True and data.get("code") == 200:
                result = data.get("result")
                if isinstance(result, str) and result:
                    return result

            if data.get("code") == 54000:
                result = data.get("result")
                if isinstance(result, dict):
                    uid = result.get("uid")
                    if isinstance(uid, str) and uid:
                        error_msg = (data.get("error") or "").lower()
                        # Distinguish "still initializing" from "truly failed":
                        # - "unexpected status ... Initialized, please wait"
                        #   → CRD accepted, operator still bootstrapping.
                        #   The experiment MAY succeed; extract uid so the
                        #   verifier can check status later.
                        # - "command not found" / "exec failed" / other
                        #   → injection process actually failed; ignore uid.
                        _is_initializing = (
                            "please wait" in error_msg
                            or "initialized" in error_msg
                        )
                        if data.get("success") is False and not _is_initializing:
                            logger.info(
                                "experiment_uid extraction: 54000 + success=false + "
                                "terminal error, treating as failed (uid=%s ignored)",
                                uid,
                            )
                            saw_failed_54000 = True
                        else:
                            logger.info(
                                "experiment_uid extraction: 54000, extracted uid=%s "
                                "(initializing=%s)",
                                uid, _is_initializing,
                            )
                            return uid

        scan_from = end_idx

    if saw_failed_54000:
        return _FAILED_54000_SENTINEL
    return None


def _uid_strategy_regex(text: str) -> str | None:
    """Find the first UUID-shaped value of `result` or `uid` in `text`."""
    match = _UUID_RE.search(text)
    if match:
        return match.group(1)
    return None


def _uid_strategy_chaosblade_resource(text: str) -> str | None:
    """Find a `chaosblade-<hex>` resource name as a last-resort identifier."""
    match = _CHAOSBLADE_RESOURCE_RE.search(text)
    if match:
        return match.group(1)
    return None


def scan_destroyed_uids(messages: list) -> set[str]:
    """UIDs the LLM has issued ``blade_destroy`` for (from AIMessage tool_calls).

    A UID sent to ``blade_destroy`` is no longer an active injection: whether
    the destroy succeeded or failed, it is residual and MUST NOT be picked up
    as the current fault's carrier. In-package shared tool (phase-13): consumed
    by both blade-family providers' ``destroyed_experiment_ids`` hooks (the
    registry's union seam for the generic layer), by
    ``extract_experiment_uid_from_messages`` internally, and by provider
    detection — every blade-family consumer applies the same destroyed-exclusion
    rigor (task-76c59364 regression: ``ChaosbladeProvider.detect`` re-claimed a
    failed, already-cleaned experiment when only the extractor excluded).
    """
    destroyed: set[str] = set()
    for msg in messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            if name != "blade_destroy":
                continue
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            uid = args.get("uid", "") if isinstance(args, dict) else ""
            if uid:
                destroyed.add(uid)
    return destroyed


def scan_blade_evidence_index(
    messages: list, *, destroyed: set[str] | frozenset[str] = frozenset(),
) -> tuple[int, str | None]:
    """Most-recent NON-destroyed ChaosBlade injection evidence.

    Reverse-scans for the latest ``blade_create`` / ``kubectl`` ToolMessage
    carrying a parseable blade UID that has NOT been ``blade_destroy``'d.
    Returns ``(message_index, method)`` where method is ``host_blade`` (via the
    blade tool) or ``kubectl_exec`` (blade run through kubectl exec), or
    ``(-1, None)`` when no live blade experiment is attested.

    The ``message_index`` is the recency key the registry uses to arbitrate
    against a later kubectl-/host-native injection (attribute to the LAST
    successful injection, not the earliest blade UID in history).

    A ``kubectl`` ToolMessage attests blade evidence ONLY when its owning
    tool_call is the blade-exec delivery (``subcommand='exec'`` with ``blade``
    and ``create`` in ``v_args``) — cross-checked through the AIMessage
    tool-call lookup, the SAME gate :func:`extract_experiment_uid_from_messages`
    and :func:`scan_kubectl_blade_success` apply. Every other kubectl output
    is outside the blade domain: a ``kubectl debug`` result embeds a
    ``[debug-pod-meta: {"uid": ...}]`` block whose ``uid`` is the K8s OBJECT
    UID of the debug pod, and a ``get -o json`` embeds ``metadata.uid`` —
    shape-identical to a blade experiment UID and reachable by the loose
    regex fallback, but never a ChaosBlade experiment (task-51193464: such a
    mis-read attributed ``injection_method=kubectl_exec`` four minutes BEFORE
    the real native injection ran, and the unfulfillable UID-less attribution
    then deadlocked the executor out of the verifier). A kubectl ToolMessage
    whose call cannot be resolved in the lookup is skipped fail-closed — an
    unattributable kubectl output must not license a blade attribution.
    """
    lookup = build_tool_call_args_lookup(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", "") or ""
        if name not in ("blade_create", "kubectl"):
            continue
        if name == "kubectl":
            tc_id = getattr(msg, "tool_call_id", "")
            args = lookup.get(tc_id) if tc_id else None
            if not isinstance(args, dict):
                continue
            v_args = args.get("v_args", "") or ""
            if (
                args.get("subcommand") != "exec"
                or "blade" not in v_args
                or "create" not in v_args
            ):
                continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        uid = extract_experiment_uid(content)
        if uid and uid not in destroyed:
            return i, ("host_blade" if name == "blade_create" else "kubectl_exec")
    return -1, None


def scan_kubectl_blade_success(messages: list) -> bool:
    """True if ``kubectl exec`` was used to successfully inject a ChaosBlade
    experiment (bypassing the ``blade_create`` tool).

    Finds a kubectl ToolMessage carrying ChaosBlade success JSON
    (``{"code":200,"success":true,"result":"<uid>"}``) and cross-references
    the AIMessage tool_call to verify it was ``subcommand='exec'`` with
    ``blade`` + ``create`` in ``v_args``. Falls back to content-only detection
    when the tool_call_id is missing (older sessions / synthetic ids).
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

        if not (isinstance(data, dict)
                and data.get("success") is True
                and data.get("code") == 200
                and isinstance(data.get("result"), str)
                and data["result"]):
            continue

        tc_id = getattr(msg, "tool_call_id", "")
        if tc_id and tc_id in lookup:
            args = lookup[tc_id]
            subcommand = args.get("subcommand", "")
            v_args = args.get("v_args", "")
            if subcommand == "exec" and "blade" in v_args and "create" in v_args:
                return True
            continue

        logger.debug(
            "kubectl ToolMessage with ChaosBlade success JSON: "
            "tool_call_id=%s not in AIMessage lookup, using content-only detection",
            tc_id or "(none)",
        )
        return True

    return False


def was_kubectl_exec_delivery(
    state: dict, messages: list | None = None,
) -> bool:
    """Whether the LIVE experiment was created via ``kubectl exec``.

    Two evidence sources, unioned — the same pattern as the blade_destroy
    provenance fix (SC1):

    1. ``state["injection_method"] == "kubectl_exec"`` — the framework's
       DURABLE record. It is committed in the same iteration the
       ``experiment_uid`` appears (channel A/B), is ``durable=True`` in the
       state lifecycle and is inherited by the recover graph, so it
       survives anything that happens to the message list.
    2. The message scan (:func:`scan_kubectl_blade_success`) — kept as a
       fallback for state-less paths (sessions restored without the
       method recorded). ``messages`` overrides ``state["messages"]``
       for callers that receive the history separately (the provider
       ``recover()`` contract passes it through kwargs).

    The scan alone is NOT durable: recovery runs LATE in the task, and
    compaction removes the injection evidence pair (the kubectl-exec
    AIMessage + its ChaosBlade-success ToolMessage are among the oldest
    messages) BY DESIGN. Losing it mis-routes a CRD-created experiment
    into the deterministic HOST ``blade_destroy`` — which cannot reach it
    ("record not found") — and withholds the kubectl-exec recovery
    instructions from the LLM flow. The verify side already routes on the
    durable record (``ChaosbladeProvider.layer1_verify``); recovery must
    agree with it.
    """
    if state.get("injection_method") == "kubectl_exec":
        return True
    msgs = messages if messages is not None else (state.get("messages") or [])
    return scan_kubectl_blade_success(msgs)


def was_blade_create_attempted(
    messages: list, injection_method: str | None = None,
) -> bool:
    """Check if ChaosBlade injection was attempted but ultimately failed.

    Returns False (not "attempted-and-failed") if:
      - a committed ``injection_method`` durable record exists (see below)
      - kubectl exec successfully injected a blade experiment (bypassing blade_create)
      - kubectl-native injection was used as an alternative after blade_create failed
    Returns True only if blade_create was called AND no successful injection
    was detected via any method.

    This distinguishes two scenarios when experiment_uid is empty:
      - True:  ChaosBlade injection was attempted but failed → Layer 1 returns "failed"
      - False: Non-ChaosBlade fault, OR kubectl-based injection succeeded → Layer 1 returns "skipped"

    Durable record first: ``injection_method`` is committed when the
    injection is ISSUED/succeeds (Direction B) and survives compaction — the
    same rationale as :func:`was_kubectl_exec_delivery`. ANY committed
    attribution is positive proof that some injection succeeded, so the
    "blade attempted but nothing injected" branch cannot apply, regardless
    of what the (possibly compacted) message history still shows. Without
    this, a replan/compaction that removes the kubectl-native fallback
    evidence while leaving a failed ``blade_create`` ToolMessage mis-routes
    a live, recoverable fault into the terminal "no UID" failure. The
    message scan below stays as the fallback for state-less restored
    sessions that have no durable record.
    """
    if injection_method:
        return False

    # If kubectl-based blade injection succeeded, injection was NOT "attempted and failed"
    if scan_kubectl_blade_success(messages):
        return False

    # If kubectl-native injection was used as alternative after blade_create
    # failed, treat as non-ChaosBlade fault (Layer 1 = "skipped"). The
    # vocabulary is kubectl tool-domain knowledge
    # (``providers.message_scanning``, phase-14 G2: the "did native take over
    # after blade failed" judgement is boundary knowledge and lives with the
    # blade domain, but the word lists it consumes belong to the tool).
    if scan_kubectl_injection_after_blade(
        messages,
        KUBECTL_WRITE_SUBCOMMANDS,
        command_subcommands=KUBECTL_COMMAND_SUBCOMMANDS,
        is_mutating_command=exec_inner_command_mutates,
    ):
        return False

    for msg in messages:
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "blade_create":
            return True
    return False


# ---------------------------------------------------------------------------
# Experiment-UID extraction from message evidence
# ---------------------------------------------------------------------------

def _parse_blade_uid_from_content(content) -> str | None:
    """Extract a ChaosBlade UID from ToolMessage content.

    Thin wrapper around this module's :func:`extract_experiment_uid`
    — accepts the raw `content` field of a ToolMessage (string or other) and
    delegates multi-strategy parsing to the shared provider-family helper.
    """
    if not isinstance(content, str):
        return None
    return extract_experiment_uid(content)


def _parse_uid_from_status_content(content) -> str | None:
    """Extract experiment UID from blade_status or blade_query_k8s output.

    blade_status / blade_query_k8s return:
        {"code":200,"success":true,"result":{"uid":"<hex>","phase":"Running",...}}

    Unlike blade_create (where ``result`` is a string UID), these tools
    return ``result`` as a **dict** containing a ``uid`` field. The
    standard ``extract_experiment_uid`` does not handle this case because its
    strategy 1 only accepts string results, and its regex strategy expects
    UUID format (8-4-4-4-12) while ChaosBlade UIDs are short hex strings.
    """
    if not isinstance(content, str) or not content:
        return None

    # First try the standard extractor (handles blade_create format
    # where result is a string, and chaosblade-<hex> resource names)
    uid = extract_experiment_uid(content)
    if uid:
        return uid

    # Handle blade_status/blade_query_k8s format where result is a dict
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # ChaosBlade success response with dict result
    if data.get("success") is True and data.get("code") == 200:
        result = data.get("result")
        if isinstance(result, dict):
            uid = result.get("uid")
            if isinstance(uid, str) and uid:
                return uid

    return None


def extract_experiment_uid_from_messages(
    messages: list,
    retired: "list[str] | set[str] | None" = None,
) -> str | None:
    """Scan messages for an experiment uid from a blade-family tool's output.

    ChaosBlade `blade create` returns JSON like:
        {"code": 200, "success": true, "result": "<uid>"}

    Sources scanned, in priority order:
      1. an experiment-creating tool (``blade_create`` for OS / K8s faults,
         ``blade_python_create`` for in-process application faults),
      2. ``kubectl exec ... blade create`` — the bypass the LLM may use when the
         blade tool fails on the host, where the success JSON lands in a kubectl
         ToolMessage,
      3. ``blade_status`` / ``blade_query_k8s`` — relevant when the create call
         timed out but the experiment was in fact created, so the LLM discovered
         the uid via a status query (uid nested in a dict ``result`` field).

    Only kubectl exec calls whose v_args contain "blade create" are considered —
    other kubectl outputs (get -o json, describe, ...) are NOT scanned, to
    prevent false-positive extraction from K8s resource ``metadata.uid`` fields.

    ``retired``: UIDs destroyed by FRAMEWORK-side cleanup (verify-replan
    residual destroy). They leave no ``blade_destroy`` ToolMessage, so the
    message scan alone would resurrect them; callers holding
    ``state.retired_experiment_uids`` must pass it here (task-29848471).
    """
    kubectl_uid = None  # fallback uid from kubectl exec
    status_uid = None   # fallback uid from blade_status / blade_query_k8s

    # UIDs already sent to blade_destroy are cleaned-up / residual — never
    # treat them as the current active injection (root-cause guard).
    # ``scan_destroyed_uids`` is the carrier-agnostic primitive (the
    # execute-node's ``_collect_destroyed_uids`` mirrors it).
    destroyed = scan_destroyed_uids(messages)
    if retired:
        destroyed |= set(retired)

    # Build a set of tool_call_ids that correspond to "kubectl exec ... blade create"
    blade_exec_call_ids: set[str] = set()
    for msg in messages:
        if not hasattr(msg, "tool_calls"):
            continue
        for tc in (msg.tool_calls or []):
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            if name == "kubectl" and isinstance(args, dict):
                v_args = args.get("v_args", "")
                if "blade" in v_args and "create" in v_args:
                    blade_exec_call_ids.add(tc_id)

    # Check if an experiment-creating tool was attempted (even if it failed /
    # timed out). blade_status UID extraction is only relevant then — otherwise
    # the status check might pick up unrelated experiments.
    _EXPERIMENT_CREATE_TOOLS = ("blade_create", "blade_python_create")
    _has_blade_create = any(
        isinstance(msg, ToolMessage)
        and getattr(msg, "name", "") in _EXPERIMENT_CREATE_TOOLS
        for msg in messages
    )

    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        msg_name = getattr(msg, "name", "") or ""
        content = msg.content

        # Priority 1: a ToolMessage from an experiment-creating tool. Both
        # ``blade_create`` (OS / K8s carrier) and ``blade_python_create``
        # (in-process application carrier) return the same ChaosBlade CLI JSON
        # with the experiment uid, and both recover via ``blade destroy <uid>``.
        # Missing the python tool here would leave ``experiment_uid`` unset on the
        # ReAct path, so verification and recovery would have no uid to act on.
        if msg_name in _EXPERIMENT_CREATE_TOOLS:
            uid = _parse_blade_uid_from_content(content)
            if uid and uid not in destroyed:
                return uid

        # Priority 2: kubectl exec blade ToolMessage ONLY
        if msg_name == "kubectl" and not kubectl_uid:
            tool_call_id = getattr(msg, "tool_call_id", "") or ""
            if tool_call_id in blade_exec_call_ids:
                _uid = _parse_blade_uid_from_content(content)
                if _uid and _uid not in destroyed:
                    kubectl_uid = _uid

        # Priority 3: blade_status / blade_query_k8s ToolMessage
        # Relevant when blade_create timed out but experiment was created.
        if msg_name in ("blade_status", "blade_query_k8s") and not status_uid:
            if _has_blade_create:
                _uid = _parse_uid_from_status_content(content)
                if _uid and _uid not in destroyed:
                    status_uid = _uid

    # Return by priority: blade_create > kubectl exec > blade_status
    return kubectl_uid or status_uid


# ---------------------------------------------------------------------------
# Layer-1 execution domain (moved verbatim from
# ``nodes/verify/_verifier_layer1.py`` in phase-4 T4 — design D2: the
# execution bodies the providers' ``layer1_verify`` dispatch into physically
# belong to the provider layer; only the STATE orchestration stayed in nodes).
# ---------------------------------------------------------------------------

# Layer1Result is now a Pydantic model imported from chaos_agent.agent.result.verdict

# Upper bound on how many discovered tool pods Layer 1 probes for the
# experiment record. Exec-carrier records live in one pod's local DB and
# discovery order is arbitrary, so we sweep broadly; this cap only bounds
# worst-case probe time on very large clusters (definitive results return
# early). See task-2d612caa.
_MAX_DISCOVERY_PROBES = 8


# ---------------------------------------------------------------------------
# Refactor 2: 提取 blade_status JSON 解析为独立函数
# 原因: blade_status 返回值解析嵌套 5-6 层，在 verifier() 和
#        _verifier_with_llm() 中完全重复
# 做法: 独立函数 + 扁平化 if/elif，消除深层嵌套
# ---------------------------------------------------------------------------

_EXPIRED_STATES = frozenset({"Destroyed", "destroyed", "Revoked", "revoked", "Completed", "completed"})

_RUNNING_STATES = frozenset({"Running", "running", "Success", "success"})

# Phases the Operator reports while it is still setting the experiment up. Not a
# verdict: ``Initialized`` means the CRD exists and the controller has not
# finished reconciling it, so ``statuses`` is still empty and there is nothing
# for Layer 1 to read. The fault itself may already be in effect — task-fc64c982
# stopped containerd, saw the node go Ready→NotReady, and was still reported
# ``failed`` because the CRD had not left ``Initialized`` by the time Layer 1
# polled. A setup phase is therefore a warning, which keeps Layer 2 in play to
# judge the actual cluster state.
_TRANSIENT_STATES = frozenset({"Initialized", "initialized", "Creating", "creating"})

# Substrings that unambiguously signal a FAILED / absent experiment. Checked in
# the non-JSON fallback path BEFORE the permissive _RUNNING_STATES match, so a
# wrapped `{"success":false,...}` (whose JSON was unparseable due to a shell
# "command terminated" trailer) is never misread as Running.
_FAILURE_SIGNALS = (
    "record not found",
    "not found",
    '"success":false',
    '"success": false',
    "command terminated with exit code",
)


def _extract_json_object(raw: str) -> dict | None:
    """Extract the first top-level JSON object from possibly-wrapped output.

    Transport wrappers can prepend an ``exit_code: N`` line and append a
    ``command terminated with exit code N`` trailer around the real ChaosBlade
    JSON, which makes a naive ``json.loads(raw)`` fail and pushes callers onto
    fragile substring matching. This scans for the first ``{`` and uses
    ``raw_decode`` so surrounding noise is ignored.
    """
    if not raw:
        return None
    start = raw.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError:
            start = raw.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = raw.find("{", start + 1)
    return None


def _parse_iso_ts_seconds(ts_value, created_value) -> float | None:
    """Return (UpdateTime - CreateTime) in seconds, or None if unparseable."""
    from datetime import datetime

    def _parse(ts) -> datetime | None:
        if not isinstance(ts, str) or not ts.strip():
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    updated = _parse(ts_value)
    created = _parse(created_value)
    if updated is None or created is None:
        return None
    return (updated - created).total_seconds()


def _is_early_destroy(res: dict, exp_status: str) -> bool:
    """True when an expired record was destroyed BEFORE its --timeout elapsed.

    Distinguishes timeout expiry (record lives out its full window) from an
    external destroy — e.g. the executor cleaning up its own injection record
    after a one-shot fault (task inject-e47de3e8: destroyed +20.4s after
    creation with --timeout=600). The two need different verdicts: expiry
    means the fault window closed and live observation is impossible, while an
    early destroy says nothing about the fault's actual effects.
    """
    flag = str(res.get("Flag", "") or "")
    match = re.search(r"--timeout[=\s]+(\d+)", flag)
    if not match:
        return False
    try:
        timeout_seconds = int(match.group(1))
    except ValueError:
        return False
    if timeout_seconds <= 0:
        return False
    elapsed = _parse_iso_ts_seconds(
        res.get("UpdateTime") or res.get("update_time"),
        res.get("CreateTime") or res.get("create_time"),
    )
    if elapsed is None:
        return False
    logger.info(
        "Layer1 expired-record attribution: status=%s elapsed=%.1fs timeout=%ss",
        exp_status, elapsed, timeout_seconds,
    )
    return elapsed < timeout_seconds


def _parse_blade_status_output(raw: str) -> tuple[str, str, bool]:
    """Parse blade_status JSON output into (status, details, expired).

    Returns:
        status: "passed" if experiment is Running/Success, "failed" otherwise.
        details: Human-readable details string.
        expired: True if experiment status is Destroyed/Revoked/Completed (timeout expired).
    """
    data = _extract_json_object(raw)
    if data is None:
        # Fallback: raw string search. Guard against false positives first —
        # an explicit failure signal (e.g. `"success":false` / "record not
        # found" / a shell "command terminated" trailer) must NOT be read as
        # "Running" just because the substring "success" appears inside it.
        lowered = raw.lower()
        if any(sig in lowered for sig in _FAILURE_SIGNALS):
            return "failed", raw[:200], False
        if any(s in raw for s in _RUNNING_STATES):
            return "passed", "blade_status: Running (raw match)", False
        return "failed", raw[:200], False

    if not (data.get("success") or data.get("code") == 200):
        return "failed", raw[:200], False

    res = data.get("result", {})
    # Non-dict result (e.g. just a UID string) means success
    if not isinstance(res, dict):
        return "passed", "blade_status: Success (experiment running)", False

    exp_status = res.get("Status", res.get("status", "")) or res.get("phase", "")
    if exp_status in _RUNNING_STATES:
        return "passed", f"blade_status: {exp_status} (experiment running)", False
    if exp_status in _EXPIRED_STATES:
        if _is_early_destroy(res, exp_status):
            return (
                "warning",
                f"Experiment record is '{exp_status}' before its --timeout elapsed — "
                f"the record was destroyed externally (e.g. post-injection cleanup), "
                f"not by timeout expiry. Record liveness cannot judge the fault's "
                f"actual effects; Layer 2 will verify cluster-level evidence.",
                True,
            )
        return (
            "failed",
            f"Experiment status: {exp_status} — the fault window has expired "
            f"(--timeout elapsed), so live fault effects can no longer be observed. "
            f"Layer 2 may still find residual evidence.",
            True,
        )
    # Transient state: the experiment is mid-transition, either because the
    # Operator is still reconciling a freshly created CRD (``Initialized``) or
    # because blade reports "please wait" during setup/teardown. Neither is a
    # verdict — the fault may already be in effect, so Layer 2 decides on the
    # actual cluster state rather than on the controller's bookkeeping.
    error_msg = res.get("Error", "")
    if exp_status in _TRANSIENT_STATES:
        return (
            "warning",
            f"Experiment status: {exp_status} — the Operator is still setting the "
            f"experiment up, so no per-resource status is available yet. "
            f"Layer 2 will verify actual cluster state.",
            False,
        )
    if "please wait" in error_msg.lower():
        return (
            "warning",
            f"Experiment in transient state ({error_msg}). "
            f"Layer 2 will verify actual cluster state.",
            False,
        )
    return "failed", f"Experiment status: {exp_status}", False


# ---------------------------------------------------------------------------
# Refactor 3: 提取 blade_query_k8s 结果解析为独立函数
# 原因: 同上，深层嵌套 + 两处重复
# 做法: 独立函数，职责单一——只负责解析 query k8s 返回值
# ---------------------------------------------------------------------------

_QueryK8sResult = namedtuple(
    "_QueryK8sResult", ["status", "details", "resource_statuses", "affected_count", "expired"],
)


def _parse_blade_query_k8s_output(raw: str) -> _QueryK8sResult:
    """Parse blade_query_k8s JSON output for per-resource status.

    Returns:
        _QueryK8sResult with:
            status: "passed" if all resources succeeded, "failed" if any failed,
                    "unknown" if output cannot be parsed (non-critical).
            details: Human-readable summary of resource-level results.
            resource_statuses: list of per-resource dicts from statuses[].
            affected_count: number of resources in statuses[].
            expired: True if any resource has state in _EXPIRED_STATES (Destroyed/Revoked/Completed).
    """
    _empty = _QueryK8sResult("unknown", "", [], 0, False)

    if not raw or raw.startswith("Error"):
        logger.debug(f"blade_query_k8s: empty/error output, raw={raw[:200]!r}")
        # Extract meaningful info from error messages (e.g., "not found" = CRD not yet ready)
        if "not found" in raw:
            return _QueryK8sResult("unknown", "blade_query_k8s: CRD not yet ready (will be available shortly)", [], 0, False)
        return _empty

    data = _extract_json_object(raw)
    if data is None:
        logger.debug(f"blade_query_k8s: non-JSON output, raw={raw[:200]!r}")
        return _empty

    if not (data.get("success") or data.get("code") == 200):
        logger.debug(f"blade_query_k8s: unsuccessful response, data={json.dumps(data, ensure_ascii=False)[:200]}")
        # ChaosBlade returns JSON errors like {"code":63061,"success":false,"error":"...not found"}
        err_msg = data.get("error", "")
        if "not found" in err_msg.lower():
            return _QueryK8sResult("unknown", "blade_query_k8s: CRD not found (experiment may still be initializing)", [], 0, False)
        return _empty

    qresult = data.get("result", {})
    statuses = qresult.get("statuses", [])

    if statuses:
        # Check for expired states FIRST (before generic failed check)
        expired_states = [s for s in statuses if s.get("state", "") in _EXPIRED_STATES]
        if expired_states:
            names = [s.get("name", "?") for s in expired_states]
            return _QueryK8sResult(
                "failed",
                f"blade query k8s: experiment expired (state: Destroyed/Revoked): {names}",
                statuses, len(statuses), True,
            )
        failed = [s for s in statuses if not s.get("success", True)]
        if failed:
            names = [s.get("name", "?") for s in failed]
            return _QueryK8sResult("failed", f"blade query k8s: failed resources: {names}", statuses, len(statuses), False)
        return _QueryK8sResult("passed", f"blade query k8s: all {len(statuses)} resource(s) Success", statuses, len(statuses), False)

    if isinstance(qresult, dict) and qresult.get("success", True):
        return _QueryK8sResult("passed", "blade query k8s: confirmed", [], 0, False)

    logger.debug(f"blade_query_k8s: unhandled format, result={json.dumps(qresult, ensure_ascii=False)[:200]}")
    return _empty


# ---------------------------------------------------------------------------
# Refactor 4: 提取完整的 Layer 1 验证流程为独立函数
# 原因: Layer 1 逻辑（blade_status → blade_query_k8s）在两个入口函数中
#        完全重复 ~80 行，且包含 try/except 错误处理
# 做法: 独立 async 函数，返回 Layer1Result dataclass，彻底消除重复
# Note: _resolve_kubeconfig moved to _kubeconfig_inject.py for shared use
# ---------------------------------------------------------------------------


def _find_blade_query_in_messages(messages: list, experiment_uid: str) -> str:
    """Scan kubectl ToolMessages for blade query k8s output matching the given uid.

    When the host blade binary is unavailable, the LLM may have already run
    `blade query k8s create <uid>` via kubectl exec during the execution phase.
    This function finds that output so Layer 1 can use it as verification evidence.

    Returns the raw JSON string if found, empty string otherwise.
    """
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") != "kubectl":
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if experiment_uid in content and '"success"' in content:
            try:
                data = json.loads(content)
                if isinstance(data, dict) and data.get("success") is True:
                    result = data.get("result", {})
                    if isinstance(result, dict) and result.get("uid") == experiment_uid:
                        return content
            except (json.JSONDecodeError, TypeError):
                pass
    return ""


def _map_query_k8s_to_layer1(
    q_result: _QueryK8sResult, raw: str, pod_name: str, source: str,
) -> Layer1Result:
    """Map _QueryK8sResult to Layer1Result with expired detection.

    Used when kubectl exec path uses `blade query k8s create <uid>`
    instead of `blade status <uid>` (CRD UID not in pod's local DB).
    """
    if q_result.status == "passed":
        layer1_status = "passed"
    elif q_result.expired:
        # expired=True means experiment Destroyed/Revoked
        layer1_status = "failed"
    else:
        layer1_status = q_result.status  # "failed" or "unknown"
    return Layer1Result(
        status=layer1_status,
        details=f"blade query k8s via kubectl exec ({pod_name}, {source}): {q_result.details}",
        raw_output=raw,
        resource_statuses=q_result.resource_statuses,
        affected_count=q_result.affected_count,
        expired=q_result.expired,
    )


async def _run_layer1_via_kubectl_exec(
    experiment_uid: str, kubeconfig: str, *, task_id: str = "",
    injection_pod_name: str | None = None,
) -> Layer1Result:
    """Layer 1 verification via kubectl exec into a tool pod.

    Used when injection_method is "kubectl_exec" (host blade binary may be
    incompatible, so host blade_status would fail).

    If the original injection pod name is known (injection_pod_name), it is
    tried first (Step 0) before discovering new pods (Step 1). This maximises
    success probability since the original pod is where the experiment was
    created and is most likely to have it visible.

    Error handling follows the principle "infrastructure failure ≠ experiment failure":
    - Type A (infrastructure failure): can't discover pods or can't exec
      into them -> "skipped" (non-terminal, Layer 2 proceeds)
    - Type B (experiment status failure): blade status returns Error/Destroyed
      -> "failed" (terminal, blocks Layer 2)

    Retries up to 2 different pods before giving up.
    """
    # No UID → nothing to poll. A kubectl-native injection (or a failed
    # blade_create) reaches here only via mis-detection; issuing `blade status
    # ''` / `blade query k8s create ''` returns ChaosBlade code 45000
    # ("less parameter: type|uid") which _parse_blade_status_output reads as a
    # genuine experiment FAILURE — wrongly failing a successful native fault
    # (task-76c59364). Treat an absent UID as "not applicable", not failed.
    if not experiment_uid:
        return Layer1Result(
            status="skipped",
            details="kubectl_exec Layer 1: no experiment_uid to query "
                    "(kubectl-native injection or failed blade create) — "
                    "Layer 1 not applicable, Layer 2 will verify cluster state.",
        )
    tracker = get_tracker(task_id) if task_id else None

    try:
        from chaos_agent.tools.kubectl import build_kubectl_cmd
        from chaos_agent.transports import (
            PROFILE_K8S,
            TransportTarget,
            execute_via_transport,
        )

        _target = TransportTarget.from_state({})

        # Step 0: Try the original injection pod first (if known)
        if injection_pod_name:
            # PRIMARY: blade query k8s (queries CRD, works with CRD UID)
            # blade status <crd_uid> returns "record not found" inside pod
            # because pod's local experiment DB uses a different UID.
            query_cmd = build_kubectl_cmd("exec", [
                injection_pod_name, "-n", _TOOL_POD_NAMESPACE,
                "--", "blade", "query", "k8s", "create", experiment_uid,
            ], kubeconfig=kubeconfig)
            try:
                query_run_result = await execute_via_transport(
                    query_cmd, _target, task_id=task_id, source="verifier-L1", expect_profile=PROFILE_K8S)
                raw = query_run_result.stdout

                # Check if blade query k8s is available (not in older ChaosBlade versions)
                if raw and "unknown command" not in raw and "command not found" not in raw:
                    if "error: unable to upgrade connection" in raw:
                        logger.info(
                            f"Original injection pod {injection_pod_name} unavailable, "
                            f"falling back to pod discovery"
                        )
                    else:
                        q_result = _parse_blade_query_k8s_output(raw)
                        # Only return if parseable; if unknown (kubectl exec error,
                        # non-JSON output), fall through to blade status fallback
                        if q_result.status != "unknown":
                            layer1_result = _map_query_k8s_to_layer1(q_result, raw, injection_pod_name, "original")
                            if tracker:
                                tracker.update(
                                    f"Layer 1 step 0: blade_query_k8s (kubectl exec {injection_pod_name}): {layer1_result.status}",
                                    {"step": "blade_query_k8s_kubectl", "status": layer1_result.status,
                                     "pod": injection_pod_name, "source": "original"},
                                )
                            return layer1_result
                        logger.info(
                            f"blade query k8s returned unparseable result from pod {injection_pod_name}, "
                            f"trying blade status fallback"
                        )
                elif raw and ("command not found" in raw or "No such file" in raw):
                    logger.info(
                        f"blade query k8s not available in pod {injection_pod_name}, "
                        f"trying blade status fallback"
                    )
                # If blade query k8s failed or unavailable, fall through to blade status
            except Exception as e:
                logger.info(
                    f"blade query k8s failed on original pod {injection_pod_name}: {e}, "
                    f"trying blade status fallback"
                )

            # FALLBACK: blade status (searches local DB, CRD UID may not be found)
            status_cmd = build_kubectl_cmd("exec", [
                injection_pod_name, "-n", _TOOL_POD_NAMESPACE,
                "--", "blade", "status", experiment_uid,
            ], kubeconfig=kubeconfig)
            try:
                status_result = await execute_via_transport(
                    status_cmd, _target, task_id=task_id, source="verifier-L1", expect_profile=PROFILE_K8S)
                raw = status_result.stdout

                # Check if the original pod is unavailable
                if raw and ("not found" in raw
                            or "error: unable to upgrade connection" in raw):
                    logger.info(
                        f"Original injection pod {injection_pod_name} unavailable, "
                        f"falling back to pod discovery"
                    )
                elif raw and ("command not found" in raw or "No such file" in raw):
                    logger.info(
                        f"blade binary not found in original pod {injection_pod_name}, "
                        f"falling back to pod discovery"
                    )
                elif raw:
                    # Got a parseable response from the original pod
                    status, details, expired = _parse_blade_status_output(raw)
                    if tracker:
                        tracker.update(
                            f"Layer 1 step 0: blade_status (kubectl exec {injection_pod_name}): {status}",
                            {"step": "blade_status_kubectl", "status": status,
                             "pod": injection_pod_name, "source": "original"},
                        )
                    return Layer1Result(
                        status=status,
                        details=f"blade_status via kubectl exec ({injection_pod_name}, original): {details}",
                        raw_output=raw,
                        expired=expired,
                    )
                else:
                    logger.info(
                        f"Empty response from original pod {injection_pod_name}, "
                        f"falling back to pod discovery"
                    )
            except Exception as e:
                logger.info(
                    f"Failed to query original pod {injection_pod_name}: {e}, "
                    f"falling back to pod discovery"
                )

        # Step 1: Discover running tool pods (cluster-wide)
        from chaos_agent.tools.pod_discovery import discover_tool_pods_cluster_wide
        try:
            pods_with_ns = await discover_tool_pods_cluster_wide(kubeconfig, task_id)
        except Exception as e:
            msg = f"kubectl exec: failed to discover tool pods: {e}"
            if tracker:
                tracker.update(f"Layer 1 (kubectl exec): {msg} -> skipped",
                               {"step": "discover_pods", "status": "skipped"})
            return Layer1Result(
                status="skipped",
                details=f"{msg} (infrastructure issue, not experiment failure)",
            )

        if not pods_with_ns:
            msg = "kubectl exec: no running tool pods found, cannot verify blade status"
            if tracker:
                tracker.update(f"Layer 1 (kubectl exec): {msg} -> skipped",
                               {"step": "discover_pods", "status": "skipped"})
            return Layer1Result(
                status="skipped",
                details=f"{msg} (infrastructure issue, not experiment failure)",
            )

        # Step 2: Try blade query k8s (primary) then blade status (fallback)
        # via kubectl exec on each discovered pod. Exec-carrier experiments
        # live in ONE pod's local DB and discovery order is arbitrary, so
        # every candidate must be probed before a "record not found" verdict
        # (task-2d612caa: a 2-pod cap read the wrong pod's empty DB as
        # experiment failure). The bound only limits worst-case probe time on
        # very large clusters; a definitive result always returns early.
        # NOTE: blade status v1.8.0 does NOT support --kubeconfig flag.
        # Inside the pod, blade can access the API server directly without kubeconfig.
        last_error = None
        for pod_name, pod_ns in pods_with_ns[:_MAX_DISCOVERY_PROBES]:
            # PRIMARY: blade query k8s (queries CRD, works with CRD UID)
            query_cmd = build_kubectl_cmd("exec", [
                pod_name, "-n", pod_ns,
                "--", "blade", "query", "k8s", "create", experiment_uid,
            ], kubeconfig=kubeconfig)
            try:
                query_result = await execute_via_transport(
                    query_cmd, _target, task_id=task_id, source="verifier-L1", expect_profile=PROFILE_K8S)
                raw = query_result.stdout

                # Check for Type A infrastructure errors
                if not raw or "error: unable to upgrade connection" in raw:
                    last_error = f"cannot exec into pod {pod_name}"
                    continue

                # If blade query k8s is available, use it
                if "unknown command" not in raw and "command not found" not in raw and "No such file" not in raw:
                    q_result = _parse_blade_query_k8s_output(raw)
                    # Only return if parseable; if unknown (kubectl exec error,
                    # non-JSON output), fall through to blade status fallback
                    if q_result.status != "unknown":
                        layer1_result = _map_query_k8s_to_layer1(q_result, raw, pod_name, "discovered")
                        if tracker:
                            tracker.update(
                                f"Layer 1 step 1/1: blade_query_k8s (kubectl exec {pod_name}): {layer1_result.status}",
                                {"step": "blade_query_k8s_kubectl", "status": layer1_result.status, "pod": pod_name},
                            )
                        return layer1_result
                    logger.info(f"blade query k8s returned unparseable result from pod {pod_name}, trying blade status")

                # FALLBACK: blade query k8s not available, try blade status
                logger.info(f"blade query k8s not available in pod {pod_name}, trying blade status")
            except Exception as e:
                logger.debug(f"blade query k8s failed in pod {pod_name}: {e}, trying blade status")

            # FALLBACK: blade status (searches local DB, CRD UID may not be found)
            status_cmd = build_kubectl_cmd("exec", [
                pod_name, "-n", pod_ns,
                "--", "blade", "status", experiment_uid,
            ], kubeconfig=kubeconfig)
            try:
                status_result = await execute_via_transport(
                    status_cmd, _target, task_id=task_id, source="verifier-L1", expect_profile=PROFILE_K8S)
                raw = status_result.stdout

                # Check for Type A infrastructure errors (can't execute command)
                if not raw or "command not found" in raw or "No such file" in raw:
                    last_error = f"blade binary not found in pod {pod_name}"
                    continue
                if "error: unable to upgrade connection" in raw:
                    last_error = f"cannot exec into pod {pod_name}"
                    continue

                # Per-pod local DB guard: an experiment created via exec on
                # one tool pod lives in THAT pod's local DB only — every
                # other tool pod reports "record not found". A discovered
                # pod's empty DB is therefore not a verdict; the next pod
                # may be the injection pod (task-2d612caa).
                if "record not found" in raw:
                    last_error = f"record not found in pod {pod_name} local DB"
                    continue

                # Type B: Parse blade status output (experiment status)
                status, details, expired = _parse_blade_status_output(raw)
                if tracker:
                    tracker.update(
                        f"Layer 1 step 1/1: blade_status (kubectl exec {pod_name}): {status}",
                        {"step": "blade_status_kubectl", "status": status, "pod": pod_name},
                    )
                return Layer1Result(
                    status=status,
                    details=f"blade_status via kubectl exec ({pod_name}): {details}",
                    raw_output=raw,
                    expired=expired,
                )
            except Exception as e:
                last_error = str(e)
                continue

        # All pods failed. Distinguish an experiment-level verdict from an
        # infrastructure failure: if every probed pod answered (exit code and
        # parseable JSON) but none holds the record, the experiment genuinely
        # cannot be found anywhere — that is a failure, not a skip.
        if last_error and "record not found" in last_error:
            msg = f"kubectl exec: experiment record not found in any tool pod's local DB ({last_error})"
            if tracker:
                tracker.update(
                    "Layer 1 (kubectl exec): record not found in all tool pods -> failed",
                    {"step": "blade_status_kubectl", "status": "failed"},
                )
            return Layer1Result(status="failed", details=msg)

        # Type A (infrastructure failure) -> skipped
        msg = f"kubectl exec: could not execute blade status in any tool pod ({last_error})"
        if tracker:
            tracker.update(
                "Layer 1 (kubectl exec): all tool pods failed -> skipped",
                {"step": "blade_status_kubectl", "status": "skipped"},
            )
        return Layer1Result(
            status="skipped",
            details=f"{msg} -- infrastructure issue, not experiment failure",
        )

    except Exception as e:
        logger.error(f"Layer 1 kubectl exec verification failed: {e}")
        return Layer1Result(
            status="skipped",
            details=f"kubectl exec verification error: {e} -- infrastructure issue, allowing Layer 2 to proceed",
        )


async def _run_host_blade_layer1(
    experiment_uid: str, kubeconfig: str, *, task_id: str = "",
    messages: list | None = None,
    injection_method: str | None = None,
) -> Layer1Result:
    """Execute host-blade Layer 1 verification: blade_status + blade_query_k8s.

    This is the ChaosBlade ``host_blade`` delivery body (local blade binary).
    The ``kubectl_exec`` delivery is a separate path selected by
    :class:`ChaosbladeProvider` via :func:`_run_layer1_via_kubectl_exec`, so this
    function carries no ``injection_method`` branching.

    Returns a Layer1Result with status, details, and raw output.
    Also emits per-step status events via StatusTracker so the user
    can see each check individually.
    """
    if not experiment_uid:
        # Same-package attempted judgement (was ``_was_blade_create_attempted``
        # through the nodes re-export pre-migration — same function object).
        if messages and was_blade_create_attempted(messages, injection_method):
            # blade_create was called but extract_blade_uid rejected the UID
            # (e.g., 54000+success=false). blade's error report may be wrong
            # (ChaosBlade may use fallback mechanisms like tc instead of
            # iptables). Mark as WARNING (non-terminal) — Layer 2 will
            # verify actual cluster state to determine the truth.
            return Layer1Result(
                status="warning",
                details="blade_create was called but reported error — "
                        "fault may still be in effect via fallback mechanisms. "
                        "Layer 2 will verify actual cluster state.",
            )
        return Layer1Result(
            status="skipped",
            details="Non-ChaosBlade fault (no blade_create used), Layer 1 not applicable",
        )

    tracker = get_tracker(task_id) if task_id else None

    try:
        from chaos_agent.agent.providers.chaosblade.cli import (
            blade_query_k8s,
            blade_status,
        )
        from chaos_agent.transports import PROFILE_HOST, profile_of, resolve_channel_name

        # Host scope has no cluster CRD: blade_query_k8s is k8s-only and would
        # just return a "not applicable" guidance string. blade_status (remote
        # local DB) is the authoritative Layer 1 check for host, so skip the
        # k8s-side query steps below when the resolved channel is a host channel.
        _is_host = profile_of(resolve_channel_name()) == PROFILE_HOST

        # Step 1: blade_status — experiment-level check
        status_output = await blade_status.ainvoke(
            {"uid": experiment_uid, "kubeconfig": kubeconfig}
        )
        raw = status_output if isinstance(status_output, str) else str(status_output)
        layer1_status, layer1_details, layer1_expired = _parse_blade_status_output(raw)

        # If blade_status failed because the experiment isn't in the local DB,
        # fall back to blade_query_k8s (cluster-side CRD query). Skipped for host
        # scope: there is no cluster CRD, so a host "record not found" is a
        # genuine failure that the k8s query cannot resolve.
        # Two cases: (1) explicit "record not found" message, (2) empty stdout
        # (kubewiz mode — experiment runs remotely, no local record exists).
        _fallback_used = False
        if not _is_host and layer1_status == "failed" and (not raw.strip() or "record not found" in raw.lower()):
            logger.info(f"blade_status local DB miss (raw={raw[:80]!r}), trying blade_query_k8s as fallback")
            try:
                query_output = await blade_query_k8s.ainvoke(
                    {"uid": experiment_uid, "kubeconfig": kubeconfig}
                )
                query_raw = query_output if isinstance(query_output, str) else str(query_output)
                q_result = _parse_blade_query_k8s_output(query_raw)
                if q_result.status != "unknown":
                    layer1_status = q_result.status
                    layer1_details = f"blade_query_k8s fallback: {q_result.details}"
                    layer1_expired = q_result.expired
                    # Preserve fallback data — will be used directly if Step 2 is skipped
                    q_resource_statuses = q_result.resource_statuses
                    q_affected_count = q_result.affected_count
                    _fallback_used = True
                    logger.info(f"blade_query_k8s fallback succeeded: status={q_result.status}")
            except Exception as qe:
                logger.debug(f"blade_query_k8s fallback also failed: {qe}")

        # Emit step 1 result
        step1_msg = f"Layer 1 step 1/2: blade_status: {layer1_status}"
        if layer1_details:
            step1_msg += f" - {layer1_details}"
        if tracker:
            tracker.update(step1_msg, {"step": "blade_status", "status": layer1_status})

        # Step 2: blade_query_k8s — per-resource check (supplementary)
        # Only if blade_status passed AND fallback was NOT used (fallback already
        # has the blade_query_k8s data; re-querying would waste an API call and
        # overwrite the fallback's resource_statuses/affected_count).
        query_status_str = "skipped"
        query_details_str = ""
        if _fallback_used:
            # Fallback already provided blade_query_k8s data — use it directly
            query_status_str = layer1_status
            query_details_str = layer1_details
        else:
            q_resource_statuses: list[dict] = []
            q_affected_count = 0
            if _is_host:
                # Host scope: no cluster CRD to query — blade_status above is
                # authoritative. Skip the supplementary k8s per-resource query.
                query_status_str = "n/a"
                query_details_str = "host scope: no cluster CRD (blade_status is authoritative)"
            elif layer1_status == "passed":
                try:
                    query_output = await blade_query_k8s.ainvoke(
                        {"uid": experiment_uid, "kubeconfig": kubeconfig}
                    )
                    query_raw = query_output if isinstance(query_output, str) else str(query_output)
                    q_result = _parse_blade_query_k8s_output(query_raw)
                    q_status = q_result.status
                    q_details = q_result.details
                    q_resource_statuses = q_result.resource_statuses
                    q_affected_count = q_result.affected_count
                    # If blade_query_k8s detected expired state, propagate expired flag
                    if q_result.expired:
                        layer1_expired = True
                    query_status_str = q_status
                    query_details_str = q_details

                    if q_status == "failed":
                        layer1_status = "failed"
                        layer1_details = f"blade_status: Running, but {q_details}"
                    elif q_status == "passed":
                        # CRD status settle guard: ChaosBlade CRD reports
                        # Success immediately upon creation, then asynchronously
                        # exec's the fault process into the target container.
                        # If exec fails (e.g. "dd: command not found" in minimal
                        # images), the CRD status flips to Error a few seconds
                        # later. Querying too early sees stale Success. Wait
                        # briefly and re-query to catch async failures.
                        import asyncio
                        if tracker:
                            tracker.update(
                                "CRD settle guard: waiting 5s to confirm injection process started",
                                {"step": "crd_settle_guard"},
                            )
                        await asyncio.sleep(5)
                        try:
                            recheck_output = await blade_query_k8s.ainvoke(
                                {"uid": experiment_uid, "kubeconfig": kubeconfig}
                            )
                            recheck_raw = recheck_output if isinstance(recheck_output, str) else str(recheck_output)
                            recheck = _parse_blade_query_k8s_output(recheck_raw)
                            if recheck.status == "failed":
                                q_status = "failed"
                                q_details = f"re-check after 5s: {recheck.details}"
                                q_resource_statuses = recheck.resource_statuses
                                q_affected_count = recheck.affected_count
                                query_status_str = q_status
                                query_details_str = q_details
                                layer1_status = "failed"
                                layer1_details = f"blade_status: Running, but {q_details}"
                                logger.info("CRD settle guard: status flipped to failed after 5s re-check")
                            elif recheck.expired:
                                layer1_expired = True
                            else:
                                layer1_details = f"blade_status: Running, {q_details} (confirmed after re-check)"
                        except Exception:
                            layer1_details = f"blade_status: Running, {q_details}"
                    else:
                        # q_status == "unknown": non-critical, keep blade_status result
                        if not layer1_details:
                            layer1_details = "blade_status: Running (blade_query_k8s unavailable)"
                except Exception as qe:
                    query_status_str = "error"
                    query_details_str = str(qe)
                    logger.debug(f"blade query k8s failed (non-critical): {qe}")

        # Emit step 2 result
        step2_msg = f"Layer 1 step 2/2: blade_query_k8s: {query_status_str}"
        if query_details_str:
            step2_msg += f" - {query_details_str}"
        if tracker:
            tracker.update(step2_msg, {"step": "blade_query_k8s", "status": query_status_str})

        # Degradation: when blade_status/blade_query_k8s report failure but we have
        # evidence of successful injection via kubectl exec (host blade binary broken),
        # try to find blade query k8s results in message history as fallback.
        if layer1_status == "failed" and experiment_uid and messages:
            # Fallback 1: find blade query k8s evidence from kubectl exec in message history
            fallback = _find_blade_query_in_messages(messages, experiment_uid)
            if fallback:
                layer1_status = "passed"
                layer1_details = (
                    "blade_status unavailable (host blade error), "
                    "but blade query k8s from kubectl exec confirmed injection success"
                )
            # Same-package scan primitive (was ``_was_kubectl_blade_injection_successful``
            # through the nodes thin wrapper pre-migration — the wrapper
            # delegates to this exact function, so behaviour is identical).
            elif scan_kubectl_blade_success(messages):
                # Fallback 2: kubectl exec injection output exists but no query evidence
                layer1_status = "skipped"
                layer1_details = (
                    f"blade_status/blade_query_k8s reported failure, "
                    f"but experiment_uid={experiment_uid} was extracted from kubectl exec injection output. "
                    f"Host blade binary may be incompatible."
                )

        # Fallback 3: Self-destructive fault detection.
        # Some faults (e.g. node-process stop containerd) destroy the very
        # communication channel Layer 1 uses to verify. The injection
        # succeeds, but blade_status/blade_query_k8s fail because the
        # target node is now unreachable. Detect this by checking if the
        # failure is a connectivity error AND the target node is NotReady
        # (which is observable via API server, not via the dead node).
        if layer1_status == "failed" and experiment_uid:
            _conn_keywords = (
                "connection refused", "connection timed out",
                "unreachable", "dial tcp", "i/o timeout",
            )
            _all_text = ((layer1_details or "") + (raw or "")).lower()
            if any(kw in _all_text for kw in _conn_keywords):
                try:
                    from chaos_agent.tools.kubectl import kubectl_read as _kro
                    _node_out = await _kro.ainvoke({
                        "subcommand": "get",
                        "v_args": "nodes",
                        "kubeconfig": kubeconfig,
                    })
                    _node_str = _node_out if isinstance(_node_out, str) else str(_node_out)
                    if "NotReady" in _node_str:
                        logger.info(
                            "Self-destructive fault detected: Layer 1 failed due to "
                            "connectivity loss, target node is NotReady — skipping "
                            "Layer 1 to let Layer 2 verify the actual fault effect"
                        )
                        layer1_status = "skipped"
                        layer1_details = (
                            "blade_status unreachable (node connectivity lost), "
                            "but target node is NotReady — consistent with a "
                            "self-destructive fault (e.g. containerd/kubelet stop). "
                            "Layer 2 will verify the actual fault effect."
                        )
                except Exception as _sde:
                    logger.debug(f"Self-destructive fault check failed: {_sde}")

        return Layer1Result(
            status=layer1_status,
            details=layer1_details,
            raw_output=raw,
            resource_statuses=q_resource_statuses,
            affected_count=q_affected_count,
            expired=layer1_expired,
        )

    except Exception as e:
        logger.error(f"Layer 1 verification failed: {e}")

        # Fallback 1: try to find blade query results from kubectl exec in message history.
        # When the host blade binary is unavailable, the LLM may have already
        # verified injection via kubectl exec blade query k8s.
        if experiment_uid and messages:
            fallback = _find_blade_query_in_messages(messages, experiment_uid)
            if fallback:
                return Layer1Result(
                    status="passed",
                    details="blade_status unavailable (host blade error), "
                            "but blade query k8s from kubectl exec confirmed injection success",
                    raw_output=fallback,
                )

        # Fallback 2: experiment_uid exists but tools failed — allow Layer 2 to proceed.
        # This happens when blade_create failed but kubectl exec injection succeeded,
        # and the host blade binary also cannot run blade_status.
        if experiment_uid:
            return Layer1Result(
                status="skipped",
                details=f"blade_status/blade_query_k8s unavailable ({e}), "
                        f"but experiment_uid={experiment_uid} was extracted from injection output",
                raw_output=str(e),
            )

        return Layer1Result(status="error", details=str(e), raw_output=str(e))
