"""Phase 0 deliverable: guard command corpus collector (design doc 5.3).

Sources:
  A. tests/ guard-related test files (AST string literals) — the team's
     hand-enumerated edge cases.
  B. skills/ + knowledge/ + prompts/ markdown — command templates.
  C. ~/.blade-ai/memory/tasks/*.json — REAL executed commands, mined from
     tool_execution message contents (``[shell] ... --command <cmd>``).
  D. Built-in adversarial seed set — the 5.4 categories (obfuscation, quote
     nesting, ANSI-C, deep nesting, malformed truncation, interpreter
     program strings). Category "adversarial"; verdicts are NOT asserted
     here, only recorded by the harness baseline.

Output: tests/fixtures/guard_command_corpus.json
  {"version": 1, "sources": {...}, "commands": [{"cmd", "src", "category"}]}

Placeholders (``<pod-name>`` etc.) are kept verbatim in the corpus; the
harness instantiates them at judge time so both raw and instantiated forms
stay reproducible.

Run:  .venv/bin/python scripts/collect_command_corpus.py
"""
from __future__ import annotations

import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "tests" / "fixtures" / "guard_command_corpus.json"
TASKS_DIR = Path.home() / ".blade-ai" / "memory" / "tasks"

TEST_FILES = [
    "tests/test_tools/test_guard.py",
    "tests/test_tools/test_readonly_dual_use_guards.py",
    "tests/test_tools/test_readonly_escape_probe.py",
    "tests/test_tools/test_guard_gateway.py",
    "tests/test_tools/test_guard_parser.py",
    "tests/test_tools/test_guard_single_resource_binaries.py",
    "tests/test_tools/test_host_cmd_guard_skip.py",
    "tests/test_agent/test_target_guard/test_carriers.py",
    "tests/test_agent/test_target_guard/test_classifier.py",
]

MD_DIRS = [
    "skills",
    "src/chaos_agent/knowledge",
    "src/chaos_agent/agent/prompts",
]

# Known binary / wrapper command heads (first token heuristic).
BINARY_HEADS = {
    "kubectl", "blade", "cat", "cd", "chmod", "chroot", "cp", "curl", "date",
    "dd", "df", "dig", "dmesg", "du", "echo", "env", "free", "fuser", "grep",
    "head", "hostname", "id", "iostat", "ip", "iptables", "journalctl", "kill",
    "ln", "ls", "md5sum", "mkdir", "mount", "mpstat", "mv", "nc", "netstat",
    "nice", "nohup", "nsenter", "nslookup", "numactl", "pgrep", "pidof", "ping",
    "pkill", "ps", "pwd", "rm", "rmdir", "sed", "awk", "sh", "bash", "ss",
    "strace", "sudo", "systemctl", "tail", "tar", "tc", "timedatectl", "timeout",
    "top", "touch", "ulimit", "uname", "uptime", "vmstat", "w", "watch", "wc",
    "wget", "which", "command", "crictl", "ctr", "dmsetup", "lsblk", "lsof",
    "stat", "readlink", "tr", "sort", "uniq", "xargs", "find", "tee", "sleep",
    "chattr", "lsattr", "getenforce", "sestatus", "ethtool", "ifconfig", "route",
    "arp", "hostnamectl", "lsmod", "modinfo", "lspci", "lsusb", "fdisk", "parted",
    "mkfs", "fsck", "tune2fs", "hdparm", "smartctl", "ionice", "chrt", "taskset",
    "perf", "bpftool", "tcpdump", "conntrack", "nft", "ipset", "modprobe",
}

# ---------------------------------------------------------------- source D
# Adversarial seeds — one representative per 5.4 category. The harness
# baseline records their current verdicts; direction assertions live in the
# resident adversarial test suite (Phase 1+), not here.
ADVERSARIAL: list[tuple[str, str]] = [
    # shell-exec wrapper command substitution (P2)
    ("watch echo '$(rm -rf /)'", "wrapper-subst"),
    ("watch -n 1 cat /proc/loadavg", "wrapper-benign"),
    # arithmetic hiding a substitution
    ("echo $(( $(id) + 1 ))", "arith-subst"),
    # quote-spliced substitution
    ('cat "$(id)"x', "quote-splice"),
    # nested sh -c (2 and 3 layers)
    ("sh -c 'sh -c \"id\"'", "sh-c-2layer"),
    ('sh -c \'sh -c "sh -c \\"id\\""\'', "sh-c-3layer"),
    # backtick / escaped backtick
    ("echo `id`", "backtick"),
    ("echo \\`id\\`", "backtick-escaped"),
    # heredoc with substitution inside (unquoted delimiter)
    ("cat <<EOF\n$(id)\nEOF", "heredoc-subst"),
    ("cat <<'EOF'\n$(id)\nEOF", "heredoc-quoted"),
    # process substitution
    ("diff <(ls /a) <(ls /b)", "process-subst"),
    ("cat <(id)", "process-subst-exec"),
    # ANSI-C boundary: literal argument must PASS, spliced name must not
    ("echo $'\\x3b'", "ansi-c-literal"),
    ("l$'\\x73' /etc", "ansi-c-splice"),
    # interpreter program strings (P1 fix pair — see design 2.2)
    ("awk 'NR>1{print $1}' /etc/passwd", "interp-benign"),
    ("awk '{print > \"/etc/cron.d/evil\"}' /tmp/x", "interp-write"),
    ("awk '{print | \"sh\"}' /tmp/x", "interp-pipe"),
    # unquoted variable expansion (readonly ALLOW / carriers DENY pair)
    ("echo $HOME", "var-expansion"),
    ("echo $! ; echo $1", "var-expansion-special"),
    # malformed / truncated
    ("sh -c 'cat /a", "malformed-unclosed"),
    ('echo "abc', "malformed-unclosed-dq"),
    # deep nesting beyond any budget (generated)
    ("sh -c '" * 40 + "id" + "'" * 40, "deep-nesting"),
    # quoted metachar literals (P1 core)
    ("grep 'a>b' /var/log/x", "quoted-literal"),
    ("grep 'a|b' /var/log/messages", "quoted-pipe"),
    ("echo 'hello; world'", "quoted-semicolon"),
]


