"""Unified nesting budget (design doc 4.4).

ONE Budget object shared by every recursion point: command/backtick
substitution, ``sh -c`` unwrap layers, expansions nested inside double
quotes, parameter-expansion nesting, subshells. ANSI-C quoting (``$'...'``)
creates no nesting and never consumes.

Exhaustion semantics: ``budget_exhausted=True`` + a ``BUDGET_EXCEEDED``
issue with the pos where analysis stopped. The policy layer maps this to a
denial whose reason says "analysis capacity exceeded", NEVER "dangerous
structure found" — the two are distinguishable in feedback and audit.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_NESTING = 64  # single constant; legitimate probes nest ≤ 3 in practice


@dataclass
class Budget:
    remaining: int = MAX_NESTING
    exhausted: bool = False  # latched on the first refusal; bubbles to the
    # root ScriptFacts.budget_exhausted (single shared object per parse)

    def consume(self) -> bool:
        """Spend one nesting level. False means the cap is exceeded."""
        if self.remaining <= 0:
            self.exhausted = True
            return False
        self.remaining -= 1
        return True
