"""5.3 fuzz (Phase-1 half): 100k generated inputs — parse_script must never
raise and never hang; budget-exhausted outputs must still be walkable.

Plus the Phase-1 bench: p99 parse latency over the real corpus < 1 ms
(the guard sits on the tool-call hot path).
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from chaos_agent.bashfacts import parse_script

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "guard_command_corpus.json"
CORPUS = [row["cmd"] for row in json.loads(CORPUS_PATH.read_text())["commands"]]

# alphabet weighted toward structural characters so the fuzzer lives on the
# interesting paths instead of rediscovering plain words
_ALPHABET = (
    "aaaaeeeecckkllsstt"          # common letters
    " /._-0123456789"             # word filler
    "''\"\"``$$$$()"              # quotes & subst openers
    "\\\\;|&<>\n"                 # escapes & operators
    "{}[]#!*?~"                   # specials
)
_FRAGMENTS = ["$(", ")", "$(", "`", "`", "'", "'", '"', '"', "${", "}",
              "$((", "))", "<(", ">(", "<<EOF\n", "EOF", "2>", ">&2",
              "<>", "$(echo # c\n)",  # D9 op / D10 comment-in-subst paths
              "sh -c ", "echo ", "cat ", " ; ", " && ", " | ", "\\$",
              "$'", "\\n", "'", "watch ", "awk ", "{print ", "$1}", " "]


def _gen(rng: random.Random) -> str:
    if rng.random() < 0.01:
        # rare deep-nesting draw so the budget path is fuzzed too
        depth = rng.randint(55, 200)
        close = ")" * (depth if rng.random() < 0.8 else rng.randint(0, depth))
        return "echo " + "$(" * depth + "id" + close
    n = rng.randint(1, 60)
    if rng.random() < 0.5:
        return "".join(rng.choice(_ALPHABET) for _ in range(n))
    return "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(1, 12)))


def test_fuzz_100k_no_exception_no_hang():
    rng = random.Random(20260817)
    t0 = time.monotonic()
    budget_hits = 0
    for _ in range(100_000):
        facts = parse_script(_gen(rng))  # must never raise
        # every produced tree must remain walkable (no cycles, no lazy bombs)
        sum(1 for _ in facts.walk_scripts())
        budget_hits += facts.budget_exhausted
    elapsed = time.monotonic() - t0
    # generous ceiling: the run must complete, proving no hang; typical is
    # an order of magnitude below this
    assert elapsed < 120, f"fuzz took {elapsed:.1f}s"
    assert budget_hits > 0, "fuzzer never reached the nesting budget?"


def test_fuzz_deep_nesting_tail():
    """Deliberately hostile deep inputs beyond the alphabet fuzzer."""
    for depth in (1, 7, 63, 64, 65, 128, 500):
        facts = parse_script("echo " + "$(" * depth + "id" + ")" * depth)
        assert facts.budget_exhausted == (depth > 64)
        sum(1 for _ in facts.walk_scripts())
    # unbalanced tails must not wedge the scanner
    for src in ("$(" * 200, "`" * 200, "'" * 201, "\\" * 101):
        parse_script(src)


def test_bench_p99_under_1ms():
    samples = []
    for cmd in CORPUS:
        t0 = time.perf_counter_ns()
        parse_script(cmd)
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    p50 = samples[len(samples) // 2] / 1e6
    p99 = samples[int(len(samples) * 0.99)] / 1e6
    assert p99 < 1.0, f"p99 {p99:.3f}ms (p50 {p50:.3f}ms) exceeds the 1ms budget"