def looks_like_command(s: str) -> bool:
    s = s.strip()
    if not (4 <= len(s) <= 400):
        return False
    if "\n" in s and s.count("\n") > 8:
        return False
    first = re.split(r"\s", s, 1)[0].split("=")[-1]
    first = first.lstrip("(/{[\"'").split("/")[-1]
    if first in BINARY_HEADS:
        return True
    return bool(re.match(r"^(sudo|env|[A-Z_]+=\S+)\s+\S", s))


# ---------------------------------------------------------------- source A
def from_test_file(path: Path) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for line in node.value.splitlines() or [node.value]:
                line = line.strip()
                if looks_like_command(line):
                    out.append(line)
    return out


# ---------------------------------------------------------------- source B
CMD_LINE_RE = re.compile(
    r"^\s{0,6}(?:\$|>>>)?\s*((?:kubectl|blade|cat|chmod|chroot|cp|curl|dd|df"
    r"|dmesg|echo|free|grep|head|iostat|iptables|kill|ls|nsenter|pgrep|pidof"
    r"|ps|rm|sh|bash|ss|strace|systemctl|tail|tc|top|vmstat|watch|wget|awk"
    r"|sed|mpstat|netstat)\b[^\n]{3,300})"
)


def from_md(path: Path) -> list[str]:
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    for line in text.splitlines():
        m = CMD_LINE_RE.match(line)
        if m and looks_like_command(m.group(1)):
            out.append(m.group(1).strip())
    return out


# ---------------------------------------------------------------- source C
COMMAND_FLAG_RE = re.compile(r"--command\s+(.+?)(?:\s+--(?:cluster|timeout|task)\b|$)")


def from_task_json(path: Path) -> list[str]:
    """Mine real executed commands from a persisted task transcript.

    ``tool_execution`` contents look like ``[shell] wiz task exec --command
    sh -c '...' --cluster-uuid ...`` — the guard-relevant payload is the
    ``--command`` value. The full wiz line itself is not a guard surface
    command, so only the flag value is collected (regex first, shlex
    fallback is unnecessary: the flag value is the last meaningful text).
    """
    out: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for msg in data.get("messages", []):
        if msg.get("type") != "tool_execution":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.startswith("[shell]"):
            continue
        m = COMMAND_FLAG_RE.search(content)
        if m and looks_like_command(m.group(1).strip()):
            out.append(m.group(1).strip())
    return out


def main() -> None:
    corpus: dict[str, tuple[str, str]] = {}  # cmd -> (first src, category)
    counts = {"tests": 0, "docs": 0, "tasks": 0, "adversarial": 0}

    def add(cmd: str, src: str, category: str) -> None:
        if cmd not in corpus:
            corpus[cmd] = (src, category)
            counts[category] += 1

    for rel in TEST_FILES:
        p = REPO / rel
        if p.exists():
            for cmd in from_test_file(p):
                add(cmd, rel, "tests")
    for d in MD_DIRS:
        base = REPO / d
        if base.exists():
            for p in sorted(base.rglob("*.md")):
                rel = str(p.relative_to(REPO))
                for cmd in from_md(p):
                    add(cmd, rel, "docs")
    if TASKS_DIR.exists():
        for p in sorted(TASKS_DIR.glob("*.json")):
            for cmd in from_task_json(p):
                add(cmd, p.name, "tasks")
    for cmd, tag in ADVERSARIAL:
        add(cmd, f"adversarial:{tag}", "adversarial")

    rows = [
        {"cmd": c, "src": s, "category": cat}
        for c, (s, cat) in sorted(corpus.items())
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_counts": counts,
        "commands": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"total unique commands: {len(rows)}  (by source: {counts})")
    from collections import Counter
    heads = Counter(r["cmd"].split()[0].split("/")[-1] for r in rows)
    print("top heads:", heads.most_common(12))
    print(f"written: {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    sys.exit(main())
