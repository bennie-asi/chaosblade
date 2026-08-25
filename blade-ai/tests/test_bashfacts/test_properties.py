"""5.2 property tests — the three invariants, locked over the Phase-0 corpus
(1120 real commands) plus a targeted structural generator.

  1. LOSSLESS RECONSTRUCTION — every word's parts rejoin to its exact source
     interval; operator gaps between segments carry exactly the recorded
     operator; no byte of a clean parse is silently swallowed (coverage).
  2. TRAVERSAL COMPLETENESS — walk_scripts() enumerates every subst/subshell
     exactly once (equality when the budget held); every nested script's
     issues bubble to the root.
  3. LITERAL CONSISTENCY (fast-path equivalence) — words with no structural
     character at all decode to the same value shlex produces. Quoted/escaped
     words are outside the fast path: there OUR decoding is the bash truth
     and shlex is known-divergent (it keeps ``\\$`` inside double quotes),
     so it is not the reference there. Heredoc bodies are not argv words.

The invariants were first probed against the corpus in .codex_work
(probe_invariants.py) — two genuine parser bugs were found and fixed that way
(DQ closing quote leaked as a literal child; segment span missed redirect
targets). This file locks the fixed behavior.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from chaos_agent.bashfacts import iter_parts, parse_script
from chaos_agent.bashfacts.facts import CommandFacts, PartKind, ScriptFacts

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "guard_command_corpus.json"
CORPUS = [row["cmd"] for row in json.loads(CORPUS_PATH.read_text())["commands"]]

SUBST_KINDS = {PartKind.COMMAND_SUBST, PartKind.BACKTICK_SUBST,
               PartKind.PROCESS_SUBST}
STRUCTURAL_CHARS = set("\\'\"$`<>|;&\n")
_WS = {ord(c): None for c in " \t\r"}

# targeted generator: structures the corpus rarely or never exercises
GENERATED = [
    "( ls / ) ; ( echo ok )",
    "a | b | c && d || e ; f & g",
    "echo $(echo $(echo $(echo deep)))",
    "echo `echo \\`echo nested\\``",
    "diff <(ls /) >(cat > /dev/null)",
    "cat <<-EOF\n\t$(id)\n\tEOF",
    "echo $(( 1 + $(echo 2) )) $[3]",
    "echo x$HOME'y'\"$PATH\"z",
    "echo $'\\x3b\\n\\\\' end",
    "sh -c 'echo \"sh -c \\\"echo deep\\\"\"'",
    "printf '%s' 'a' \"b\" c\\ d e''f",
    "echo >&2 2>&1 1>&2 <&0 &> /dev/null &>>/log",
    "cmd \\\n  --flag value",          # line continuation
    "echo $(echo \"$(echo '$HOME')\")",  # mixed nesting with quotes
    "true &&\n echo continued",        # operator + newline gap
    "echo $! $? $$ $1 ${10} ${x:-d}",  # param expansion shapes
]


def command_words(cmd: CommandFacts):
    if cmd.name is not None:
        yield cmd.name
    yield from cmd.args
    for r in cmd.redirects:
        if r.target is not None:
            yield r.target
        if r.body is not None:
            yield r.body


def all_words(root: ScriptFacts):
    """Every materialized word, each paired with the script whose ``source``
    its offsets index (the decoded-backtick exception is carried along)."""
    for s in root.walk_scripts():
        for seg in s.segments:
            if isinstance(seg.command, ScriptFacts):
                continue
            for w in command_words(seg.command):
                yield s, w


def has_heredoc(root: ScriptFacts) -> bool:
    return any(
        r.operator in ("<<", "<<-")
        for s in root.walk_scripts() for seg in s.segments
        if not isinstance(seg.command, ScriptFacts)
        for r in seg.command.redirects)


# ---------------------------------------------------------------- invariant 1

def _check_lossless(root: ScriptFacts) -> None:
    for s, w in all_words(root):
        assert s.source[w.pos:w.end] == w.text, (w.text, s.source)
        assert "".join(p.text for p in w.parts) == w.text, w.text
        for p in iter_parts(w):
            # part offsets index the same source the word's offsets index;
            # the decoded backtick BODY is the documented exception and its
            # parts index the decoded source (they live under part.script)
            assert s.source[p.pos:p.end] == p.text, (p.text, s.source)


def _check_operator_alignment(s: ScriptFacts) -> None:
    segs = s.segments
    assert len(s.operators) == max(0, len(segs) - 1), s.source
    for i, op in enumerate(s.operators):
        a, b = segs[i].command, segs[i + 1].command
        if isinstance(a, ScriptFacts) or isinstance(b, ScriptFacts):
            continue  # subshell segments carry no pos/end (facts model)
        gap = s.source[a.end:b.pos]
        compact = gap.translate(_WS)
        if op == "\n":
            assert compact and set(compact) == {"\n"}, (op, gap)
        else:
            # line continuations may add newlines around the operator
            assert compact.replace("\n", "") == op, (op, gap)
    for seg in segs:
        if isinstance(seg.command, ScriptFacts):
            _check_operator_alignment(seg.command)


def _check_coverage(root: ScriptFacts) -> None:
    """No byte of a clean parse is silently dropped: every char of the ROOT
    source is inside a word/redirect span or is whitespace/operator
    punctuation. Spans are collected across the whole walked tree (nested
    subst/subshell bodies index the root source); decoded-backtick bodies
    index their own decoded source and are skipped — their characters are
    already inside the enclosing word's span."""
    covered: list[tuple[int, int]] = []
    for s in root.walk_scripts():
        if s.source is not root.source:
            continue  # decoded backtick body (documented exception)
        for seg in s.segments:
            if isinstance(seg.command, ScriptFacts):
                continue  # subshell parens are allowed gap punctuation
            for w in command_words(seg.command):
                covered.append((w.pos, w.end))
            for r in seg.command.redirects:
                covered.append((r.pos, r.end))
    allowed = set(" \t\r\n|;&()")
    for i, ch in enumerate(root.source):
        if ch in allowed or any(a <= i < b for a, b in covered):
            continue
        pytest.fail(f"coverage hole at {i} {ch!r} in {root.source!r}")


