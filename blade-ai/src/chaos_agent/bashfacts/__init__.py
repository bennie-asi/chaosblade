"""bashfacts — structural facts for bash command strings (fact layer).

Public API (design doc 4.3 / 4.6):
  - ``parse_script(source, *, budget=None)`` — full structural parse into
    ScriptFacts. NEVER raises; any failure is a ParseIssue.
  - ``has_shell_structure(source)`` — prescan gate: single O(n) pass over
    the 13 structural characters. No hit → the caller takes the plain
    literal fast path (equivalent to today's no-metachar argv judging).
  - ``unwrap_sh_c(words, *, budget)`` — the unified shell-wrapper unwrapper
    replacing the three sh -c peel copies (readonly / carriers /
    classifier). Peel layers count into the Budget.

Iron rules (4.2): absolute offsets, fully materialized parts, lossless
reconstruction. This package is a stdlib-only leaf: it imports nothing from
chaos_agent, so both ``agent`` and ``tools`` may depend on it.
"""
from __future__ import annotations

from .budget import MAX_NESTING, Budget
from .facts import (
    CommandFacts,
    IssueKind,
    ParseIssue,
    PartKind,
    RedirectFacts,
    ScriptFacts,
    Segment,
    WordFacts,
    WordPart,
    iter_parts,
)
from .parser import parse_script

__all__ = [
    "MAX_NESTING", "Budget",
    "CommandFacts", "IssueKind", "ParseIssue", "PartKind", "RedirectFacts",
    "ScriptFacts", "Segment", "WordFacts", "WordPart", "iter_parts",
    "parse_script", "has_shell_structure", "unwrap_sh_c",
    "unwrap_shell_invocation", "word_token",
]

# prescan gate character set (4.3): backslash, quotes, dollar, backtick,
# angle brackets, pipe, semicolon, ampersand, newline, parentheses — parens
# included because an unquoted bare paren is a bash syntax error outside
# case/array/function-def syntax (which the subset does not support), and
# the fast path must not wave it through (found by the Phase-2 fuzz referee:
# ``bash -n`` rejects ``cat )`` that the paren-blind prescan allowed).
_PRESCAN_CHARS = frozenset("\\'\"$`<>|;&\n()")

_SHELL_NAMES = frozenset({"sh", "bash", "ash", "dash"})


def has_shell_structure(source: str) -> bool:
    """Single-pass prescan: True when any structural character is present.

    90%+ of probe commands on the guard hot path exit here (unbash
    ``hasEmbeddedWordStructure`` idea, translated to the guard context).
    """
    return any(ch in _PRESCAN_CHARS for ch in source)


def unwrap_sh_c(words: list[WordFacts], *,
                budget: Budget | None = None) -> ScriptFacts | None:
    """Unified ``sh -c`` unwrapper (design 4.6, P6 convergence).

    Recognizes EXACTLY ``sh|bash|ash|dash -c <script-word>`` — three plain
    literal words, nothing else. Flags before ``-c`` are refused on
    purpose: ``bash --init-file /tmp/x -c id`` executes /tmp/x BEFORE the
    script, so unwrapping to the script alone would hide half the command
    (both legacy peel copies had exactly this hole; the 1120-command
    corpus shows zero real flag-before-``-c`` hits, so strictness costs
    nothing). Extra operands after the script word are refused too — they
    are $0/args, a shape the policy layer should see unwrapped. Returns
    None on any other shape (the caller keeps its existing fallback
    semantics — that fallback is policy, not fact).

    Deviation from the draft signature (``-> list[WordFacts]``): a script
    payload may hold MULTIPLE segments (``sh -c 'ls /; df -h'`` is a
    legitimate carriers probe), and a bare word list cannot express
    operators. The ScriptFacts itself is the lossless form; policy code
    walks its segments. The nested script's source is the script word's
    decoded VALUE (quotes/escapes resolved by bash before sh sees it) —
    offsets inside it index that value, the same documented exception
    class as decoded backtick bodies (iron rule 1).
    """
    if len(words) != 3 or not all(w.is_plain_literal for w in words):
        return None
    name = words[0].value.split("/")[-1] if words[0].value else ""
    if name not in _SHELL_NAMES or words[1].value != "-c":
        return None
    script_text = words[2].value
    if script_text is None:  # defensive: is_plain_literal implies a value
        return None
    budget = budget if budget is not None else Budget()
    if not budget.consume():
        return ScriptFacts(
            script_text, (), (),
            (ParseIssue("nesting budget exceeded at sh -c unwrap",
                        words[2].pos, IssueKind.BUDGET_EXCEEDED),),
            True)
    return parse_script(script_text, budget=budget)


def unwrap_shell_invocation(words: list[WordFacts], *,
                            budget: Budget | None = None) -> ScriptFacts | None:
    """Loose ``sh -c`` peel for PEEK routing (design 4.6, face 4).

    ``unwrap_sh_c`` is strict (exactly three words): right for JUDGING,
    where refusing to peel fails closed (an unpeeled ``sh`` is an unknown
    binary). The classifier's escape-primitive peek is ROUTING, not
    judging — there the same refusal fails OPEN: ``sh -x -c 'chroot /host
    …'`` left unpeeled routes to pod scope and the host escape is never
    attributed. This variant mirrors the legacy index-based peel: the head
    word's BASENAME is a shell name (so ``/usr/bin/sh`` peels too — the
    legacy full-path list missed it; a registered tightening), the script
    is the word after the first plain-literal ``-c``, and extra operands
    are ignored (they are ``$0``/args to the script, irrelevant to what
    the script HEAD is).

    Returns None whenever no confident peel exists (non-shell head, no
    ``-c``, no script word, or the script word carries structure so its
    text cannot be trusted as the delivered argv value) — the caller keeps
    its unpeeled fallback, exactly like the legacy ``except ValueError``
    path.
    """
    if not words or not words[0].is_plain_literal:
        return None
    name = (words[0].value or "").split("/")[-1]
    if name not in _SHELL_NAMES:
        return None
    c_idx = None
    for i in range(1, len(words)):
        if words[i].is_plain_literal and words[i].value == "-c":
            c_idx = i
            break
    if c_idx is None or c_idx + 1 >= len(words):
        return None
    script_text = words[c_idx + 1].value
    if not script_text or not script_text.strip():
        return None  # legacy: shlex.split('') == [] → keep unpeeled
    budget = budget if budget is not None else Budget()
    if not budget.consume():
        return ScriptFacts(
            script_text, (), (),
            (ParseIssue("nesting budget exceeded at shell-invocation unwrap",
                        words[c_idx + 1].pos, IssueKind.BUDGET_EXCEEDED),),
            True)
    return parse_script(script_text, budget=budget)


def word_token(word: WordFacts) -> str:
    """Render a word to its argv-token form (design 4.6 value discipline).

    Literal-class words dequote to their value; a word carrying structure
    keeps the structure's raw source text (``$(id)`` stays ``$(id)``), so a
    flag table can never match a partially-dequoted shape and a
    partially-expanded word can never impersonate a value it is not. This
    is the single renderer every facts-based policy layer (readonly,
    carriers, classifier, detection) uses to reach argv semantics.
    """
    value = word.value
    if value is not None:
        return value
    return "".join(_part_token(p) for p in word.parts)


def _part_token(part: WordPart) -> str:
    if part.kind in (
            PartKind.LITERAL, PartKind.SINGLE_QUOTED, PartKind.ANSI_C_QUOTED):
        return part.value
    if part.kind in (PartKind.DOUBLE_QUOTED, PartKind.LOCALE_QUOTED):
        return "".join(_part_token(c) for c in part.children)
    return part.text
