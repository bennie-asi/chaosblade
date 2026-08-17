"""Read-only phase screening shared by the verification loops.

Both verification phases observe with a read-only tool surface and must
never inject or repair: the inject verifier judges whether the fault
landed, recover Layer 2 judges whether the undo landed. Accident
task-a77f7663 (recover side) showed the failure mode this screen
closes: the verifier deleted a pod to "fix" a residual it should have
reported.

Single shared implementation so the inject verifier and recover Layer 2
cannot drift:

  - verdicts come from the target_guard classifier (no hand-rolled verb
    lists); classifier failures fail open per call so a classification
    bug cannot wedge a verification loop;
  - the capability-probe exception (``kubectl_read debug``) is shared
    with ``phase1_screener`` — verification legitimately creates the
    ephemeral, self-gated, auto-cleaned probe pod to inspect host-level
    state.

The classification core (:func:`find_readonly_violations`) is the
verdict source for the screener edge nodes in :mod:`_phase_screener`,
which enforce the read-only discipline on the verification phases in
the phase1/tool_screener graph-edge paradigm (fabricated ToolMessage
pairing, whole-batch retry).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def find_readonly_violations(tool_calls) -> list[tuple]:
    """Classify *tool_calls* against the read-only phase discipline.

    Pure classification — no message mutation. Returns one
    ``(tool_name, tool_call_id, detail, probe_reason, effective)`` tuple
    per offending call (``detail`` is the raw command when the classifier
    surfaced one; ``probe_reason`` is the judge's actual cause when the
    refusal is an exec whose inner command failed the read-only probe
    test — empty otherwise; ``effective`` is the full verdict object so
    the renderer can apply the shared truth-first chain —
    ``_guard_rejection.read_only_rejection_reason`` — instead of
    re-inventing the cause from the two string fields. Dropping it is
    how the recorded ``reject_detail`` (host-escape primitive, banned
    subcommand, ...) used to be lost on this path).
    Verdict rules, shared with every caller:

      - verdicts come from the target_guard classifier (no hand-rolled
        verb lists); classifier failures fail OPEN per call so a
        classification bug cannot wedge a verification loop;
      - the capability-probe exception (``kubectl_read debug``) is
        shared with ``phase1_screener`` — verification legitimately
        creates the ephemeral, self-gated, auto-cleaned probe pod to
        inspect host-level state.
    """
    from chaos_agent.agent.nodes.execute.react_helpers import (
        extract_tool_call_fields,
    )
    from chaos_agent.agent.nodes.planning.phase1_screener import (
        is_capability_probe_call,
    )
    from chaos_agent.agent.target_guard import SCOPE_READONLY, infer_effective_target

    violations: list[tuple] = []
    for tc in tool_calls or []:
        name, args = extract_tool_call_fields(tc)
        if is_capability_probe_call(name, args):
            continue
        try:
            effective = infer_effective_target(name, args)
        except Exception as exc:
            logger.warning(
                "Read-only phase screen: classifier failed for %s: %s",
                name or "<unknown>", exc,
            )
            continue
        if effective.scope != SCOPE_READONLY:
            tc_id = (
                tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            )
            violations.append((
                name or "<unknown call>",
                tc_id,
                effective.raw_command or name or "<unknown call>",
                effective.readonly_probe_reason,
                effective,
            ))
    return violations


__all__ = ["find_readonly_violations"]
