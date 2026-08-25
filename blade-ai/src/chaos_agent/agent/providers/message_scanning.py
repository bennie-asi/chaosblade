"""Stateless message-history scanning primitives shared by all FaultProviders.

Phase-14 lateral retirement (G1): these scans previously lived in
``providers/chaosblade/detection.py`` and were module-level imported by the
host_shell and k8s_native carriers — a cross-carrier dependency. They are
physically moved here, line-for-line, as a FLAT providers module so no
carrier subpackage owns them. The blade carrier's own output-format
knowledge (experiment-UID extraction, blade-evidence scans) stayed in the
chaosblade domain (``chaosblade/verify.py``); everything here is
carrier-agnostic — the *data* (which tool names / kubectl subcommands count
as a given carrier's injection) is declared on the provider class, and these
functions take that set as a parameter.

This module imports NOTHING from any carrier subpackage (zero carrier
dependency, verified by the phase-9/14 import guards) — only langchain
messages, the generic tools layer, and stdlib.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)


def build_tool_call_args_lookup(messages: list) -> dict:
    """Map ``tool_call_id`` → tool call args by scanning AIMessages.

    Lets a ToolMessage be cross-referenced back to the originating tool call
    arguments (e.g. ``subcommand`` / ``v_args``). Entries with missing/empty
    id are skipped.
    """
    lookup: dict[str, dict] = {}
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
                args = tc.get("args", {})
            else:
                tc_id = getattr(tc, "id", "")
                args = getattr(tc, "args", {})
            if tc_id:
                lookup[tc_id] = args
    return lookup


# Markers that mean a command NEVER reached the target (pre-execution
# rejection): guard reject, phase-1 read-only enforcement, unknown
# subcommand, arg validation error. Everything else — success, timeout,
# non-zero exit — counts as an ATTEMPTED action. This is the HIGH-TOLERANCE
# rule shared by the per-provider executed-action scans: a mutation that
# reached the cluster/host (even if it timed out or failed) counts as "done",
# so an ambiguous timeout no longer produces a false "missing step"
# (the original INCOMPLETE-INJECTION false-positive).
PRE_EXEC_REJECTION_MARKERS = (
    "[target_guard]",
    "phase1_readonly_violation",
    "does not accept subcommand",
    "validationerror",
    "validation error",
)


def reached_target(content: object) -> bool:
    """True unless the ToolMessage content is a pre-execution rejection."""
    low = (content if isinstance(content, str) else "").lower()
    return not any(m in low for m in PRE_EXEC_REJECTION_MARKERS)


# ---------------------------------------------------------------------------
# kubectl tool-domain vocabulary (phase-14 G2, design D2)
#
# These word lists and the fail-safe exec-mutation judgement are KUBECTL
# TOOL knowledge, not any single carrier's property: the k8s-native
# provider consumes them to attribute its OWN injections, while the blade
# domain consumes the same lists for its boundary judgements (embedded
# ``kubectl exec ... blade create`` delivery; native takeover after a
# failed blade_create). Both domains MUST see the identical
# vocabulary/judgement or their attributions drift apart — hence this
# neutral home. Previously class attributes / module functions on
# ``k8s_native.provider`` (physically moved here, phase-14 G2).
# ---------------------------------------------------------------------------

#: kubectl OBJECT-WRITE subcommands — the verb itself IS the mutation (the
#: API-server result is trustworthy evidence). Single source of truth for
#: the kubectl-native carrier's injection attribution; the target-guard
#: invariant (test_kubectl_verb_consistency) pins this set as a subset of
#: ``classifier.DESTRUCTIVE_KUBECTL_SUBS``.
KUBECTL_WRITE_SUBCOMMANDS = frozenset(
    {
        "scale",
        "patch",
        "cordon",
        "taint",
        "set",
        "delete",
        "drain",
        "label",
    }
)

#: kubectl COMMAND-MODE subcommands — these ENTER a pod/host to run a
#: command (``kubectl exec`` / ``kubectl debug``), so whether they mutate is
#: judged on the INNER command (:func:`exec_inner_command_mutates`), not
#: the verb. Kept separate from the object-write list so the object-write
#: invariant above is untouched.
KUBECTL_COMMAND_SUBCOMMANDS = frozenset({"exec", "debug"})


def exec_inner_command_mutates(v_args: str) -> bool:
    """True if a ``kubectl exec``/``debug`` inner command mutates state (an
    injection), False for a read-only probe.

    Fail-safe attribution: exec/debug default to MUTATING; only the shared
    read-only vocabulary (``tools.readonly.is_readonly_kubectl_exec`` — the
    single source shared with the guard-scope classifier and ``host_read``)
    is excluded. This inverts the former fault-family blacklist, which
    silently missed shell CPU loops (``while true``), ``/etc/hosts`` edits,
    ``dmsetup`` IO-error maps and ``nc`` port listeners — all real
    skill-case injections. Dual-use tools (iptables / ip / tc / systemctl /
    mount / dmesg) are judged at the ARGUMENT level there (``iptables -L``
    read, ``iptables -A`` mutating), so a novel injection shape is
    attributed by default rather than slipping through as "not an
    injection".

    Public since phase-7 T4 (originally as
    ``k8s_native.provider.exec_inner_command_mutates``): the issue-time
    attribution hook and the verifier-side reverse scans consult it as the
    carrier's owned classifier. Phase-14 G2: physically moved here — kubectl
    tool-domain knowledge, consumed identically by both carrier domains
    (attribution drift between them would mis-route recovery).
    """
    from chaos_agent.tools.readonly import is_readonly_kubectl_exec

    return not is_readonly_kubectl_exec(v_args)


def _host_native_call_is_readonly(args: object) -> bool:
    """True if a host-native carrier tool_call ran a READ-ONLY diagnostic.

    ``host_inject`` is the superset of ``host_read`` (it admits read-only
    diagnostics with ``skip_guard``), so a successful ``host_inject`` ToolMessage
    is NOT necessarily an injection. Mirror the content-aware attribution the
    kubectl-native scan uses: a read-only command is not an injection.
    """
    if not isinstance(args, dict):
        return False

    from chaos_agent.tools.readonly import (
        host_command_rejection_reason,
        is_readonly_argv,
    )

    command = args.get("command")
    if isinstance(command, str) and command.strip():
        # Face 8 (design 4.6): judge the raw string on structural facts,
        # not as a shlex token soup — a pipe/substitution an argv split
        # would render as inert words is real syntax here, and a ``watch``
        # payload is re-parsed the way watch runs it.
        return host_command_rejection_reason(command) is None
    # exec_host_command shape: binary + args list — no raw text exists, so
    # it falls to the argv judge (which re-parses ``watch`` payloads the
    # same way inside).
    binary = args.get("binary")
    if isinstance(binary, str) and binary:
        extra = args.get("args") or []
        argv = [binary] + [str(a) for a in extra]
        return is_readonly_argv(argv)
    return False


def scan_host_native_injection(messages: list, tool_names: frozenset[str]) -> bool:
    """True if a host-native command tool in ``tool_names`` ran successfully.

    Reverse-scans for the most recent ToolMessage whose name is one of the
    carrier's injection tools and whose content is not an ``Error:``. The
    caller (HostShellProvider) supplies its own ``inject_tool_names`` so this
    stays carrier-agnostic. A read-only diagnostic run through ``host_inject``
    (its host_read superset role) is content-aware EXCLUDED — it is not an
    injection.
    """
    lookup = build_tool_call_args_lookup(messages)
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") not in tool_names:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if content.startswith("Error:"):
            continue
        if _host_native_call_is_readonly(lookup.get(getattr(msg, "tool_call_id", ""), {})):
            continue
        return True
    return False


def scan_kubectl_injection_after_blade(
    messages: list,
    subcommands: set[str] | frozenset[str],
    *,
    command_subcommands: set[str] | frozenset[str] = frozenset(),
    is_mutating_command=None,
) -> bool:
    """True if a kubectl-native injection followed a ``blade_create`` attempt.

    Detects the kubectl-native alternative injection AFTER the last
    blade_create, so kubectl calls before blade_create (normal verification)
    don't count. Two attempt shapes are recognised:

    - **Object-write** — a ``subcommand`` in ``subcommands``
      (scale/patch/cordon/...) that SUCCEEDED. The verb itself IS the
      mutation and its result is trustworthy, so failed calls don't count.
    - **Command-mode** — a ``subcommand`` in ``command_subcommands``
      (``exec``/``debug``) whose inner command mutates (judgement delegated
      to ``is_mutating_command``, fed the raw ``v_args``). Keyed on the
      ATTEMPT (AIMessage tool_call), NOT the result: an exec-delivered fault
      can sever its own feedback channel, so its ToolMessage comes back as
      ``Error:`` — the forensic paradox (see
      :func:`scan_kubectl_mutation_attempted`) — and an error result must not
      disprove the injection. Without a callback, command-mode calls never
      count (a bare ``exec`` is not assumed mutating). An exec carrying a
      ``blade ... create`` command is EXCLUDED — that is a ChaosBlade
      delivery channel (the ``kubectl_exec`` method), not a kubectl-native
      injection, and its attribution belongs to
      :func:`scan_kubectl_blade_success` (chaosblade/verify.py).
    """
    lookup = build_tool_call_args_lookup(messages)

    last_blade_create_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "blade_create":
            last_blade_create_idx = i

    scan_command_mode = bool(command_subcommands) and is_mutating_command is not None

    for i, msg in enumerate(messages):
        if i <= last_blade_create_idx:
            continue
        if isinstance(msg, ToolMessage):
            if getattr(msg, "name", "") != "kubectl":
                continue
            tc_id = getattr(msg, "tool_call_id", "")
            if tc_id and tc_id in lookup:
                args = lookup[tc_id]
                subcommand = args.get("subcommand", "")
                if subcommand in subcommands:
                    content = msg.content or ""
                    if not content.startswith("Error:"):
                        return True
        elif scan_command_mode and isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict):
                    name = tc.get("name", "")
                    args = tc.get("args", {})
                else:
                    name = getattr(tc, "name", "")
                    args = getattr(tc, "args", {})
                if name != "kubectl" or not isinstance(args, dict):
                    continue
                if args.get("subcommand", "") not in command_subcommands:
                    continue
                v_args = args.get("v_args", "")
                if not isinstance(v_args, str):
                    continue
                # ChaosBlade delivered through exec is the kubectl_exec
                # method, not a kubectl-native injection — leave it to
                # scan_kubectl_blade_success (a FAILED blade-via-exec must
                # still read as "blade attempted and failed").
                if "blade" in v_args and "create" in v_args:
                    continue
                if is_mutating_command(v_args):
                    return True
    return False


def scan_kubectl_mutation_attempted(
    messages: list,
    write_subcommands: set[str] | frozenset[str],
    *,
    command_subcommands: set[str] | frozenset[str] = frozenset(),
    is_mutating_command=None,
) -> bool:
    """True if a kubectl mutating call was ATTEMPTED, keyed on the injection
    ATTEMPT (AIMessage tool_calls) — NOT on the tool RESULT.

    Attribution follows what was *launched*, not whether it returned success: a
    network-drop injection severs the ``kubectl exec`` connection, so its
    ToolMessage comes back as ``Error:`` (timeout). Keying on a successful
    result would misread a *successful* injection as none — the forensic
    paradox (the more effective the network fault, the more its own carrier
    command looks like a failure). We therefore scan AIMessage tool_calls for a
    ``kubectl`` call and deliberately do NOT inspect the ToolMessage outcome.
    Whether the fault actually took effect is Layer 2's job; this only
    attributes the carrier.

    Two attempt shapes are recognised, so a read-only ``kubectl exec`` is NOT
    mistaken for an injection:

    - **Object-write** — a ``subcommand`` in ``write_subcommands``
      (scale/patch/cordon/...). The verb itself IS the mutation, so no command
      inspection is needed.
    - **Command-mode** — a ``subcommand`` in ``command_subcommands``
      (``exec``/``debug``) is only an injection when its inner command actually
      mutates. That judgement is delegated to the caller-injected
      ``is_mutating_command`` (fed the raw ``v_args``) so this scan stays
      carrier-agnostic and reuses the tool layer's structured read/mutate
      classification instead of parsing command text here. Without a callback,
      command-mode calls never count (a bare ``exec`` is not assumed mutating).

    All subcommand sets are caller-injected — the provider owns its own
    vocabulary — keeping this scan carrier-agnostic.
    """
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {})
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {})
            if name != "kubectl" or not isinstance(args, dict):
                continue
            subcommand = args.get("subcommand", "")
            if subcommand in write_subcommands:
                return True
            if (
                subcommand in command_subcommands
                and is_mutating_command is not None
            ):
                v_args = args.get("v_args", "")
                if isinstance(v_args, str) and is_mutating_command(v_args):
                    return True
    return False


def scan_native_issue_disproven(
    messages: list,
    write_subcommands: set[str] | frozenset[str],
    *,
    command_subcommands: set[str] | frozenset[str] = frozenset(),
    is_mutating_command=None,
) -> bool:
    """True when the MOST RECENT kubectl-native attempt's result explicitly
    DISPROVES the mutation — an explicit counter-evidence scan for revoking
    an issue-time (channel A) attribution whose command provably failed —
    and ONLY when no object-write in the epoch ever landed (a single landed
    write confirms the attribution irrevocably; see the confirmation guard
    below).

    The scan stops at the LATEST native attempt of ANY shape and judges it:

    - **Object-write** (``subcommand`` in ``write_subcommands``): the verb
      itself is the mutation and the API-server result is trustworthy — an
      ``Error:`` result proves the write never landed (returns True).
    - **Command-mode** (``subcommand`` in ``command_subcommands`` with a
      mutating inner command): never judgeable — its error results may be
      the fault severing its own feedback channel (the forensic paradox,
      see :func:`scan_kubectl_mutation_attempted`), and a later command-mode
      attempt must not be revoked on an EARLIER failed object-write.
      Returns False.
    - A ``blade ... create`` carried through exec is the ``kubectl_exec``
      method, not a kubectl-native attempt — skipped, scanning continues.

    The judged attempt's result must be PRESENT: a missing ToolMessage means
    the call is still pending (issue-time attribution in the same turn, or a
    severed channel), and absence of result is never counter-evidence.

    RESULT-BORN CONFIRMATION GUARD: if ANY object-write attempt in the epoch
    has a present, non-error result, the attribution is CONFIRMED — a mutation
    provably landed on the cluster. A LATER failed write (a multi-step skill's
    second step failing, or a self-undo retry failing) must not revoke it:
    revocation would orphan the still-live fault from the successful write.
    Counter-evidence only exists while NO write ever landed."""
    results = {
        getattr(msg, "tool_call_id", ""): msg
        for msg in messages
        if isinstance(msg, ToolMessage)
    }
    # Confirmation pre-pass: any landed object-write makes the attribution
    # irrevocable within this epoch (see the guard paragraph above).
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {})
                tc_id = tc.get("id", "")
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {})
                tc_id = getattr(tc, "id", "")
            if name != "kubectl" or not isinstance(args, dict):
                continue
            if args.get("subcommand", "") not in write_subcommands:
                continue
            result_msg = results.get(tc_id or "")
            if result_msg is None:
                continue
            content = result_msg.content if isinstance(
                result_msg.content, str
            ) else str(result_msg.content)
            if not content.startswith("Error:"):
                return False  # a write landed — attribution confirmed
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in reversed(getattr(msg, "tool_calls", None) or []):
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {})
                tc_id = tc.get("id", "")
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {})
                tc_id = getattr(tc, "id", "")
            if name != "kubectl" or not isinstance(args, dict):
                continue
            subcommand = args.get("subcommand", "")
            if subcommand in write_subcommands:
                # Latest attempt found — judge its result only.
                result_msg = results.get(tc_id or "")
                if result_msg is None:
                    return False  # pending / severed — never counter-evidence
                content = result_msg.content if isinstance(
                    result_msg.content, str
                ) else str(result_msg.content)
                return content.startswith("Error:")
            if (
                subcommand in command_subcommands
                and is_mutating_command is not None
            ):
                v_args = args.get("v_args", "")
                if isinstance(v_args, str) and (
                    "blade" in v_args and "create" in v_args
                ):
                    continue  # kubectl_exec method — not a native attempt
                if isinstance(v_args, str) and is_mutating_command(v_args):
                    return False  # command-mode: paradox — never judgeable
    return False


def scan_kubectl_mutation_index(
    messages: list,
    write_subcommands: set[str] | frozenset[str],
    *,
    command_subcommands: set[str] | frozenset[str] = frozenset(),
    is_mutating_command=None,
) -> int:
    """Index of the most-recent AIMessage carrying a mutating kubectl attempt.

    Recency-returning companion to :func:`scan_kubectl_mutation_attempted`
    (same attribution rules — keyed on the ATTEMPT, not the tool result).
    Returns the message index, or ``-1`` when no mutating kubectl call was
    attempted.
    """
    last = -1
    for i, msg in enumerate(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {})
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {})
            if name != "kubectl" or not isinstance(args, dict):
                continue
            subcommand = args.get("subcommand", "")
            if subcommand in write_subcommands:
                last = i
                break
            if subcommand in command_subcommands and is_mutating_command is not None:
                v_args = args.get("v_args", "")
                if isinstance(v_args, str) and is_mutating_command(v_args):
                    last = i
                    break
    return last


def scan_host_native_index(messages: list, tool_names: frozenset[str]) -> int:
    """Index of the most-recent successful host-native carrier ToolMessage.

    Recency-returning companion to :func:`scan_host_native_injection`
    (same content-aware rule — a read-only ``host_inject`` diagnostic is not an
    injection). Returns ``-1`` when no successful host-native command is
    attested.
    """
    lookup = build_tool_call_args_lookup(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") not in tool_names:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if content.startswith("Error:"):
            continue
        if _host_native_call_is_readonly(lookup.get(getattr(msg, "tool_call_id", ""), {})):
            continue
        return i
    return -1


__all__ = [
    "PRE_EXEC_REJECTION_MARKERS",
    "build_tool_call_args_lookup",
    "reached_target",
    "scan_host_native_injection",
    "scan_host_native_index",
    "scan_kubectl_injection_after_blade",
    "scan_kubectl_mutation_attempted",
    "scan_kubectl_mutation_index",
    "scan_native_issue_disproven",
]
