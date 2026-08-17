"""Shared truthful-rejection rendering for the read-only phase screeners.

Standing principle (polished over many guard-feedback rounds — c76d06e accurate
propagation, c2f31bc reason/suggestion pairing): whatever the verdict layer
actually computed is what the model sees. A screener may add PHASE framing
(duty, forward path, anti-bypass floor) around it, but must never re-invent
the CAUSE from the scope word alone — the generic templates that did so
("call would mutate pod resources (classifier verdict: destructive)") named
neither the offending token nor a compliant shape, and read as "all exec is
blocked", pushing the model into over-generalisation (task inject-a9ea4da7).

Single renderer so every read-only rejection surface (phase1_screener,
_phase_screener factory) consumes the verdict the same way: new surfaces
built on top inherit truthful rendering by construction instead of each
hand-writing its own wording and regressing the principle.
"""

from __future__ import annotations

from chaos_agent.agent.target_guard.classifier import (
    SCOPE_BANNED,
    SCOPE_UNKNOWN,
    EffectiveTarget,
)
from chaos_agent.tools.readonly import READONLY_PROBE_FIX_HINT

__all__ = ["read_only_rejection_reason", "is_malformed_probe", "scope_floor_note"]


def is_malformed_probe(effective: EffectiveTarget) -> bool:
    """True when the refusal is a SHAPE problem, not a mutation intent.

    ``readonly_probe_reason`` is populated only by the classifier's exec
    branch when the inner command failed the read-only probe test — the
    command may be a malformed probe (shell control operators, unknown
    binary) rather than a deliberate mutation. The two cases need different
    framing: a malformed probe keeps its exploration space open ("reshape
    and retry"), a genuine mutation hits the Phase boundary floor.
    """
    return bool(effective.readonly_probe_reason)


def read_only_rejection_reason(effective: EffectiveTarget) -> tuple[str, str]:
    """Return ``(reason, suggestion)`` for a non-READONLY verdict, truth-first.

    Priority:
      1. ``reject_detail`` / ``reject_suggestion`` — the origin recorded an
         explicit cause (banned subcommand, unresolved escape carrier, ...);
         render it verbatim.
      2. ``readonly_probe_reason`` — the exec's inner command failed the
         read-only probe test; state the judge's actual cause and pair it
         with the shared compliant-shape hint.
      3. ``raw_command`` — no explicit cause, but the command itself is the
         most truthful thing we hold; name it with the scope it would touch.
      4. Scope word only — last resort; nothing better was recorded.

    The suggestion half follows the pairing rule (reason and fix must point
    at the same thing): it is populated ONLY when a compliant form actually
    exists — probe shape for a malformed probe, the origin's suggestion for
    an explicit detail. A genuine mutation in a read-only phase has no
    compliant reshape HERE, so it gets an empty suggestion rather than a
    fake fix path.
    """
    if effective.reject_detail:
        return effective.reject_detail, effective.reject_suggestion

    if effective.readonly_probe_reason:
        return (
            "the exec's inner command is not a valid read-only probe: "
            f"{effective.readonly_probe_reason}",
            READONLY_PROBE_FIX_HINT,
        )

    if effective.raw_command:
        return (
            f"the call ({effective.raw_command}) would mutate "
            f"{effective.scope} resources",
            "",
        )

    return f"the call would mutate {effective.scope} resources", ""


def scope_floor_note(scope: str) -> str:
    """One-line anti-bypass floor, scoped to the verdict class.

    BANNED and UNKNOWN verdicts are hard floors (no reshape of THIS call can
    pass); a targeted mutation is blocked by the PHASE boundary, not by shape
    — the floor must say which, or the model misreads a phase rule as a
    capability rule and gives up on paths that are still open.
    """
    if scope in (SCOPE_BANNED, SCOPE_UNKNOWN):
        return (
            "DO NOT retry with `kubectl exec ... blade create` or any other "
            "equivalent path — all mutation paths are blocked here by the "
            "same classifier."
        )
    return (
        "Fault INJECTION (blade create/destroy, kubectl delete/patch/..., "
        "exec payloads that mutate) is blocked in this read-only phase by "
        "runtime enforcement and is bound automatically in Phase 2 after "
        "your plan is approved."
    )