# ---------------------------------------------------------------- invariant 2

def _check_walk_completeness(root: ScriptFacts) -> None:
    scripts = list(root.walk_scripts())
    subst_parts = 0
    subshells = 0
    for s in scripts:
        for seg in s.segments:
            if isinstance(seg.command, ScriptFacts):
                subshells += 1
                continue
            for w in command_words(seg.command):
                for p in iter_parts(w):
                    if p.kind in SUBST_KINDS:
                        subst_parts += 1
    walked = len(scripts) - 1  # exclude the root itself
    expect = subst_parts + subshells
    budget_hit = any(e.kind.value == "budget_exceeded" for e in root.errors)
    if budget_hit:
        # budget exhaustion may skip deeper scripts — never over-walk
        assert walked <= expect, root.source
    else:
        assert walked == expect, (root.source, walked, expect)
    for s in scripts:
        if s is root:
            continue
        for e in s.errors:
            assert e in root.errors, (e, s.source)


# ---------------------------------------------------------------- invariant 3

def _check_literal_fast_path(root: ScriptFacts) -> None:
    for s in root.walk_scripts():
        for seg in s.segments:
            c = seg.command
            if isinstance(c, ScriptFacts):
                continue
            words = ([c.name] if c.name else []) + list(c.args)
            for w in words:
                if not w.is_plain_literal:
                    continue
                if any(ch in STRUCTURAL_CHARS for ch in w.text):
                    continue  # not a fast-path word — shlex diverges there
                ref = shlex.split(w.text, posix=True)
                assert len(ref) == 1 and ref[0] == w.value, (w.text, w.value, ref)


# ------------------------------------------------------------------- drivers

@pytest.mark.parametrize("cmd", CORPUS,
                         ids=[f"corpus-{i}" for i in range(len(CORPUS))])
def test_invariants_corpus(cmd: str) -> None:
    root = parse_script(cmd)
    _check_lossless(root)
    _check_walk_completeness(root)
    _check_literal_fast_path(root)
    if not root.errors:
        _check_operator_alignment(root)
        if not has_heredoc(root):
            _check_coverage(root)  # heredoc body layout is exempt (documented)


@pytest.mark.parametrize("cmd", GENERATED)
def test_invariants_generated(cmd: str) -> None:
    root = parse_script(cmd)
    _check_lossless(root)
    _check_walk_completeness(root)
    _check_literal_fast_path(root)
    if not root.errors:
        _check_operator_alignment(root)
        if not has_heredoc(root):
            _check_coverage(root)


def test_parse_never_raises_on_adversarial_bytes() -> None:
    """The public contract: parse_script NEVER raises (4.5)."""
    nasty = ["", " ", "'", "\"", "`", "$(", "${", "$((", "<(", ";", "&&",
             "\\", "'\\", "\"\\", "$(('", "((((", "))))", "<<", "<<EOF",
             ">>>>", "2>", "cat '", 'echo "', "$'\\x", "\n\n\n", "|", "|&"]
    for src in nasty:
        facts = parse_script(src)  # must not raise
        assert isinstance(facts, ScriptFacts)
