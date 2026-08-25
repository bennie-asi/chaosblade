"""Phase 0 deliverable: guard differential harness (design doc 5.3).

Same corpus, per-surface verdict comparison. The comparison is
TWO-DIMENSIONAL: guard verdicts (allow/deny + reason) AND attribution
results (k8s exec-inner mutation scan, screener scope routing) — a readonly
behavior change shifts both, and either dimension feeds the adjudication
list.

Harness input rules (5.3 — hardened by the three pilot correction rounds):
  1. ALWAYS the raw command string. Never ``join(shlex.split(cmd))``
     reconstruction — quote loss shatters ``sh -c 'script'`` payloads into
     multiple tokens and manufactures phantom regressions.
  2. ALWAYS the production entry function per surface — never an internal
     leaf helper (e.g. the carriers surface calls ``is_readonly_host_probe``,
     not the bare per-segment leaf it delegates to).
  3. Every surface adapter documents its input shape (see SURFACES).

Modes:
  baseline [OUT]        snapshot all surfaces over the corpus → JSON report
  diff OLD NEW          compare two snapshots → adjudication list (5.3 schema)
  fuzz N [SEED] [OUT]   grammar-subset + malformed fuzz (5.3): judge all
                        surfaces over N generated inputs; a ``bash -n``
                        referee flags any input it rejects but the facts
                        host surface allowed. Per-surface exceptions are
                        recorded, never raised.

Engine note (post-flip): the ``CHAOS_GUARD_READONLY_ENGINE`` switch and the
legacy chain were deleted at the engine flip — the bashfacts judge is the
ONLY engine, and snapshots label their ``chain`` as ``facts``. The
Phase-2/3 dual-run workflow lives on only in git history and the
adjudication ledger (docs/design/bash-structural-guard-diff-adjudication.md).

Run:  .venv/bin/python scripts/guard_diff_harness.py baseline
      .venv/bin/python scripts/guard_diff_harness.py diff old.json new.json
      .venv/bin/python scripts/guard_diff_harness.py fuzz 2000 20260817
"""
from __future__ import annotations

import json
import random
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from chaos_agent.tools import readonly  # noqa: E402
from chaos_agent.tools.guard import ToolGuard  # noqa: E402
from chaos_agent.agent.nodes.baseline import _baseline_profiles as bp  # noqa: E402
from chaos_agent.agent.providers import message_scanning as _scanning  # noqa: E402
from chaos_agent.agent.providers.k8s_native import provider as k8s_native  # noqa: E402
from chaos_agent.agent.target_guard import carriers, classifier  # noqa: E402
from chaos_agent.transports import PROFILE_HOST, PROFILE_K8S  # noqa: E402

CORPUS = REPO / "tests" / "fixtures" / "guard_command_corpus.json"
BASELINE_OUT = REPO / "tests" / "fixtures" / "guard_baseline_report.json"

# ---------------------------------------------------------------- instantiate
# Corpus keeps placeholders verbatim; judging sees the instantiated form.
_POD, _NS, _NODE = "chaosblade-tool-2xnhk", "default", "i-bp11fabmqqffbml8ysjs"
SUBS = [
    (r"\{\{\s*namespace\s*\}\}", _NS), (r"\{\{\s*target_name\s*\}\}", _POD),
    (r"\{\{\s*app_label\s*\}\}", "app=chaosblade-tool"),
    (r"<pod-name>|<pod>|<POD_NAME>|<target-pod>", _POD),
    (r"<test-pod>", "k8s-sre-toolbox-656cd88f8d-4k5xl"),
    (r"<debug-pod>", "node-debugger-xxx"), (r"<debug-namespace>", _NS),
    (r"<namespace>|<ns>|<NAMESPACE>", _NS),
    (r"<node-name>|<node>|<NODE>", _NODE),
    (r"<labels>|<label-selector>|<label>", "app=chaosblade-tool"),
    (r"<service-name>|<svc>", "kubernetes"),
    (r"<deployment-name>|<deployment>", "chaosblade-operator"),
    (r"<container>|<container-name>", "chaosblade-tool"),
    (r"<uid>|<experiment-uid>|<EXPERIMENT_UID>", "c7549a48024162de"),
    (r"<process-name>|<process>|<proc_name>", "java"),
    (r"<kubeconfig>|<path/to/kubeconfig>|<kubeconfig路径>", "/root/.kube/config"),
    (r"<目标服务地址>", "http://10.96.0.1:443"),
    (r"<target-pod-ip>", "10.0.0.1"), (r"<port>", "8080"),
    (r"<percent>", "10"), (r"<time>", "30"), (r"<size>", "1G"),
    (r"<image>", "busybox"), (r"<app>", "web-app"),
    (r"<[^>\n]{1,30}>", "x"),  # catch-all
    (r"\$\{NAMESPACE\}", _NS),
]


