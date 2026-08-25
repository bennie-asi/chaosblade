"""Phase 1 deliverable: LLM eval harness (T1.14 / A9, roadmap 9.1.4).

Real-model evaluation over the SAME golden corpus the PR gate runs in
mock mode (tests/golden/scenarios/*.yaml): per-scenario multi-run
sampling, a win-rate matrix report, and report-vs-report diff. Only
``intent`` scenarios are driven — safety/recover surfaces never touch
the LLM (mock gate already pins them; paying tokens there proves
nothing).

Modes:
  run [flags]    drive every intent scenario through golden_runner in
                 mode="real" (production factory.make_llm + the factory
                 tool advertisement, mocked environment) ``--runs``
                 times, print the win-rate matrix, write a JSON report
                 (git sha + model + per-run failures/durations).
  diff OLD NEW   compare two run reports → per-scenario win-rate delta
                 (regressed / improved / new / removed), exit 1 on any
                 regression — the adversarial check for "the new prompt
                 or model fixed A but broke B".

Exit codes (run mode): 0 only when every scenario's win rate ≥
--min-win-rate (default 1.0 — one flaky scenario reddens the lane);
1 on threshold breach; 2 on preflight failure (missing API key,
empty corpus). A missing key fails fast BEFORE any LLM call.

Environment: same knobs the production agent reads —
BLADE_AI_LLM_API_KEY / BLADE_AI_MODEL_NAME / BLADE_AI_API_BASE_URL
(and the rest of the BLADE_AI_* family via chaos_agent settings).

Run:  .venv/bin/python scripts/llm_eval_harness.py run
      .venv/bin/python scripts/llm_eval_harness.py run --runs 5
      .venv/bin/python scripts/llm_eval_harness.py diff old.json new.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))                       # tests package
sys.path.insert(0, str(REPO / "tests" / "golden"))  # golden_runner module

from golden_runner import load_scenarios, run_scenario  # noqa: E402

DEFAULT_OUT = REPO / "outputs" / "llm_eval_report.json"


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _load_intent_scenarios(only: str | None) -> list[dict]:
    """Intent-surface scenarios only; ``only`` filters by name substring."""
    scenarios = [s for s in load_scenarios() if s["surface"] == "intent"]
    if only:
        scenarios = [s for s in scenarios if only in s["name"]]
    return scenarios


async def mode_run(
    runs: int, out_path: Path, only: str | None, min_win_rate: float,
) -> int:
    from chaos_agent.config.settings import settings

    if not settings.llm_api_key:
        print(
            "BLADE_AI_LLM_API_KEY is empty — real-model eval needs a key "
            "(config.json or env).",
            file=sys.stderr,
        )
        return 2

    scenarios = _load_intent_scenarios(only)
    if not scenarios:
        print("no matching intent scenarios — corpus empty after filter", file=sys.stderr)
        return 2

    print(
        f"model={settings.model_name}  base_url={settings.api_base_url}\n"
        f"scenarios={len(scenarios)}  runs={runs}  min_win_rate={min_win_rate}"
    )

    rows: list[dict] = []
    for s in scenarios:
        attempts = []
        for i in range(runs):
            t0 = time.monotonic()
            failures, actual = await run_scenario(s, mode="real", return_actual=True)
            attempts.append({
                "pass": not failures,
                "duration_s": round(time.monotonic() - t0, 1),
                "failures": failures,
                # Forensic evidence: adjudicate failures WITHOUT re-running.
                "tool_trace": actual.get("tool_trace"),
                "llm_calls": actual.get("llm_calls"),
                "messages_tail": (actual.get("messages_text") or "")[-1500:],
            })
            mark = "PASS" if not failures else "FAIL"
            print(f"  {s['name']}  run {i + 1}/{runs}  {mark}  "
                  f"({attempts[-1]['duration_s']}s)")
        rows.append({
            "name": s["name"],
            "runs": attempts,
            "win_rate": sum(a["pass"] for a in attempts) / runs,
        })

    # ── win-rate matrix ────────────────────────────────────────────────
    print("\nwin-rate matrix")
    print(f"  {'scenario':<44} {'rate':>7}  runs")
    for row in rows:
        marks = "".join("P" if a["pass"] else "F" for a in row["runs"])
        print(f"  {row['name']:<44} {row['win_rate']:>6.0%}  [{marks}]")

    total_pass = sum(a["pass"] for r in rows for a in r["runs"])
    total = len(rows) * runs
    payload = {
        "mode": "real",
        "runs": runs,
        "model": settings.model_name,
        "api_base_url": settings.api_base_url,
        "min_win_rate": min_win_rate,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "corpus_size": len(rows),
        "summary": {
            "sample_win_rate": total_pass / total if total else 0.0,
            "scenarios_all_pass": sum(r["win_rate"] >= min_win_rate for r in rows),
            "scenarios_total": len(rows),
        },
        "scenarios": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"\nwritten: {out_path}")

    worst = min(r["win_rate"] for r in rows)
    if worst < min_win_rate:
        print(
            f"FAIL: worst scenario win-rate {worst:.0%} < {min_win_rate:.0%}",
            file=sys.stderr,
        )
        return 1
    print("all scenarios at or above the win-rate threshold")
    return 0


def mode_diff(old_path: Path, new_path: Path) -> int:
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    old_rates = {r["name"]: r["win_rate"] for r in old.get("scenarios", [])}
    new_rates = {r["name"]: r["win_rate"] for r in new.get("scenarios", [])}

    def _hdr(tag: str) -> str:
        return f"{tag} ({old.get('git_sha', '?')} → {new.get('git_sha', '?')})"

    print(f"diff {_hdr('models')}: {old.get('model')} → {new.get('model')}")

    regressed = []
    for name, rate in sorted(new_rates.items()):
        if name not in old_rates:
            print(f"  NEW      {name}: {rate:.0%}")
        elif rate < old_rates[name]:
            regressed.append(name)
            print(f"  REGRESS  {name}: {old_rates[name]:.0%} → {rate:.0%}")
        elif rate > old_rates[name]:
            print(f"  IMPROVE  {name}: {old_rates[name]:.0%} → {rate:.0%}")
    for name, rate in sorted(old_rates.items()):
        if name not in new_rates:
            print(f"  REMOVED  {name}: was {rate:.0%}")

    if regressed:
        print(
            f"FAIL: {len(regressed)} scenario(s) regressed: "
            f"{', '.join(regressed)}",
            file=sys.stderr,
        )
        return 1
    print("no regressions")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="sample the corpus against the real model")
    p_run.add_argument("--runs", type=int, default=3,
                       help="samples per scenario (default 3)")
    p_run.add_argument("--out", type=Path, default=DEFAULT_OUT,
                       help=f"report path (default {DEFAULT_OUT.relative_to(REPO)})")
    p_run.add_argument("--scenarios", default=None,
                       help="substring filter on scenario names")
    p_run.add_argument("--min-win-rate", type=float, default=1.0,
                       help="exit 1 below this per-scenario win rate (default 1.0)")

    p_diff = sub.add_parser("diff", help="compare two run reports")
    p_diff.add_argument("old", type=Path)
    p_diff.add_argument("new", type=Path)

    args = parser.parse_args()
    if args.cmd == "run":
        rc = asyncio.run(mode_run(
            args.runs, args.out.resolve(), args.scenarios, args.min_win_rate,
        ))
    else:
        rc = mode_diff(args.old, args.new)
    sys.exit(rc)


if __name__ == "__main__":
    main()