def instantiate(cmd: str) -> str:
    out = cmd
    for pat, rep in SUBS:
        out = re.sub(pat, rep, out)
    return out.strip()


# ---------------------------------------------------------------- surfaces
def _is_kubectl_exec(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[0].split("/")[-1] == "kubectl" and argv[1] == "exec"


def _exec_v_args(cmd: str) -> str:
    """Full exec argument string INCLUDING the leading ``exec`` (rule 1:
    raw substring of the original command, never a token re-join)."""
    return cmd[len("kubectl "):] if cmd.startswith("kubectl ") else cmd


def _exec_inner_str(cmd: str) -> str | None:
    """Raw inner substring after the first `` -- `` (quotes preserved)."""
    m = re.search(r"\s--\s", cmd)
    return cmd[m.end():] if m else None


_guard = ToolGuard()


def _s_host(cmd: str, argv: list[str]) -> Any:
    """INPUT: raw command string. ENTRY: readonly.host_command_rejection_reason."""
    return readonly.host_command_rejection_reason(cmd) or "ALLOW"


def _s_exec_outer(cmd: str, argv: list[str]) -> Any:
    """INPUT: full kubectl-exec arg string (raw substring, leading ``exec``).
    ENTRY: readonly.kubectl_exec_rejection_reason."""
    return readonly.kubectl_exec_rejection_reason(_exec_v_args(cmd)) or "ALLOW"


def _s_exec_inner(cmd: str, argv: list[str]) -> Any:
    """INPUT: shlex tokens of the raw inner substring after ``--``.
    ENTRY: readonly.readonly_inner_tokens_reason."""
    inner = _exec_inner_str(cmd)
    if inner is None:
        return None
    try:
        tokens = shlex.split(inner)
    except ValueError:
        tokens = []
    return readonly.readonly_inner_tokens_reason(tokens) or "ALLOW"


def _s_carriers(cmd: str, argv: list[str]) -> Any:
    """INPUT: raw command string (host-entry form required by the surface).
    ENTRY: carriers.is_readonly_host_probe — the FULL production entry
    (host-entry unwrap + separator segmentation + strictness scan)."""
    return "ALLOW" if carriers.is_readonly_host_probe(cmd) else "DENY"


def _s_baseline_k8s(cmd: str, argv: list[str]) -> Any:
    """INPUT: raw command string + transports.PROFILE_K8S constant.
    ENTRY: baseline validate_command."""
    return "ALLOW" if bp.validate_command(cmd, PROFILE_K8S) else "DENY"


def _s_baseline_host(cmd: str, argv: list[str]) -> Any:
    """INPUT: raw command string + transports.PROFILE_HOST constant.
    ENTRY: baseline validate_command."""
    return "ALLOW" if bp.validate_command(cmd, PROFILE_HOST) else "DENY"


def _s_guard_argv(cmd: str, argv: list[str]) -> Any:
    """INPUT: exec-form argv (shlex). ENTRY: ToolGuard.evaluate.
    CONTROL surface only — exec-form argv judging is out of scope (1.3)."""
    fb = _guard.evaluate(argv)
    return "ALLOW" if fb.allowed else f"DENY:{fb.constraint.value}"


def _s_attr_mutates(cmd: str, argv: list[str]) -> Any:
    """INPUT: full kubectl-exec arg string (same shape as K1).
    ENTRY: k8s_native.provider.exec_inner_command_mutates — attribution dimension."""
    return "MUTATES" if k8s_native.exec_inner_command_mutates(_exec_v_args(cmd)) else "readonly"


def _s_attr_scope(cmd: str, argv: list[str]) -> Any:
    """INPUT: command as a host_inject tool_call arg dict.
    ENTRY: classifier.infer_effective_target — screener scope attribution."""
    return classifier.infer_effective_target("host_inject", {"command": cmd}).scope


def _s_attr_host_cmd(cmd: str, argv: list[str]) -> Any:
    """INPUT: command as a host-native carrier tool_call (command-string form).
    ENTRY: providers.message_scanning._host_native_call_is_readonly — face 8
    injection-vs-diagnostic attribution (Phase 3 dispatches it to facts)."""
    return "readonly" if _scanning._host_native_call_is_readonly(
        {"command": cmd}) else "MUTATES"


def _s_attr_host_argv(cmd: str, argv: list[str]) -> Any:
    """INPUT: shlex argv as an exec_host_command tool_call (binary+args form).
    ENTRY: providers.message_scanning._host_native_call_is_readonly — face 8."""
    return "readonly" if _scanning._host_native_call_is_readonly(
        {"binary": argv[0], "args": argv[1:]}) else "MUTATES"


# name -> (dimension, applies(cmd, argv), judge(cmd, argv) -> verdict|None)
SURFACES: dict[str, tuple[str, Callable[[str, list[str]], bool],
                   Callable[[str, list[str]], Any]]] = {
    "H_host":        ("verdict", lambda c, a: True, _s_host),
    "K1_exec":       ("verdict", lambda c, a: _is_kubectl_exec(a), _s_exec_outer),
    "K2_exec_inner": ("verdict", lambda c, a: _is_kubectl_exec(a), _s_exec_inner),
    "C_carriers":    ("verdict", lambda c, a: bool(a) and a[0].split("/")[-1] != "kubectl",
                      _s_carriers),
    "B_k8s":         ("verdict", lambda c, a: True, _s_baseline_k8s),
    "B_host":        ("verdict", lambda c, a: True, _s_baseline_host),
    "G_argv_ctrl":   ("verdict", lambda c, a: bool(a), _s_guard_argv),
    "ATTR_mutates":  ("attribution", lambda c, a: _is_kubectl_exec(a), _s_attr_mutates),
    "ATTR_scope":    ("attribution", lambda c, a: True, _s_attr_scope),
    "ATTR_host_cmd":  ("attribution", lambda c, a: True, _s_attr_host_cmd),
    "ATTR_host_argv": ("attribution", lambda c, a: bool(a), _s_attr_host_argv),
}


def judge_command(cmd: str) -> dict[str, str]:
    """Run every applicable surface; per-surface exceptions are recorded,
    never raised (fuzz rule: the harness itself never aborts the run)."""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        argv = []
    out: dict[str, str] = {}
    for name, (_dim, applies, judge) in SURFACES.items():
        if not applies(cmd, argv):
            continue
        try:
            v = judge(cmd, argv)
        except Exception as exc:  # noqa: BLE001 — record, don't die
            v = f"ERROR:{type(exc).__name__}:{exc}"[:200]
        if v is not None:
            out[name] = str(v)[:240]
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- modes
def mode_baseline(out_path: Path) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    results, errors = [], 0
    for row in corpus["commands"]:
        cmd = instantiate(row["cmd"])
        if not cmd:
            continue
        verdicts = judge_command(cmd)
        if any(v.startswith("ERROR:") for v in verdicts.values()):
            errors += 1
        results.append({
            "cmd": row["cmd"], "instantiated": cmd,
            "src": row["src"], "category": row["category"],
            "verdicts": verdicts,
        })
    payload = {
        # The label reflects the judge the snapshot was taken with — a
        # hardcoded "old" here once let a stale pre-fix snapshot pose as
        # current in a diff. Facts is the only engine since the flip.
        "chain": "facts",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "corpus_version": corpus.get("version"),
        "corpus_size": len(results),
        "surface_errors": errors,
        "dimensions": {n: d for n, (d, _a, _j) in SURFACES.items()},
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    from collections import Counter
    verdict_stats, attr_stats = Counter(), Counter()
    for r in results:
        for name, v in r["verdicts"].items():
            if SURFACES[name][0] == "attribution":
                attr_stats[f"{name}={v[:40]}"] += 1
            else:
                verdict_stats[f"{name}={'A' if v == 'ALLOW' else 'D'}"] += 1
    print(f"commands judged: {len(results)}  surface errors: {errors}")
    for k, n in sorted(verdict_stats.items()):
        print(f"  {k}: {n}")
    for k, n in attr_stats.most_common(10):
        print(f"  {k}: {n}")
    print(f"written: {out_path.relative_to(REPO)}")


def mode_diff(old_path: Path, new_path: Path) -> None:
    old_doc = json.loads(old_path.read_text())
    new_doc = json.loads(new_path.read_text())
    old = {r["instantiated"]: r for r in old_doc["results"]}
    new = {r["instantiated"]: r for r in new_doc["results"]}
    # Cross-chain transparency: a legacy-era snapshot diffed against a
    # post-flip one reports engine-flip differences as FIX/REGRESS — the
    # flip itself, not a behavior change. Surface the labels up front so
    # nobody has to archaeology the snapshot timestamps to interpret them.
    oc, nc = old_doc.get("chain", "?"), new_doc.get("chain", "?")
    if oc != nc:
        print(f"chain: {oc} -> {nc}  (cross-engine diff — deltas below are the flip itself, not regressions)")
    adjudication, stats = [], {"same": 0, "fix": 0, "regress": 0,
                               "attr_same": 0, "attr_diff": 0, "missing": 0}
    dims = {n: d for n, (d, _a, _j) in SURFACES.items()}
    for cmd, nrow in new.items():
        orow = old.get(cmd)
        if orow is None:
            stats["missing"] += 1
            continue
        for surface, nval in nrow["verdicts"].items():
            oval = orow["verdicts"].get(surface)
            if oval is None or oval == nval:
                stats["attr_same" if dims[surface] == "attribution" else "same"] += 1
                continue
            if dims[surface] == "attribution":
                stats["attr_diff"] += 1
                kind = "ATTR_SHIFT"
            else:
                ov = "A" if oval in ("ALLOW", "readonly") else "D"
                nv = "A" if nval in ("ALLOW", "readonly") else "D"
                if ov == nv:  # same direction, different reason — still record
                    stats["same"] += 1
                    continue
                kind = "FIX(旧拒→新放)" if (ov == "D" and nv == "A") else "REGRESS(旧放→新拒)"
                stats["fix" if ov == "D" else "regress"] += 1
            adjudication.append({
                "input": cmd, "src": orow["src"], "surface": surface,
                "dimension": dims[surface],
                "old": oval, "new": nval, "kind": kind,
                "adjudication": "", "adjudicator": "", "date": "",
            })
    report = {"stats": stats, "adjudication_list": adjudication}
    # Scratch payload — the adjudication ledger required by 5.3 lives in
    # docs/design/bash-structural-guard-diff-adjudication.md; this JSON is
    # the per-entry record the ledger's class verdicts are back-filled into.
    out = REPO / ".codex_work" / "guard_diff_adjudication.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False))
    for d in adjudication[:20]:
        print(f"  [{d['kind']}] {d['surface']}: {d['input'][:90]}")
    print(f"written: {out.relative_to(REPO)}  (清零后方可切换)")


# ---------------------------------------------------------------- fuzz (5.3)
# Grammar-subset probe shapes + malformed mutations. The generator is a
# seeded RNG so every run is reproducible; seeds are recorded in the payload.
_FUZZ_BINS = ("cat", "df", "ls", "grep", "awk", "ps", "ss", "free", "head",
              "tail", "wc", "sort", "id", "hostname", "uptime")
_FUZZ_FLAGS = ("", " -h", " -n 5", " -l", " --help", " -a")
_FUZZ_PATHS = ("/etc/passwd", "/var/log/messages", "/tmp/x", "/proc/meminfo")
# Patterns deliberately include metachar-looking CONTENT (quoting decides).
_FUZZ_PATTERNS = ("root", "error", "a|b", "a>b", "x;y")
_FUZZ_WRAP = ("", "", "timeout 5 ", "watch -n 1 ")
_FUZZ_EXEC = ("", "", "kubectl exec mypod -- ",
              "kubectl exec mypod -n default -- ")


def _fuzz_one(rng: random.Random) -> str:
    cmd = rng.choice(_FUZZ_BINS) + rng.choice(_FUZZ_FLAGS)
    if rng.random() < 0.6:
        cmd += " " + rng.choice(_FUZZ_PATHS)
    if rng.random() < 0.3:
        pat = rng.choice(_FUZZ_PATTERNS)
        cmd += " " + (f"'{pat}'" if rng.random() < 0.5 else pat)
    if rng.random() < 0.25:  # pipeline into a filter stage
        cmd += " | " + rng.choice(("grep root", "head -5", "sort",
                                   "awk '{print $1}'"))
    if rng.random() < 0.3:
        cmd = rng.choice(_FUZZ_WRAP) + cmd
    pre = rng.choice(_FUZZ_EXEC)
    if pre:
        cmd = pre + (f"sh -c '{cmd}'" if rng.random() < 0.3 else cmd)
    # Malformed mutations (~38%): referee + fail-closed fodder.
    r = rng.random()
    if r < 0.12 and len(cmd) > 4:
        cmd = cmd[: rng.randrange(3, len(cmd))]
    elif r < 0.22:
        cmd += " 'unclosed"
    elif r < 0.32:
        cmd += rng.choice(("; id", " && id", " $(id)", " `id`", " >(id)", " )"))
    elif r < 0.38:
        inner = cmd.replace("'", "").replace('"', "")
        cmd = f"sh -c 'sh -c \"sh -c '{inner}'\"'"
    return cmd.strip()


def _bash_n_ok(cmd: str) -> bool:
    try:
        return subprocess.run(
            ["bash", "-n"], input=cmd, capture_output=True, text=True,
            timeout=5,
        ).returncode == 0
    except Exception:  # noqa: BLE001 — referee timeout/error = not clean
        return False


def mode_fuzz(count: int, seed: int, out_path: Path) -> None:
    rng = random.Random(seed)
    engine = "facts"
    # 5.3 referee rule (b): ``bash -n`` referees the structural judge (the
    # only engine since the flip).
    referee = shutil.which("bash") is not None
    results, errors, referee_hits = [], 0, 0
    for _ in range(count):
        cmd = _fuzz_one(rng)
        if not cmd:
            continue
        verdicts = judge_command(cmd)
        if any(v.startswith("ERROR:") for v in verdicts.values()):
            errors += 1
        # Rule (b), operationalized on the host surface: when ``bash -n``
        # rejects the input, the facts engine must NOT judge it clean
        # (``bash -n`` referees syntax only, never read-only semantics).
        if referee and verdicts.get("H_host") == "ALLOW" and not _bash_n_ok(cmd):
            referee_hits += 1
            verdicts["REFEREE"] = "VIOLATION: bash -n rejects, facts host allows"
        results.append({"cmd": cmd, "instantiated": cmd, "src": "fuzz",
                        "category": "fuzz", "verdicts": verdicts})
    payload = {
        "chain": engine,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "corpus_version": f"fuzz-{count}",
        "corpus_size": len(results),
        "surface_errors": errors,
        "referee_violations": referee_hits,
        "dimensions": {n: d for n, (d, _a, _j) in SURFACES.items()},
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"fuzz inputs judged: {len(results)}  engine={engine}  seed={seed}")
    print(f"  surface errors: {errors}  referee violations: {referee_hits}")
    print(f"written: {out_path.relative_to(REPO)}")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "baseline":
        if len(args) > 2:
            # ``baseline facts OUT`` used to SILENTLY write to ./facts — the
            # engine was never an argv option (and is no longer switchable
            # at all since the flip).
            print("usage: baseline [OUT]")
            sys.exit(2)
        out = Path(args[1]) if len(args) > 1 else BASELINE_OUT
        mode_baseline(out.resolve())
    elif args[0] == "diff" and len(args) == 3:
        mode_diff(Path(args[1]), Path(args[2]))
    elif args[0] == "fuzz" and len(args) >= 2:
        out = (Path(args[3]) if len(args) > 3
               else REPO / ".codex_work" / "fuzz_facts.json")
        mode_fuzz(int(args[1]), int(args[2]) if len(args) > 2 else 20260817,
                  out.resolve())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
