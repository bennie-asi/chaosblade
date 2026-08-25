"""Shared read-only command classifier — the single source of truth for
"is this shell command a read-only probe?".

Three separate judgments used to answer this question with divergent
vocabularies:

  - ``k8s_native._is_readonly_exec_probe`` — ``detect()`` injection attribution.
  - ``providers.k8s_native.classifier._classify_kubectl_exec`` — the guard SCOPE for
    the screeners (moved out of ``target_guard/classifier.py`` in phase-7 T5).
  - ``_baseline_profiles.validate_command`` — ``host_read`` + baseline capture.

This module unifies them. The core per-command judgment
(:func:`is_readonly_argv`) is context-independent — ``iptables -L`` is a
read-only rule dump whether it runs inside a pod exec or directly on a host, so
the same judgment applies everywhere. Two thin adapters sit on top:

  - :func:`is_readonly_kubectl_exec` — parses ``POD [-n NS] [-c C] -- INNER``,
    unwraps one ``sh -c`` layer, splits pipelines, and classifies each stage
    (matches the former ``_is_readonly_exec_probe`` behaviour exactly, plus the
    dual-use arg guards below).
  - :func:`is_readonly_host_command` — a bare host command (no ``POD --``); a
    single read-only diagnostic with NO UNQUOTED shell operators (pipe /
    redirect / chain / substitution — quoted literals like ``'a|b'`` are
    fine), matching the former host-profile
    ``validate_command`` policy.

Dual-use tools (``iptables`` / ``nft`` / ``tc`` / ``ip`` / ``systemctl`` /
``mount`` / ``dmesg``) are classified at the ARGUMENT level: their inspection
forms (``iptables -L``, ``ip addr show``, ``systemctl status``) are read-only
while their mutating forms (``iptables -A``, ``ip link set``, ``systemctl
stop``, ``dmesg -C``) are NOT. This closes a latent hole where a "read-only"
tool could run ``ip link set down`` / ``mount -o remount`` / ``dmesg -C``.

Every rejection carries a SPECIFIC reason (which binary / verb / operator made
it non-read-only) so a tool can tell the LLM exactly what to fix, rather than a
generic "rejected". The module is self-contained (no ``agent`` imports) so the
``tools`` layer can depend on it without an upward dependency.
"""

from __future__ import annotations

import re
import shlex

# The raw-string public surfaces (``host_command_rejection_reason`` /
# ``contains_shell_metachar`` / ``kubectl_exec_rejection_reason`` /
# ``is_readonly_argv``, plus their boolean views) run on the bashfacts
# structural judge in ``_readonly_facts`` — the ONLY engine since the
# engine flip deleted the legacy substring/shlex chain. The argv-level
# classifiers below (``_classify_argv`` / ``_classify_inner``) are NOT
# legacy residue: the facts judge reuses them for argv-semantics policy
# (binary/flag matching — see design 4.2 fact/policy split), and
# ``is_readonly_inner_tokens`` remains the token fallback for callers
# with no raw command text (the classifier's synthetic arg shapes).


def _facts_engine():
    """Lazy-import the facts engine (it imports THIS module's
    ``_classify_argv``, so a top-level import here would be circular)."""
    from chaos_agent.tools import _readonly_facts

    return _readonly_facts


# 4.5 fail-closed matrix: an internal error inside the facts engine must be
# reported TRUTHFULLY (never disguised as the command's syntax problem) and
# always fails closed. No fix-path suggestion pairs with it — the guard's
# own problem has no user-side fix.
_INTERNAL_ERROR_REASON = (
    "guard parser internal error; refusing fail-closed "
    "— very likely not your command's problem"
)


def _facts_verdict(thunk, *, on_error):
    """Run a facts-engine verdict behind the 4.5 internal-error net."""
    try:
        return thunk()
    except Exception:  # the guard must never crash its caller
        return on_error


# One ``sh -c "<script>"`` wrapper is peeled to reach the real entry token
# (mirrors ``target_guard.carriers._host_entry_tokens``; replicated here so this
# module stays in the ``tools`` layer with no agent-package import). Nested
# shells beyond one layer are unusual for a probe and keep failing closed.
_SHELL_WRAPPERS = ("sh", "bash", "ash", "dash", "/bin/sh", "/bin/bash")

# Read-only diagnostics — the TAIL allowlist of ``_classify_argv`` (union
# of the k8s exec-probe vocabulary and the host baseline diagnostic
# whitelist). Dual-use probe binaries (curl/wget/find/awk/ss...) ARE listed
# here, but the argument-level guards in ``_classify_argv`` run FIRST —
# only their guard-cleared shapes reach this allow pass. Binaries with a
# mutating sibling and no probe vocabulary are deliberately EXCLUDED
# instead and allowed per-argument below (see the host-probe exclusion note).
_READONLY_BINARIES = frozenset(
    {
        # identity / capability inspection
        "which",
        "type",
        "command",
        "test",
        "[",
        "uname",
        "id",
        "hostname",
        "whoami",
        "getent",
        "env",
        "printenv",
        "nproc",
        # no-op keep-alive (debug-pod entrypoint ``-- sleep 3600``; changes nothing)
        "sleep",
        "true",
        "echo",
        # filesystem inspection
        "ls",
        "stat",
        "readlink",
        "realpath",
        "file",
        "readelf",
        "cat",
        "head",
        "tail",
        "wc",
        "find",
        "du",
        "df",
        "lsblk",
        "blkid",
        # text filters (read-only stages of a probe pipeline, e.g. ps aux | grep)
        "grep",
        "egrep",
        "fgrep",
        "sort",
        "uniq",
        "cut",
        "tr",
        "awk",
        # process / resource inspection
        "ps",
        "top",
        "free",
        "uptime",
        "vmstat",
        "iostat",
        "mpstat",
        "sar",
        "pidof",
        "pgrep",
        "lsof",
        "lsmod",
        # network inspection
        "ss",
        "netstat",
        "ping",
        "ping6",
        "nslookup",
        "dig",
        "host",
        "wget",
        "curl",
        # path / reachability probes (send packets, mutate nothing — same class as
        # ``ping``). ``traceroute`` maps hops; ``arping`` resolves a MAC.
        "traceroute",
        "traceroute6",
        "arping",
        # host inspection probes reached through a privileged debug pod: hardware /
        # kernel / filesystem / hashing facts that are read-only REGARDLESS of args
        # in this name-only set. Added after task-3a360709 surfaced read-only host
        # probes rejected as escape mutations. Commands with a mutating sibling are
        # deliberately EXCLUDED here and handled per-argument below: date (-s),
        # route (add/del), ethtool (-s/-K), swapon (bare = enable), conntrack (-D),
        # arp (-d/-s), numactl (runs a wrapped command).
        "findmnt",
        "mountpoint",
        "lsns",
        "lscpu",
        "lspci",
        "getcap",
        "getenforce",
        "sestatus",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "cksum",
        "base64",
        "strings",
        "hexdump",
        "xxd",
        "od",
        "nm",
        "ldd",
        "objdump",
        # extended session / locale / hardware facts (all dump state, none write):
        # who/w/last enumerate logins, groups/locale/getconf print facts,
        # dmidecode/lshw inspect hardware, whereis locates files.
        # Evidence (strace on al8 host): who/w/last/groups/getconf/whereis/locale/
        # dmidecode/traceroute/arping are fully CLEAN. lshw creates+unlinks a
        # transient probe marker (/var/run/fb-<pid>) that is removed before exit —
        # no residual state. numastat has no binary in the target env; verified by
        # upstream source audit (numactl numastat.c): every fopen is mode "r"
        # (/proc/meminfo, sysfs numastat/meminfo, /proc/<pid>/smaps), the only
        # popen("resize") fires solely when stdout is a TTY — never in exec output.
        "who",
        "w",
        "last",
        "groups",
        "locale",
        "getconf",
        "numastat",
        "dmidecode",
        "lshw",
        "whereis",
    }
)
# Binaries that ARE the injection in an exec context even though their names
# are not fault verbs: load generators, device-mapper, port-occupying
# listeners. Their presence alone marks a mutation.
_MUTATING_BINARIES = frozenset(
    {
        "stress",
        "stress-ng",
        "dd",
        "fallocate",
        "fio",
        "dmsetup",
        "nc",
        "ncat",
        "socat",
    }
)
# Container-escape primitives reach the host. In a BARE host command they are
# always treated as a mutation (see ``_classify_argv``): ``is_readonly_argv``
# feeds ``host_inject``'s ``skip_guard``, and neither primitive is in
# ToolGuard's allow-list, so admitting them there would open a guard bypass on
# a path that never needs them. Inside a ``kubectl exec`` they ARE the standard
# way to inspect a node from a privileged debug pod (``chroot /host cat
# /etc/os-release``), so ``_classify_inner`` unwraps them and judges the REAL
# command instead — see ``_unwrap_escape``.
_ESCAPE_PRIMITIVES = ("chroot", "nsenter", "unshare")
# Flags that consume a SEPARATE value token, so the parser must skip two tokens.
# Getting these wrong shifts the parser's idea of where the real command starts.
_CHROOT_VALUE_FLAGS = frozenset({"--userspec", "--groups"})
_NSENTER_VALUE_FLAGS = frozenset(
    {
        "-t",
        "--target",
        "-S",
        "--setuid",
        "-G",
        "--setgid",
        "-r",
        "--root",
        "-w",
        "--wd",
        "--wdns",
    }
)


def _unwrap_escape(tokens: list[str]) -> list[str] | None:
    """Strip a leading escape primitive, returning the command it would run.

    Returns ``None`` when the prefix cannot be parsed with confidence — the
    caller must then fail closed rather than guess.

    Forms handled:
      ``chroot /host CMD...``                  → ``CMD...``
      ``chroot --skip-chdir /host CMD...``     → ``CMD...``
      ``nsenter -t 1 -m -n -- CMD...``         → ``CMD...``
      ``nsenter -t1 -m CMD...``                → ``CMD...``
      ``unshare -m CMD...``                    → ``CMD...``
    """
    if not tokens:
        return None
    binary = tokens[0].rsplit("/", 1)[-1]
    rest = tokens[1:]
    if binary == "chroot":
        # chroot [OPTION]... NEWROOT [COMMAND]... — options may PRECEDE NEWROOT
        # (--userspec / --groups / --skip-chdir). Skipping them is mandatory:
        # blindly treating rest[0] as NEWROOT once let
        # ``chroot --skip-chdir /host iptables -F`` through, because the
        # leftover ``/host`` basename collides with the read-only DNS ``host``
        # binary and the real command was never inspected.
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            if rest[i] in _CHROOT_VALUE_FLAGS:
                i += 2  # flag with a separate value
            else:
                i += 1
        # rest[i] is NEWROOT; the command follows it.
        if i + 1 >= len(rest):
            return None
        return rest[i + 1 :]
    # nsenter / unshare: an explicit ``--`` separates flags from the command;
    # without it, the command starts at the first token that is neither a flag
    # nor a value consumed by a flag taking an argument.
    if "--" in rest:
        inner = rest[rest.index("--") + 1 :]
        return inner or None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("-"):
            return rest[i:]
        # "-t 1" (separate value) vs "-t1" / "--target=1" (attached value)
        if tok in _NSENTER_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    return None


# Dual-use arg guards ------------------------------------------------------
_IPTABLES_READONLY_FIRST = (
    "-L",
    "-S",
    "--list",
    "--list-rules",
    "--version",
    "-V",
    "--help",
    "-h",
    "version",
)
# Global options that PRECEDE the command verb and consume a value (``-t nat``)
# or stand alone (``-4``/``-6``/``-w``). The first-token check used to stop at
# these and reject the everyday ``iptables -t nat -L -n`` form; skip them to
# reach the real verb.
_IPTABLES_GLOBAL_VALUE_FLAGS = frozenset({"-t", "--table", "-M", "--modprobe"})
_IPTABLES_GLOBAL_VALUELESS = frozenset({"-4", "-6", "-w", "--wait"})
_NFT_READONLY_FIRST = ("list", "--version", "-v", "--help", "-h")
_TC_MUTATING = ("add", "del", "delete", "change", "replace", "mod")
# ChaosBlade CLI — read-only only for its experiment-inspection verbs.
# ``create`` starts an experiment, ``destroy`` ends one (both mutate),
# ``prepare``/``revoke`` install/remove the injection agent. ``status`` /
# ``query`` inspect an experiment UID — the standard post-injection probe
# inside a tool-pod exec. Task-5193538b: ``blade status --uid ...`` was
# recorded as a kubectl-native INJECTION because ``blade`` was in neither
# vocabulary and the fail-safe below judged it mutating.
_BLADE_READONLY_VERBS = frozenset({"status", "query", "version", "-h", "--help"})
# ``ip`` mutating verbs across objects (link/addr/route/neigh/rule/...).
# ``exec`` (``ip netns exec ns1 <cmd>``) runs an ARBITRARY command inside a
# namespace; it appears in no read-only ip invocation, so keying on the bare
# token has zero false positives.
_IP_MUTATING = frozenset(
    {
        "set",
        "add",
        "del",
        "delete",
        "change",
        "replace",
        "flush",
        "append",
        "exec",
    }
)
_SYSTEMCTL_READONLY_VERBS = frozenset(
    {
        "status",
        "is-active",
        "is-enabled",
        "is-failed",
        "is-system-running",
        "show",
        "cat",
        "list-units",
        "list-unit-files",
        "list-dependencies",
        "list-timers",
        "list-sockets",
        "list-jobs",
    }
)
_MOUNT_MUTATING_FLAGS = (
    "-o",
    "--options",
    "--bind",
    "--move",
    "-B",
    "-M",
    "--rbind",
    "--make-shared",
    "--remount",
    # ``mount -a`` mounts everything in fstab — a mutation
    # even with no positional target.
    "-a",
    "--all",
)
# ``-a`` also bundles (``mount -av``), so the cluster is scanned too.
_MOUNT_MUTATING_SHORT_CHARS = frozenset("a")
_MOUNT_VALUELESS_SHORT = frozenset("avrwnfli")
_DMESG_MUTATING_FLAGS = ("-C", "--clear", "-c", "--read-clear")
# ``-C``/``-c`` bundle with the display flags (``dmesg -cT`` clears AND prints).
_DMESG_MUTATING_SHORT_CHARS = frozenset("Cc")
_DMESG_VALUELESS_SHORT = frozenset("CcTxkurtHwdePS")
# journalctl reads the journal; only its maintenance verbs write to it.
_JOURNALCTL_MUTATING_FLAGS = (
    "--rotate",
    "--flush",
    "--sync",
    "--relinquish-var",
    "--vacuum-size",
    "--vacuum-time",
    "--vacuum-files",
)
# sysctl reads unless a write form is present: ``-w``, ``key=value``, loading
# from a file (``-p``), or applying every config file (``--system``).
_SYSCTL_MUTATING_FLAGS = ("-w", "--write", "-p", "--load", "--system")
# date reads the clock unless it SETS it: ``-s``/``--set`` change the system
# time — which in this project is itself a fault (clock skew), never a probe.
# ``date -s`` appears verbatim in the time-drift skill, so misjudging it as
# read-only would wave an injection through the verify/intent screeners.
_DATE_MUTATING_FLAGS = ("-s", "--set")
# route prints/-n unless it edits the table (``add``/``del``/``delete``/
# ``flush``) — the mutating verbs are positionals, not flags.
_ROUTE_MUTATING_VERBS = frozenset({"add", "del", "delete", "flush", "change"})
# ethtool inspects (bare / ``-i``/``-S``/``-k``/``-g``/``-a``/``-c``) unless a
# CHANGE flag is present. The change flags are the upper-case-ish setters.
_ETHTOOL_MUTATING_FLAGS = frozenset(
    {
        "-s",
        "--change",
        "-K",
        "--features",
        "--offload",
        "-G",
        "--set-ring",
        "-A",
        "--pause",
        "-C",
        "--coalesce",
        "-L",
        "--set-channels",
        "-P",
        "--set-eeprom",
        "--reset",
    }
)
# conntrack reads with ``-L``/``-S``/``-G``; ``-D``/``-F``/``-U`` delete or
# flush the connection-tracking table (a fault, not an observation).
_CONNTRACK_MUTATING_FLAGS = frozenset(
    {
        "-D",
        "--delete",
        "-F",
        "--flush",
        "-U",
        "--update",
        "-I",
        "--create",
    }
)
# swapon ENABLES swap by default (a mutation); only ``-s``/``--show``/
# ``--summary`` are the read-only listing form. ``swapoff`` is never read-only.
_SWAPON_READONLY_FLAGS = frozenset({"-s", "--show", "--summary"})
# arp prints the cache unless ``-d`` (delete entry) / ``-s`` (add static) edit it.
_ARP_MUTATING_FLAGS = frozenset({"-d", "--delete", "-s", "--set"})
# --- Extended dual-use probe guards (audit follow-up) ---------------------
# ifconfig DISPLAYS by default; it mutates only when an action keyword or a
# value positional (what is being SET) is present. ``ifconfig eth0`` /
# ``ifconfig -a`` are display forms; ``ifconfig eth0 down`` /
# ``ifconfig eth0 10.0.0.1 netmask ...`` change state.
_IFCONFIG_MUTATING_KEYWORDS = frozenset(
    {
        "up",
        "down",
        "arp",
        "-arp",
        "promisc",
        "-promisc",
        "multicast",
        "mtu",
        "netmask",
        "dstaddr",
        "broadcast",
        "metric",
        "media",
    }
)
# crontab INSTALLS a crontab by default; only ``-l``/``--list`` reads.
# (``-r`` removes, ``-e`` edits, a positional file installs — all mutate.)
_CRONTAB_READONLY_FLAGS = frozenset({"-l", "--list"})
# timedatectl reads unless it SETS the clock — set-time IS the clock-drift
# fault in this project, so it must never pass as a probe.
_TIMEDATECTL_MUTATING_VERBS = frozenset(
    {
        "set-time",
        "set-timezone",
        "set-local-rtc",
        "set-ntp",
    }
)
# resolvectl / systemd-resolve read with ``status``; the set-*/revert/flush
# verbs rewrite resolver state (a network mutation).
_RESOLVECTL_MUTATING_VERBS = frozenset(
    {
        "revert",
        "set-dns",
        "set-domain",
        "set-llmnr",
        "set-mdns",
        "set-dns-over-tls",
        "set-dnssec",
        "flush-caches",
        "reset-statistics",
        "reset-server-features",
    }
)
# fdisk / parted list partitions only with ``-l``/``--list``; a bare device
# argument opens the interactive (mutating) partition editor.
_DISK_READONLY_FLAGS = frozenset({"-l", "--list"})
# openssl is a crypto toolkit — only the ``version`` subcommand is a probe;
# every other subcommand computes / writes / connects. java RUNS bytecode by
# default; only its version banner is a safe probe (note the single-dash
# ``-version``, not covered by the ``--version`` metadata rule).
_JAVA_READONLY_PROBES = frozenset(
    {
        "-version",
        "--version",
        "-showversion",
        "-fullversion",
    }
)
# Package managers: query forms read, everything else installs / removes.
_DPKG_READONLY_FLAGS = frozenset(
    {
        "-l",
        "--list",
        "-s",
        "--status",
        "-S",
        "--search",
        "-L",
        "--listfiles",
        "-W",
        "--show",
        "-p",
        "--print-avail",
    }
)
_APK_READONLY_VERBS = frozenset(
    {
        "info",
        "search",
        "list",
        "policy",
        "version",
        "audit",
        "manifest",
    }
)
# find — read-only only WITHOUT its action primitives. ``-exec``/``-ok`` run an
# arbitrary command per match (the ``+`` terminator needs no shell metachar,
# so the string-level screens cannot see it), ``-delete`` removes whole trees,
# ``-fprint*``/``-fls`` write result files.
_FIND_MUTATING_FLAGS = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-delete",
        "-fls",
        "-fprint",
        "-fprint0",
        "-fprintf",
    }
)
# awk is a programming language, not a text filter: ``system(...)`` runs an
# arbitrary command, and ``-f``/``-i``/``@load`` execute program FILES.
# gawk's ``-E``/``--exec`` executes a program FILE exactly like ``-f`` (the
# CGI-safe spelling); omitting it leaves the same arbitrary-program channel
# one flag over.
_AWK_MUTATING_FLAGS = frozenset({"-f", "--file", "-i", "--include", "-E", "--exec"})
# Short forms take an ATTACHED value too (``-f/tmp/prog.awk``), which an
# exact-match check would wave through.
_AWK_MUTATING_SHORT_PREFIXES = ("-f", "-i", "-E")
_AWK_MUTATING_RE = re.compile(r"system\s*\(|@load")
# In-program constructs are judged on MERIT, not on characters. Only two
# constructs inside an awk program make it non-read-only:
#   print/printf ... > / >> target   writes or appends a file
#   ... | command / |& coproc         executes a command (incl. cmd | getline)
# Everything else the legacy raw-string screens used to refuse is read-only
# and stays allowed: comparisons (``NR>1``, ``print (a>b)``), string/regex
# CONTENT (``"a>b"``, ``/a|b/``), comments, logical ``||``, ``-F'|'`` field
# separators, and input redirection — ``getline < file`` only READS, the same
# capability ``cat`` already has on this allowlist. awk's own grammar makes
# the precise call possible: an unparenthesized ``>`` in a print statement IS
# the redirect operator (a comparison must be parenthesized to print), and a
# bare ``|`` at statement level has no meaning other than a command pipe.
# See _awk_program_mutation / _awk_program_arg_mutation below.
# Statement keywords: a ``/`` right after one opens a regex constant (no left
# operand is possible); after an identifier it is division.
_AWK_STATEMENT_KEYWORDS = frozenset(
    {
        "print",
        "printf",
        "getline",
        "if",
        "else",
        "while",
        "for",
        "do",
        "switch",
        "case",
        "default",
        "break",
        "continue",
        "next",
        "nextfile",
        "exit",
        "return",
        "delete",
        "function",
        "func",
        "BEGIN",
        "END",
        "in",
    }
)
# awk option rules for locating the program WORD: ``-F``/``-v`` carry inert
# string values (skipped), ``-e``/``--source`` carry program text.
_AWK_VALUE_FLAGS = frozenset({"-F", "-v", "--field-separator", "--assign"})
_AWK_PROGRAM_FLAGS = frozenset({"-e", "--source"})
# Short options that take NO value, per binary. Needed to read a bundled
# cluster correctly: an option that TAKES a value swallows the rest of the
# token as that value, so ``-XGET`` is "method GET", not "flags X/G/E/T".
# Scanning a cluster past such an option produces false positives (``-XGET``
# contains 'T'), so ``_reachable_cluster`` stops there — see that helper.
# Under-listing costs a missed bundle; over-listing costs a false rejection,
# so only high-confidence valueless flags are listed.
_CURL_VALUELESS_SHORT = frozenset("sSILkvfigGNjpZqnBRJM46hV0123#")
_WGET_VALUELESS_SHORT = frozenset("qvdbcNSkKmrpxEHn46hV")
# curl — read-only only when the response stays on STDOUT (its default). The
# long options below write local files (``--output*``/``--cookie-jar``/
# ``--dump-header``/``--trace*``/``--remote-name``) or move data off-box
# (``--data*``/``--form*``/``--upload*``/``--config``).
_CURL_MUTATING_LONG_PREFIXES = (
    "--output",
    "--remote-name",
    "--data",
    "--form",
    "--upload",
    "--config",
    "--cookie-jar",
    "--dump-header",
    "--trace",
)
_CURL_MUTATING_SHORT_CHARS = frozenset("oOdFTKcD")
# Verb discipline: ``-X``/``--request`` names the HTTP METHOD, and a drill
# target is often a REST endpoint (the apiserver itself). DELETE/POST/PUT/
# PATCH mutate the REMOTE side with zero local footprint, which the write/
# upload scan above cannot see. Only the idempotent read verbs stay
# probe-grade; a missing verb (``-X`` at argv end) fails closed with them.
_CURL_READONLY_VERBS = frozenset({"GET", "HEAD", "OPTIONS"})
_CURL_VERB_CLUSTER = re.compile(
    r"^-[" + re.escape("".join(_CURL_VALUELESS_SHORT)) + r"]*X(.*)$"
)
#: Discard sinks. ``-o /dev/null`` (curl) and ``-O /dev/null`` (wget) throw the
#: body away rather than writing a file, which is how a latency probe asks for
#: timing without the payload: ``curl -s -o /dev/null -w '%{time_total}'``. Both
#: forms were refused as "writes local files", so a drill measuring injected
#: network delay had no way to read the actual millisecond figure and fell back
#: to a coarse timeout flip (observed on task-15543b7b, which tried ``-o
#: /dev/null`` and ``-O /dev/null`` in succession and got neither).
#:
#: Only these exact paths. A discard sink is recognised by its path, so anything
#: else — including ``/dev/stdout`` or a writable device — stays refused.
_DISCARD_SINKS = frozenset({"/dev/null"})
#: dd operands that keep a discard-sink read from being read-only. ``seek``
#: positions the OUTPUT, which is meaningless for /dev/null but signals intent
#: to write at an offset; ``conv`` / ``oflag`` change how the sink is opened
#: (``conv=notrunc``, ``oflag=append``) and ``status`` is the only other operand
#: worth allowing. Anything that is not a pure read parameter is refused so a
#: discard sink cannot be used to smuggle a write form past the check.
_DD_MUTATING_OPERANDS = ("seek", "conv", "oflag")

# wget — its DEFAULT is to write the response into a cwd file, so the verdict
# is inverted: read-only only for ``--spider`` or output explicitly redirected
# to stdout (``-O -`` / ``--output-document=-`` / bundled ``-qO-``).
# ``-o``/``-a``/``--output-file`` redirect the LOG to a file; ``--post-*``/
# ``--body-file``/``--upload-file`` transmit data off-box. Both matter even
# with ``--spider``, which is why they are checked before it.
_WGET_MUTATING_LONG_PREFIXES = (
    "--post",
    "--body-file",
    "--upload-file",
    "--output-file",
)
_WGET_MUTATING_SHORT = frozenset({"-o", "-a"})
# wget metadata-only flags: they print and exit BEFORE any URL parsing, so no
# network access and no file write can happen (same exemption shape as
# iptables/nft/blade above). Checked only AFTER the mutating-flag scan, so a
# write/upload form stays refused no matter what rides alongside it.
_WGET_METADATA_FLAGS = frozenset({"--version", "-V", "--help", "-h"})
# Universal metadata probes: GNU-style tools print and exit BEFORE any action,
# so an argv made ONLY of these flags touches neither disk, network, nor
# process state — for ANY binary. The "every token is a metadata flag" shape is
# what keeps this bypass-proof: ``docker --version run alpine`` or
# ``blade --version create cpu`` carry a real token and fall through to the
# per-binary judges. Applied below the escape-primitive check, so
# nsenter/chroot/unshare stay refused even as bare probes.
_METADATA_FLAGS = frozenset({"--version", "-V", "--help", "-h"})
# The remaining table entries that can execute a command or write a file. Same
# root cause as find/awk/curl/wget: a name that reads as "diagnostic" while the
# argument list decides.
#
# ``sort -o`` / ``sar -o`` write (and TRUNCATE) an arbitrary path; ``ss -K``
# forcibly closes matching sockets — that IS a fault injection; ``uniq``'s
# SECOND positional is an output file, so its value-taking flags must be
# skipped before positionals are counted (``uniq -f 2 in`` has one, not two).
# ``command`` is handled separately: ``command -v X`` only resolves a path,
# while ``command X args`` RUNS X.
#
# Short forms are PREFIXES throughout: GNU getopt accepts an attached value
# (``sort -o/etc/passwd``), which an exact-token check would wave through.
_SORT_MUTATING_SHORT_PREFIXES = ("-o",)
_SORT_MUTATING_LONG_FLAGS = frozenset({"--output"})
_SAR_MUTATING_SHORT_PREFIXES = ("-o",)
_SS_MUTATING_FLAGS = frozenset({"-K", "--kill"})
# ``-f``/``-N`` are omitted deliberately: they TAKE a value (family / netns), so
# listing them would let the cluster scan run into that value and reject a
# namespace named e.g. "K8s".
_SS_VALUELESS_SHORT = frozenset("tuwxnlapemios46rZzdgHSbEM")
_UNIQ_VALUE_FLAGS = frozenset(
    {
        "-f",
        "-s",
        "-w",
        "--skip-fields",
        "--skip-chars",
        "--check-chars",
    }
)
# Container-runtime CLIs are dual-use. Only LEAF inspection verbs are read-only;
# ``exec`` is deliberately excluded because its inner command is unbounded, and
# lifecycle verbs (rm/kill/stop/...) mutate workloads.
#
# Grouping verbs (``image`` / ``config`` / ``container`` / ``volume`` / ``network``)
# are deliberately ABSENT: they take a mutating sub-verb, and a verdict based on
# the first token alone would admit ``docker image rm X`` / ``crictl config
# --set`` as "read-only". Listing forms have their own leaf verbs (``images``,
# ``ps``), so nothing legitimate is lost.
_RUNTIME_CLIS = ("crictl", "docker", "nerdctl", "podman", "ctr")
_RUNTIME_READONLY_VERBS = frozenset(
    {
        "ps",
        "images",
        "inspect",
        "inspecti",
        "inspectp",
        "logs",
        "stats",
        "statsp",
        "version",
        "info",
        "pods",
        "top",
        "imagefsinfo",
        "port",
        "events",
    }
)
# Runtime-CLI global flags that consume a separate value; their value must not
# be mistaken for the verb (e.g. ``crictl --runtime-endpoint unix://... ps``).
_RUNTIME_VALUE_FLAGS = frozenset(
    {
        "-r",
        "--runtime-endpoint",
        "-i",
        "--image-endpoint",
        "-t",
        "--timeout",
        "-c",
        "--config",
        "-H",
        "--host",
        "--context",
        "--log-level",
        "-n",
        "--namespace",
        "--address",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
        "-D",
        "--debug-dir",
    }
)
# Wrappers that prefix a real command; the wrapped command decides the verdict.
_COMMAND_WRAPPERS = ("timeout", "stdbuf", "nice", "ionice", "env", "watch")
# Wrapper flags consuming a separate value — skipping only the flag would leave
# its value to be mistaken for the wrapped command.
_WRAPPER_VALUE_FLAGS = frozenset(
    {
        "-n",
        "-c",
        "-p",
        "-o",
        "-i",
        "-e",
        "-k",
        "-s",
        "-u",
        "--kill-after",
        "--signal",
        "--unset",
        "--chdir",
        "--class",
        "--classdata",
        "--pid",
        "--output",
        "--input",
        "--error",
        "--interval",
    }
)
# ``timeout``'s DURATION positional: a number with an optional unit suffix.
_DURATION_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")

# Shell control operators that make an inner command a compound script rather
# than a single probe — fail closed to non-read-only. ``|`` is handled
# separately (pipeline of read-only stages is allowed for exec probes).
_SHELL_CONTROL_OPS = (">", "<", "`", "$(", "&&", "||", ";", "&", "\n")


def _host_entry_tokens(inner: list[str]) -> list[str]:
    """Unwrap a single ``sh -c "<script>"`` layer to reach the real entry."""
    if inner and inner[0] in _SHELL_WRAPPERS and "-c" in inner:
        idx = inner.index("-c")
        if idx + 1 < len(inner):
            try:
                nested = shlex.split(inner[idx + 1])
            except ValueError:
                return inner
            if nested:
                return nested
    return inner


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Strip leading command wrappers (``timeout 5``, ``nice -n 5``, ``env A=1``).

    Returns the wrapped command, or the original tokens when nothing is wrapped
    (so a bare ``env`` still classifies as the environment dump it is).
    """
    depth = 0
    while tokens and depth < 3:
        binary = tokens[0].rsplit("/", 1)[-1]
        if binary not in _COMMAND_WRAPPERS:
            return tokens
        rest = tokens[1:]
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in _WRAPPER_VALUE_FLAGS:
                i += 2  # flag consuming a separate value (``nice -n 5``)
            elif tok.startswith("-") or "=" in tok:
                i += 1  # valueless flag, or an ``env VAR=VAL`` assignment
            elif binary == "timeout" and _DURATION_RE.match(tok):
                i += 1  # timeout's DURATION positional
            else:
                break  # first real token of the wrapped command
        rest = rest[i:]
        if not rest:
            return tokens  # nothing wrapped — judge the wrapper itself
        tokens = rest
        depth += 1
    return tokens


def _reachable_cluster(token: str, valueless: frozenset[str]) -> str:
    """Option characters an argv-level check may judge inside a short cluster.

    A short option that takes a value swallows the REST of the token as that
    value, so scanning every character is wrong in both directions:
    ``curl -XGET`` would trip on the 'T' of "GET" (false rejection), while
    ``curl -so /root/x`` must trip on the 'o' (a real write).

    Returns the leading run of known valueless flag characters PLUS the first
    character that is not one — that character is still an option and worth
    judging, but everything after it is its value.
    """
    if not token.startswith("-") or token.startswith("--"):
        return ""
    out: list[str] = []
    for ch in token[1:]:
        out.append(ch)
        if ch not in valueless:
            break  # takes a value: the remainder of the token IS that value
    return "".join(out)


def _drop_discard_output(
    args: list[str], flags: tuple[str, ...], *, cluster_of: frozenset[str]
) -> list[str]:
    """Remove ``<flag> /dev/null`` pairs so the mutating scan does not see them.

    Writing to a discard sink is not a write (see :data:`_DISCARD_SINKS`), but
    the scans that follow judge a token by its flag alone and cannot look at the
    value. Consuming the pair here keeps those scans untouched: every other
    write or upload flag still reaches them, and a non-discard value leaves the
    flag in place so it is refused exactly as before.

    Handles the three spellings a caller may use: separate (``-o /dev/null``),
    attached long (``--output=/dev/null``), and bundled short (``-so
    /dev/null``) — for the bundle only the output character is dropped, the rest
    of the cluster is preserved so a co-bundled write flag is still caught.
    """
    short = tuple(f for f in flags if not f.startswith("--"))
    long_ = tuple(f for f in flags if f.startswith("--"))
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None

        # ``--output=/dev/null``
        if tok.startswith(long_) and "=" in tok:
            name, value = tok.split("=", 1)
            if name in long_ and value in _DISCARD_SINKS:
                i += 1
                continue

        # ``-o /dev/null`` / ``--output /dev/null``
        if tok in flags and nxt in _DISCARD_SINKS:
            i += 2
            continue

        # ``-so /dev/null`` — drop only the output char from the cluster.
        if nxt in _DISCARD_SINKS and tok.startswith("-") and not tok.startswith("--"):
            cluster = _reachable_cluster(tok, cluster_of)
            chars = {f.lstrip("-") for f in short}
            if cluster and cluster[-1] in chars:
                kept = "-" + cluster[:-1]
                if len(kept) > 1:
                    out.append(kept)
                i += 2
                continue

        out.append(tok)
        i += 1
    return out


def _awk_program_mutation(program: str) -> str | None:
    """First write/execute construct in an awk program, or None.

    Quote/regex/comment/paren-aware scan that judges by CONSTRUCT, not by
    character. Only two in-program constructs make awk non-read-only:

      - an output redirect: ``>``/``>>`` at paren-depth 0 inside a
        print/printf statement (awk's own grammar makes this exact — an
        unparenthesized ``>`` there IS the redirect operator; printing a
        comparison requires parentheses, so ``print (a>b)`` stays allowed);
      - a command pipe: a bare ``|`` or ``|&`` at depth 0 — awk has no other
        single-pipe operator, so this is always ``print | cmd``,
        ``cmd | getline``, or a coproc (``||`` is logical OR, skipped).

    Input redirection (``getline < file``) only READS — the same capability
    ``cat`` has on this allowlist — and stays allowed along with
    comparisons, string/regex content, and comments. Division-vs-regex uses
    the lexer rule (a ``/`` with no possible left operand opens a regex
    constant), and a backslash-newline continuation does not end a
    statement. Anything unbalanced fails CLOSED — awk would refuse the
    program anyway.
    """
    depth = 0
    i, n = 0, len(program)
    stmt_print = False  # a print/printf statement is open at depth 0
    prev_operand = False  # previous significant char could end an operand
    while i < n:
        ch = program[i]
        if ch == "\\" and i + 1 < n and program[i + 1] == "\n":
            i += 2  # line continuation, not a stmt end
            continue
        if ch == '"':
            i += 1
            while i < n:
                if program[i] == "\\":
                    i += 2
                    continue
                if program[i] == '"':
                    break
                i += 1
            if i >= n:
                return "an unbalanced string literal"
            i += 1
            prev_operand = True  # a string is an operand
            continue
        if ch == "/" and not prev_operand:
            i += 1  # regex constant — never division here
            while i < n:
                if program[i] == "\\":
                    i += 2
                    continue
                if program[i] == "/":
                    break
                i += 1
            if i >= n:
                return "an unbalanced regex"
            i += 1
            prev_operand = True
            continue
        if ch == "#":
            j = program.find("\n", i)
            i = n if j < 0 else j  # a comment runs to end of line
            continue
        if ch in "([":
            depth += 1
            prev_operand = False
        elif ch in ")]":
            depth = max(0, depth - 1)
            prev_operand = True
        elif ch == "|" and depth == 0:
            if i + 1 < n and program[i + 1] == "|":
                i += 2  # logical OR, not a pipe
                prev_operand = False
                continue
            return "an in-program command pipe (print | cmd / cmd | getline)"
        elif ch == ">" and depth == 0 and stmt_print:
            return "an in-program output redirect (print > file)"
        elif ch in ";{}\n" and depth == 0:
            stmt_print = False
            prev_operand = False
        elif ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (program[j].isalnum() or program[j] == "_"):
                j += 1
            if depth == 0:
                word = program[i:j]
                stmt_print = word in ("print", "printf")
                prev_operand = word not in _AWK_STATEMENT_KEYWORDS
            else:
                prev_operand = True
            i = j
            continue
        elif ch.isdigit():
            prev_operand = True
        elif not ch.isspace():
            prev_operand = False  # operators/delimiters open an operand
        i += 1
    return None


def _awk_program_arg_mutation(args: list[str]) -> str | None:
    """Scan the program WORDs of an awk argv for write/execute constructs.

    Program text is located with awk's own option rules: ``-F``/``-v`` values
    are inert strings (skipped — a ``-F'|'`` separator is not a pipe),
    ``-e``/``--source`` carry program text, ``--`` ends option processing,
    and every other non-flag token is treated as program text. File and
    ``var=value`` operands are scanned too — over-scanning there only fails
    closed (a filename carrying these shapes was refused before).
    """
    i, n = 0, len(args)
    options_done = False
    while i < n:
        arg = args[i]
        if not options_done and arg.startswith("-") and arg != "-":
            if arg == "--":
                options_done = True
                i += 1
                continue
            if arg in _AWK_VALUE_FLAGS:
                i += 2  # inert string value follows
                continue
            if arg in _AWK_PROGRAM_FLAGS:
                prog = args[i + 1] if i + 1 < n else ""
                i += 2
            elif arg.startswith("--source="):
                prog = arg.split("=", 1)[1]
                i += 1
            elif arg.startswith("-e") and len(arg) > 2:
                prog = arg[2:]  # gawk attached ``-e<program>``
                i += 1
            else:
                i += 1  # other flag / attached inert value
                continue
        else:
            prog = arg
            i += 1
        mutation = _awk_program_mutation(prog)
        if mutation is not None:
            return mutation
    return None


def _classify_argv(tokens: list[str], _depth: int = 0) -> tuple[bool, str | None]:
    """Classify a single command (one pipeline stage). Returns (ok, reason)."""
    if not tokens:
        return True, None
    binary = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]

    # Wrappers (``timeout 5 <cmd>``, ``nice -n 5 <cmd>``, ``env A=1 <cmd>``):
    # the wrapped command decides the verdict.
    if binary in _COMMAND_WRAPPERS and _depth < 3:
        unwrapped = _strip_wrappers(tokens)
        if unwrapped is not tokens and unwrapped != tokens:
            return _classify_argv(unwrapped, _depth + 1)
        # No wrapped command: ``env`` alone dumps the environment (read-only);
        # a bare metadata probe (``timeout -V`` / ``nice --help``) prints and
        # exits; a bare wrapper otherwise does nothing observable.
        if binary in _READONLY_BINARIES or (
            args and all(a in _METADATA_FLAGS for a in args)
        ):
            return True, None
        return (
            False,
            f"'{binary}' wraps no command, so read-only status cannot be determined",
        )

    # Escape primitives reach the host. A ``/host/...`` absolute path needs NO
    # special handling here: ``binary`` above is the BASENAME, so
    # ``/host/usr/bin/cat`` classifies as ``cat`` (a legitimate debug-pod probe
    # path) while ``/host/usr/bin/iptables -A`` still lands in the iptables
    # guard below, and an unknown ``/host`` binary fails closed at the end.
    if binary in _ESCAPE_PRIMITIVES:
        return (
            False,
            f"'{binary}' reaches the host / escapes the container, not a read-only probe",
        )

    # Pure metadata probe (``--version`` / ``-h`` / ... and nothing else): every
    # CLI prints and exits before any action, whatever the binary otherwise
    # does — covers dd/timeout/nice/systemctl/docker/crictl/stress-ng/... in
    # one rule instead of per-binary exemptions.
    if args and all(a in _METADATA_FLAGS for a in args):
        return True, None

    # Netfilter tooling — read-only only in list/version forms. The command verb
    # may be preceded by global options (``iptables -t nat -L -n``), so skip
    # them before locating the verb rather than reading args[0].
    # Evidence: even ``-L`` creates /run/xtables.lock (O_CREAT) — the iptables
    # 1.8+ lock protocol taken for ANY netlink op; the ruleset itself is not
    # touched (strace shows no other write).
    if binary in ("iptables", "ip6tables"):
        i = 0
        while i < len(args):
            a = args[i]
            if a in _IPTABLES_GLOBAL_VALUE_FLAGS:
                i += 2  # ``-t <table>`` / ``-M <modprobe>`` consume a value
                continue
            if a in _IPTABLES_GLOBAL_VALUELESS:
                i += 1
                # ``-w`` may carry an optional seconds value (``-w 5 -L``)
                if a == "-w" and i < len(args) and args[i].isdigit():
                    i += 1
                continue
            break
        cmd = args[i] if i < len(args) else ""
        if cmd in _IPTABLES_READONLY_FIRST:
            return True, None
        return False, (
            f"'{binary}' is read-only only with -L/-S/--list/--version "
            f"(got '{cmd or 'no command'}'; -A/-D/-I/-F etc. mutate)"
        )
    if binary == "nft":
        ok = bool(args) and args[0] in _NFT_READONLY_FIRST
        if ok:
            return True, None
        return (
            False,
            "'nft' is read-only only with list/--version (add/delete/flush mutate)",
        )
    if binary == "tc":
        mutating = any(a in _TC_MUTATING for a in args)
        if not mutating:
            return True, None
        return (
            False,
            "'tc' add/del/change/replace mutate (only show/qdisc queries are read-only)",
        )

    # blade — read-only only for experiment inspection (see _BLADE_READONLY_VERBS).
    # Evidence: even these verbs open chaosblade.dat (BoltDB bookkeeping) and
    # touch its mtime, but content stays byte-identical (md5 before/after on a
    # live node) — an open-for-mapping side effect, not a mutation.
    if binary == "blade":
        if args and args[0] in _BLADE_READONLY_VERBS:
            return True, None
        # A help flag ANYWHERE short-circuits the mutation: blade is a
        # cobra-based CLI and cobra prints help and exits before the
        # subcommand's Run executes, whatever other flags are present.
        # Verified live: `blade create mem load -h`, `blade create mem load
        # --mode ram --mem-percent 80 --timeout 10 -h`, `blade create k8s
        # node-mem load --help` and `blade destroy -h` all exit 0, print
        # usage, and leave `blade status --type create` unchanged (no
        # experiment record created). This form is the flag-discovery probe
        # for injection planning, not an injection.
        if any(a in ("-h", "--help") for a in args):
            return True, None
        return False, (
            "'blade' is read-only only for status/query/version or a -h/--help probe "
            f"(got '{args[0] if args else 'no arguments'}'; create/destroy/prepare/revoke mutate)"
        )

    # ip — read-only unless a mutating verb (set/add/del/...) is present.
    if binary == "ip":
        mutating = any(a in _IP_MUTATING for a in args)
        if not mutating:
            return True, None
        return (
            False,
            "'ip' is read-only only with show/list/get (set/add/del/flush mutate)",
        )

    # systemctl — read-only only for its status/show verbs.
    if binary == "systemctl":
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in _SYSTEMCTL_READONLY_VERBS:
            return True, None
        return False, (
            "'systemctl' is read-only only for verbs like status/is-active/is-enabled/show/list-units "
            f"(got '{verb or 'no verb'}'; start/stop/restart mutate)"
        )

    # mount — read-only only when listing (no target device/dir, no remount).
    if binary == "mount":
        positionals = [a for a in args if not a.startswith("-")]
        mutating = bool(positionals) or any(
            a in _MOUNT_MUTATING_FLAGS
            or a.startswith("-o")
            or any(
                ch in _MOUNT_MUTATING_SHORT_CHARS
                for ch in _reachable_cluster(a, _MOUNT_VALUELESS_SHORT)
            )
            for a in args
        )
        if not mutating:
            return True, None
        return (
            False,
            "'mount' is read-only only with no arguments or -l (mounting/-o/-a/remount mutate)",
        )

    # dmesg — read-only unless clearing the ring buffer.
    if binary == "dmesg":
        if any(
            a in _DMESG_MUTATING_FLAGS
            or any(
                ch in _DMESG_MUTATING_SHORT_CHARS
                for ch in _reachable_cluster(a, _DMESG_VALUELESS_SHORT)
            )
            for a in args
        ):
            return (
                False,
                "'dmesg' -C/-c clears the kernel ring buffer, which mutates (read-only only reads)",
            )
        return True, None

    # journalctl — reading the journal is read-only; maintenance verbs are not.
    if binary == "journalctl":
        bad = next(
            (a for a in args if a.split("=")[0] in _JOURNALCTL_MUTATING_FLAGS),
            None,
        )
        if bad:
            return False, (
                f"'journalctl' {bad} writes to / cleans up the journal store, which mutates"
                " (read-only only queries, e.g. -u/-n/--since)"
            )
        return True, None

    # sysctl — reading a key is read-only; -w / key=value / -p write kernel params.
    if binary == "sysctl":
        if any(a in _SYSCTL_MUTATING_FLAGS for a in args) or any(
            "=" in a and not a.startswith("-") for a in args
        ):
            return False, (
                "'sysctl' is read-only only in read form (e.g. -a / -n key / key)"
                "; -w, key=value and -p write kernel parameters"
            )
        return True, None

    # hostname — bare and the read flags (-f/-s/-d/...) print a fact; a
    # POSITIONAL argument (or ``-F``/``--file``) SETS the host name, which on
    # a drill node breaks kubelet identity/registration.
    if binary == "hostname":
        bad = next(
            (a for a in args if not a.startswith("-") or a in ("-F", "--file")),
            None,
        )
        if bad is not None:
            return False, (
                f"'hostname' with an argument (or {bad}) sets the host name,"
                " which mutates node identity; only the bare form and read"
                " flags are read-only"
            )
        return True, None

    # date — reading the clock is a probe; ``-s``/``--set`` IS the clock-skew
    # fault. ``--set=...`` is caught by prefix so the value cannot hide it.
    if binary == "date":
        bad = next(
            (a for a in args if a in _DATE_MUTATING_FLAGS or a.startswith("--set=")),
            None,
        )
        if bad is not None:
            return (
                False,
                f"'date' {bad} sets the system clock, which mutates (that IS the clock-skew fault, not a probe)",
            )
        return True, None

    # route — printing the table is read-only; add/del/flush edit it.
    if binary == "route":
        bad = next((a for a in args if a in _ROUTE_MUTATING_VERBS), None)
        if bad is not None:
            return (
                False,
                f"'route' {bad} edits the routing table, which mutates (only no arguments or -n listing is read-only)",
            )
        return True, None

    # ethtool — inspects by default; setter flags change the NIC.
    if binary == "ethtool":
        bad = next((a for a in args if a in _ETHTOOL_MUTATING_FLAGS), None)
        if bad is not None:
            return False, (
                f"'ethtool' {bad} changes the NIC configuration, which mutates"
                " (only no arguments or -i/-S/-k/-g/-a/-c queries are read-only)"
            )
        return True, None

    # conntrack — -L/-S/-G read; -D/-F/-U/-I delete or flush the table.
    if binary == "conntrack":
        bad = next((a for a in args if a in _CONNTRACK_MUTATING_FLAGS), None)
        if bad is not None:
            return False, (
                f"'conntrack' {bad} deletes / flushes the connection-tracking table, which mutates (only -L/-S/-G are read-only)"
            )
        return True, None

    # swapon — ENABLES swap by default; only the listing flags are read-only.
    if binary == "swapon":
        if any(a in _SWAPON_READONLY_FLAGS for a in args):
            return True, None
        return (
            False,
            "'swapon' enables swap by default, which mutates (only the -s/--show listing is read-only)",
        )

    # arp — prints the cache unless -d (delete) / -s (add static) edit it.
    if binary == "arp":
        bad = next((a for a in args if a in _ARP_MUTATING_FLAGS), None)
        if bad is not None:
            return (
                False,
                f"'arp' {bad} edits the ARP cache, which mutates (only no arguments or -a/-n queries are read-only)",
            )
        return True, None

    # numactl — ``-H``/``--hardware`` / ``-s``/``--show`` inspect; any other form
    # RUNS a wrapped command (``numactl --physcpubind=0 stress ...``), so the
    # wrapped command must decide. Delegate exactly like the env/timeout path.
    if binary == "numactl":
        _RO = {"-H", "--hardware", "-s", "--show"}
        non_opt = [a for a in args if not a.startswith("-")]
        if not non_opt:
            # No wrapped command: read-only only if every flag is an inspect flag.
            if args and all(a in _RO for a in args):
                return True, None
            return (
                False,
                "'numactl' is read-only only for -H/--hardware/-s/--show queries",
            )
        if _depth >= 3:
            return (
                False,
                "'numactl' nesting is too deep to determine read-only status reliably",
            )
        # First non-option token onward is the wrapped command it runs.
        return _classify_argv(args[args.index(non_opt[0]) :], _depth + 1)

    # Container-runtime CLIs — only leaf inspection verbs (ps/inspect/logs/...).
    if binary in _RUNTIME_CLIS:
        verb = ""
        i = 0
        while i < len(args):
            tok = args[i]
            if not tok.startswith("-"):
                verb = tok
                break
            i += 2 if tok in _RUNTIME_VALUE_FLAGS else 1
        if verb in _RUNTIME_READONLY_VERBS:
            return True, None
        return False, (
            f"'{binary}' is read-only only for query verbs like ps/images/inspect/logs/stats/version "
            f"(got '{verb or 'no verb'}'; exec/rm/kill/stop/run, "
            "and grouped verbs with sub-verbs such as image/config, all mutate)"
        )

    # find — read-only only WITHOUT its action primitives. ``-exec``/``-ok``
    # run an arbitrary command per match (the ``+`` terminator carries no
    # shell metacharacter, so the string-level screens cannot see it),
    # ``-delete`` removes whole trees, ``-fprint*``/``-fls`` write files.
    if binary == "find":
        bad = next((a for a in args if a in _FIND_MUTATING_FLAGS), None)
        if bad is not None:
            return False, (
                f"'find' is read-only only for traversal/printing; {bad} runs commands / deletes / writes files"
            )
        return True, None

    # awk — a programming language, not a filter. ``system(...)`` runs an
    # arbitrary command; ``-f``/``-i``/``@load`` execute program FILES; an
    # in-program redirect (``print > file``) or command pipe (``print | cmd``,
    # ``cmd | getline``, ``|&`` coproc) writes files or runs commands from
    # inside the program string. The in-program shapes used to be caught ONLY
    # by the string-level metachar screens on the whole-command surfaces; the
    # bashfacts engine reads the quoted program string as the literal word it
    # is, so the guard now lives at argv level, unconditionally — and it
    # judges by construct, not by character (see _awk_program_mutation), so
    # the read-only forms those screens used to refuse (``NR>1`` comparisons,
    # regex alternation, ``getline < file`` reads) stay allowed.
    if binary == "awk":
        bad = next(
            (
                a
                for a in args
                if a.split("=", 1)[0] in _AWK_MUTATING_FLAGS
                or a.startswith(_AWK_MUTATING_SHORT_PREFIXES)
                or _AWK_MUTATING_RE.search(a)
            ),
            None,
        )
        if bad is not None:
            return False, (
                f"'awk' is read-only only for filtering/printing; {bad} can run commands or load program files"
                " (system()/@load/-f)"
            )
        mutation = _awk_program_arg_mutation(args)
        if mutation is not None:
            return False, (
                "'awk' is read-only only for filtering/printing; the program string carries"
                f" {mutation}, which writes a file or runs a command from inside awk"
            )
        return True, None

    # curl — read-only only when the response stays on STDOUT (the default).
    # Output/upload forms write local files or move host data off-box. Short
    # forms are read through ``_reachable_cluster`` so a bundled ``-so file``
    # is caught while a value-carrying ``-XGET`` is not mistaken for flags.
    #
    # ``-o /dev/null`` is the exception: it discards the body instead of writing
    # a file, which is the standard way to time a request without its payload.
    # Recognised before the mutating scan, and only for that flag — an upload or
    # a config read stays refused however its own value is spelled.
    if binary == "curl":
        args = _drop_discard_output(
            args, ("-o", "--output"), cluster_of=_CURL_VALUELESS_SHORT
        )
        for i, a in enumerate(args):
            verb = None
            if a in ("-X", "--request"):
                verb = args[i + 1] if i + 1 < len(args) else ""
            elif a.startswith("--request="):
                verb = a.split("=", 1)[1]
            else:
                m = _CURL_VERB_CLUSTER.match(a)
                if m:
                    # Attached (``-XDELETE``) or bundled (``-sX DELETE`` — the
                    # value rides the NEXT token when the cluster tail is bare).
                    verb = m.group(1) or (args[i + 1] if i + 1 < len(args) else "")
            if verb is not None and verb.upper() not in _CURL_READONLY_VERBS:
                return False, (
                    f"'curl' is read-only only with the idempotent verbs"
                    f" GET/HEAD/OPTIONS; {a}{' ' + verb if verb else ''}"
                    " mutates the remote endpoint"
                )
        bad = next(
            (
                a
                for a in args
                if a.startswith(_CURL_MUTATING_LONG_PREFIXES)
                or any(
                    ch in _CURL_MUTATING_SHORT_CHARS
                    for ch in _reachable_cluster(a, _CURL_VALUELESS_SHORT)
                )
            ),
            None,
        )
        if bad is not None:
            return False, (
                f"'curl' is read-only only when GET/HEAD output goes to stdout; {bad} writes local files"
                " or uploads data"
            )
        return True, None

    # wget — DEFAULT is to write the response into a cwd file, so the verdict
    # is inverted: read-only only for ``--spider`` or output explicitly sent
    # to stdout (``-O -`` / ``--output-document=-`` / bundled ``-qO-``).
    #
    # ``-o``/``-a``/``--output-file`` redirect the LOG, a sink separate from the
    # document, so they are dropped here on the same discard rule: ``wget -o
    # /dev/null -qO- <url>`` keeps both sinks off disk. The document check below
    # is untouched and still decides on its own.
    if binary == "wget":
        args = _drop_discard_output(
            args, ("-o", "-a", "--output-file"), cluster_of=_WGET_VALUELESS_SHORT
        )
        bad = next(
            (
                a
                for a in args
                if a.startswith(_WGET_MUTATING_LONG_PREFIXES)
                or a in _WGET_MUTATING_SHORT
            ),
            None,
        )
        if bad is not None:
            return False, (
                f"'wget' is read-only only with --spider or output to stdout; {bad} writes local files"
                " or uploads data"
            )
        # Metadata probes exit before any download: ``wget --version`` is the
        # standard binary-presence check and touches neither disk nor network.
        if any(a in _WGET_METADATA_FLAGS for a in args):
            return True, None
        if "--spider" in args:
            return True, None
        stdout_out = False
        for i, a in enumerate(args):
            if a in ("-O", "--output-document"):
                # ``-`` is stdout; ``/dev/null`` discards. Both leave nothing on
                # disk, which is what this check is actually asking about.
                nxt = args[i + 1] if i + 1 < len(args) else None
                stdout_out = nxt == "-" or nxt in _DISCARD_SINKS
            elif a.startswith("--output-document="):
                value = a.split("=", 1)[1]
                stdout_out = value == "-" or value in _DISCARD_SINKS
            else:
                # Bundled cluster (``-O-`` / ``-qO-``): only when ``O`` is the
                # LAST reachable option character is the tail its value. A
                # value-carrying option earlier in the token (``-UMozillaO-``)
                # makes the trailing "O-" part of that value, not an output
                # redirect — treating it as one would call a cwd write
                # read-only.
                cluster = _reachable_cluster(a, _WGET_VALUELESS_SHORT)
                if cluster.endswith("O"):
                    tail = a.split("O", 1)[1]
                    # ``-qO /dev/null`` puts the sink in the NEXT token.
                    if tail == "":
                        nxt = args[i + 1] if i + 1 < len(args) else None
                        stdout_out = nxt == "-" or nxt in _DISCARD_SINKS
                    else:
                        stdout_out = tail == "-" or tail in _DISCARD_SINKS
        if stdout_out:
            return True, None
        return False, (
            "'wget' writes the response into a file in the current directory by default; only --spider,"
            " -O- (stdout) or -O /dev/null (discard) is read-only (besides --version/--help metadata probes)"
        )

    # command — ``command -v X`` resolves a path and runs nothing (the probe
    # form the prompts recommend). Without ``-v``/``-V`` it EXECUTES X, so the
    # wrapped command decides, exactly as for env/timeout/nice.
    if binary == "command":
        if any(a in ("-v", "-V") for a in args):
            return True, None
        # ``command [-p] CMD [ARGS...]``: skip only ``command``'s OWN leading
        # options, then pass the remainder VERBATIM — the same shape
        # ``_strip_wrappers`` uses. Filtering out every dash token instead
        # would hand the wrapped guard an argument list with its own mutating
        # flags removed, turning ``command`` into a bypass prefix for the whole
        # set (``command sort -o /etc/cron.d/evil in`` → "read-only").
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 1
        wrapped = args[i:]
        if not wrapped:
            return True, None  # bare ``command`` does nothing observable
        if _depth >= 3:
            return (
                False,
                "'command' nesting is too deep to determine read-only status reliably",
            )
        ok, reason = _classify_argv(wrapped, _depth + 1)
        if ok:
            return True, None
        return False, f"'command' does not execute a read-only command: {reason}"

    # sort / sar — ``-o`` writes (and truncates) an arbitrary path. The short
    # form is matched as a PREFIX because GNU getopt accepts an attached value
    # (``sort -o/etc/cron.d/evil``); an exact-token check waves that through.
    if binary == "sort":
        bad = next(
            (
                a
                for a in args
                if a.startswith(_SORT_MUTATING_SHORT_PREFIXES)
                or a.split("=", 1)[0] in _SORT_MUTATING_LONG_FLAGS
            ),
            None,
        )
        if bad is not None:
            return (
                False,
                f"'sort' {bad} writes (and truncates) a file, not a read-only diagnostic",
            )
        return True, None
    if binary == "sar":
        bad = next(
            (a for a in args if a.startswith(_SAR_MUTATING_SHORT_PREFIXES)),
            None,
        )
        if bad is not None:
            return False, f"'sar' {bad} writes a data file, not a read-only diagnostic"
        return True, None

    # ss — ``-K``/``--kill`` force-closes every matching socket. That is a
    # fault injection, not an observation.
    if binary == "ss":
        bad = next(
            (
                a
                for a in args
                if a in _SS_MUTATING_FLAGS
                or "K" in _reachable_cluster(a, _SS_VALUELESS_SHORT)
            ),
            None,
        )
        if bad is not None:
            return (
                False,
                f"'ss' {bad} force-closes every matching socket, which is fault injection",
            )
        return True, None

    # uniq — ``uniq INPUT OUTPUT``: the second positional is an output file.
    if binary == "uniq":
        positionals: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in _UNIQ_VALUE_FLAGS:
                i += 2  # flag consuming a separate value (``-f 2``)
                continue
            if not a.startswith("-"):
                positionals.append(a)
            i += 1
        if len(positionals) > 1:
            return False, (
                f"'uniq' treats the second positional argument as an output file ({positionals[1]}), which writes to a file"
            )
        return True, None

    # dd — a writer by default, and that is why it sits in
    # ``_MUTATING_BINARIES``. But ``of=/dev/null`` makes it a pure reader, and
    # that form is how disk-IO verification measures read throughput: the skill
    # cases themselves run ``dd if=<file> of=/dev/null bs=1M count=100`` to show
    # a latency injection slowed reads down. Refusing it left no standard way to
    # time a read at all.
    #
    # Requires an explicit discard ``of=`` AND a source ``if=``. Without ``if=``
    # dd reads stdin, which no probe surface supplies — the form is either a
    # no-op or half of a pipeline, so it earns no exemption. Bare ``dd`` and any
    # real output path stay refused, as do the conversion operands that change
    # what is written even when the sink is discarded.
    if binary == "dd":
        operands = {k: v for k, _, v in (a.partition("=") for a in args) if _}
        if operands.get("of") in _DISCARD_SINKS and operands.get("if"):
            bad = next((f for f in _DD_MUTATING_OPERANDS if f in operands), None)
            if bad is None:
                return True, None
            return False, (
                f"'dd' reading into a discard sink is read-only, but {bad}= changes what is written"
            )

    # --- Extended dual-use probe guards (audit follow-up) -----------------
    # ifconfig — display unless an action keyword or a value positional (the
    # thing being SET) is present. Two+ positionals means ``iface VALUE``.
    if binary == "ifconfig":
        positionals = [a for a in args if not a.startswith("-")]
        kw = next(
            (p for p in positionals if p.lower() in _IFCONFIG_MUTATING_KEYWORDS), None
        )
        if kw is not None or len(positionals) >= 2:
            return False, (
                f"'ifconfig' {kw or 'with a value argument'} changes interface state "
                "(only bare / -a / single-interface display is read-only)"
            )
        return True, None

    # ipvsadm — lists with -L/--list (bare lists too); every other verb
    # adds/edits/deletes a virtual service or real server.
    if binary == "ipvsadm":
        if not args or any(
            a == "--list"
            or a == "-L"
            or (a.startswith("-L") and not a.startswith("--"))
            for a in args
        ):
            return True, None
        return (
            False,
            "'ipvsadm' is read-only only with -L/--list (add/edit/delete service mutate)",
        )

    # crontab — installs/edits/removes by default; only -l/--list reads.
    if binary == "crontab":
        if any(a in _CRONTAB_READONLY_FLAGS for a in args):
            return True, None
        return (
            False,
            "'crontab' installs/edits/removes a crontab by default (only -l/--list is read-only)",
        )

    # timedatectl — reads unless it SETS the clock.
    if binary == "timedatectl":
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in _TIMEDATECTL_MUTATING_VERBS:
            return (
                False,
                f"'timedatectl' {verb} changes the clock/timezone, which mutates (status/list-timezones are read-only)",
            )
        return True, None

    # resolvectl / systemd-resolve — read with status; set-*/revert/flush mutate.
    if binary in ("resolvectl", "systemd-resolve"):
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in _RESOLVECTL_MUTATING_VERBS:
            return (
                False,
                f"'{binary}' {verb} rewrites resolver state (status is read-only)",
            )
        return True, None

    # taskset / chrt — query one pid with -p; a second positional is the value
    # being SET, and without -p they RUN a command. chrt -m lists limits.
    if binary in ("taskset", "chrt"):
        if binary == "chrt" and any(a in ("-m", "--max") for a in args):
            return True, None
        has_p = any(
            a in ("-p", "--pid")
            or (a.startswith("-") and not a.startswith("--") and "p" in a[1:])
            for a in args
        )
        positionals = [a for a in args if not a.startswith("-")]
        if has_p and len(positionals) == 1:
            return True, None
        return (
            False,
            f"'{binary}' is read-only only as a single-pid -p query (setting affinity/priority or running a command mutates)",
        )

    # fdisk lists partitions with -l/--list (verified O_RDONLY on devices via
    # strace). parted is deliberately NOT admitted even for -l: strace shows it
    # opens every block device O_RDWR in list mode, and an RW fd on a raw
    # device is a write channel — fail closed.
    if binary == "fdisk":
        if any(a in _DISK_READONLY_FLAGS for a in args):
            return True, None
        return (
            False,
            "'fdisk' is read-only only with -l/--list (a bare device opens the mutating partition editor)",
        )
    if binary == "parted":
        return False, (
            "'parted' opens block devices O_RDWR even in list mode (verified by strace),"
            " so no form is admitted as a read-only probe — use 'fdisk -l'"
        )

    # openssl — only the 'version' subcommand is a probe.
    if binary == "openssl":
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb == "version":
            return True, None
        return (
            False,
            "'openssl' is read-only only for the 'version' subcommand (other subcommands compute/write/connect)",
        )

    # java — runs bytecode; only its version banner is a safe probe.
    # Evidence (Oracle JDK 17 CDS docs): the default CDS archive is
    # memory-mapped READ-ONLY at startup; archive WRITES happen only with the
    # explicit -Xshare:dump / -XX:ArchiveClassesAtExit flags, and crash logs
    # only on abnormal exit. The banner forms below never reach bytecode.
    if binary == "java":
        if args and all(a in _JAVA_READONLY_PROBES for a in args):
            return True, None
        return (
            False,
            "'java' runs bytecode (execution); only its -version banner is a read-only probe",
        )

    # Package managers — query forms read; everything else installs/removes.
    # Evidence (strace on al8 host): rpm -q* opens BDB region files
    # (/var/lib/rpm/__db.*) O_RDWR as part of BDB env recovery, but the real
    # database (Packages/...) is opened O_RDONLY only and no DB file mtime
    # changes — query stays query.
    if binary == "rpm":
        if any(
            a == "--query" or (a.startswith("-q") and not a.startswith("--"))
            for a in args
        ):
            return True, None
        return (
            False,
            "'rpm' is read-only only in query mode (-q/-qa/-ql..., --query); install/erase/upgrade mutate",
        )
    # dpkg query forms are classified as dpkg-query actions in the Debian man
    # page (dpkg-query reads /var/lib/dpkg without the mutating lock); no
    # Debian host exists in the test cluster, so this is doc-level evidence.
    if binary == "dpkg":
        if any(
            a in _DPKG_READONLY_FLAGS or a.split("=", 1)[0] in _DPKG_READONLY_FLAGS
            for a in args
        ):
            return True, None
        return (
            False,
            "'dpkg' is read-only only for query forms (-l/-s/-S/-L/-W); install/remove/purge mutate",
        )
    if binary == "apk":
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in _APK_READONLY_VERBS:
            return True, None
        return (
            False,
            "'apk' is read-only only for info/search/list/policy/version (add/del/upgrade mutate)",
        )

    if binary in _MUTATING_BINARIES:
        return (
            False,
            f"'{binary}' is a write/load-generating command, not a read-only diagnostic",
        )
    if binary in _READONLY_BINARIES:
        return True, None
    return False, f"'{binary}' is not a known read-only diagnostic command"


def _classify_inner(inner: list[str], _depth: int = 0) -> tuple[bool, str | None]:
    """Classify a kubectl-exec inner command (after ``--``).

    Unwraps one ``sh -c`` layer, fails closed on any shell control operator,
    and requires every pipeline stage to be a read-only probe.

    Escape primitives are unwrapped here (unlike in a bare host command): from a
    privileged debug pod, ``chroot /host <cmd>`` / ``nsenter -t 1 -m -- <cmd>``
    is the ONLY way to inspect the node, and Phase 1 must be able to verify host
    preconditions (does the node have iptables/systemd?) before committing a
    plan. The verdict is decided by the command actually being run, so
    ``chroot /host iptables -A ...`` stays non-read-only.
    """
    if not inner:
        return True, None  # bare exec (interactive/attach) → read-only
    inner = _host_entry_tokens(inner)
    if not inner:
        return False, "sh -c body is empty or cannot be parsed"

    # Escape prefix: judge the wrapped command. Wrappers are stripped first so
    # ``timeout 5 chroot /host df -h`` is recognised as an escape probe rather
    # than falling through to the bare-argv path (which rejects all escapes).
    # Depth-capped so a nested ``chroot /host chroot /host ...`` cannot spin, and
    # fail-closed whenever the prefix cannot be parsed with confidence.
    inner = _strip_wrappers(inner)
    entry = inner[0].rsplit("/", 1)[-1] if inner else ""
    if entry in _ESCAPE_PRIMITIVES:
        if _depth >= 2:
            return (
                False,
                f"'{entry}' nesting is too deep to determine read-only status reliably",
            )
        unwrapped = _unwrap_escape(inner)
        if not unwrapped:
            return False, (
                f"'{entry}' is followed by no parseable command, so it is treated as unsafe"
                " (a read-only probe must look like: chroot /host <read-only command>)"
            )
        ok, reason = _classify_inner(unwrapped, _depth + 1)
        if ok:
            return True, None
        return (
            False,
            f"'{entry}' does not run a read-only command once on the host: {reason}",
        )

    inner_str = " ".join(inner)
    for op in _SHELL_CONTROL_OPS:
        if op in inner_str:
            return False, (
                f"contains the shell control operator '{op.strip() or op!r}'"
                " (redirect/command chain/background/substitution), which a read-only probe does not allow"
            )
    if "|" in inner:
        stages: list[list[str]] = []
        current: list[str] = []
        for tok in inner:
            if tok == "|":
                stages.append(current)
                current = []
            else:
                current.append(tok)
        stages.append(current)
        for stage in stages:
            if not stage:
                continue
            ok, reason = _classify_argv(stage)
            if not ok:
                return False, f"a pipeline stage is not read-only: {reason}"
        return True, None
    return _classify_argv(inner)


# --- Public API: bool views + reason views (single source of truth) --------


def is_readonly_argv(argv: list[str]) -> bool:
    """True if a single command (one pipeline stage) is a read-only probe."""
    return (
        _facts_verdict(
            lambda: _facts_engine().argv_rejection_reason_facts(argv),
            on_error=_INTERNAL_ERROR_REASON,
        )
        is None
    )


def is_readonly_inner_tokens(inner: list[str]) -> bool:
    """True if a kubectl-exec inner command (tokens after ``--``) is read-only."""
    return _classify_inner(inner)[0]


def readonly_inner_tokens_reason(inner: list[str]) -> str | None:
    """Specific reason a kubectl-exec inner command (tokens after ``--``) is
    NOT read-only, or ``None`` when it IS.

    The reason view of :func:`is_readonly_inner_tokens`. Callers that must
    REFUSE (the classifier's exec branch, the read-only phase screeners) use
    this view so the verdict the shared judge actually reached — e.g. "contains
    the shell control operator ';'" — survives to the model instead of being
    flattened to a boolean at the API boundary and re-invented downstream.
    """
    ok, reason = _classify_inner(inner or [])
    return None if ok else reason


# The compliant-shape guidance paired with every read-only-probe refusal.
# Single source of truth: the kubectl_read tool layer and the read-only phase
# screeners render the SAME hint, so a model refused at the screener gets the
# identical fix path it would have got from the tool (and vice versa).
READONLY_PROBE_FIX_HINT = (
    "A read-only probe is ONE single command after `--` — no `;` / `&&` / "
    "`||` / redirects / background, no multi-statement `sh -c`. "
    "Examples: `-- which stress-ng` (check a binary), "
    "`-- ls /usr/bin/stress-ng` (check a file), `-- cat <path>` / "
    "`-- iptables -L` / `-- ip addr show` / `-- systemctl status` (read state). "
    "If the command is genuinely a fault INJECTION, it belongs to Phase 2 "
    "(execution), not to a read-only phase."
)


def is_readonly_kubectl_exec(v_args: str) -> bool:
    """True if a ``kubectl exec``/``debug`` inner command is a read-only probe.

    Parses ``POD [-n NS] [-c C] -- INNER``, unwraps one ``sh -c`` layer, and
    treats the command as read-only only when every pipeline stage is a known
    inspection command. Any shell control operator fails closed to mutating; a
    bare exec with no inner command is read-only.
    """
    return kubectl_exec_rejection_reason(v_args) is None


def kubectl_exec_rejection_reason(v_args: str) -> str | None:
    """Specific reason a ``kubectl exec``/``debug`` inner command is NOT
    read-only, or ``None`` when it IS read-only."""
    return _facts_verdict(
        lambda: _facts_engine().kubectl_exec_rejection_reason_facts(v_args),
        on_error=_INTERNAL_ERROR_REASON,
    )


def is_readonly_host_command(command: str) -> bool:
    """True if a bare host command is a single read-only diagnostic.

    No UNQUOTED shell operators (pipe/redirect/chain/substitution): a host
    channel does reach a remote shell, but ``wrap_command`` quotes every
    token, so an operator arrives as a literal argument and would silently
    do nothing. Quoted literals (``'a|b'``) carry no structure and are
    admitted by the facts engine.
    """
    return host_command_rejection_reason(command) is None


def contains_shell_metachar(command: str) -> bool:
    """True if a raw command string carries a shell metacharacter.

    The same screen ``host_command_rejection_reason`` applies, exported so
    other read-only fast paths that judge at ARGV level (``host_inject``'s
    ``skip_guard`` branch) can apply it too: the argv classifier judges ONE
    command, so shell-level composition (pipe/redirect/chain/substitution)
    still needs this raw-string net. (In-program writes are no longer its
    job — ``_awk_program_mutation`` sees those at argv level since Phase 2.)
    Quoting makes a metachar a useless literal on the wire anyway, so refusing
    loses nothing.
    """
    return _facts_verdict(
        lambda: _facts_engine().contains_shell_metachar_facts(command),
        on_error=True,
    )


def host_command_rejection_reason(command: str) -> str | None:
    """Specific reason a bare host command is NOT an allowed read-only
    diagnostic, or ``None`` when it is."""
    return _facts_verdict(
        lambda: _facts_engine().host_command_rejection_reason_facts(command),
        on_error=_INTERNAL_ERROR_REASON,
    )


__all__ = [
    "is_readonly_argv",
    "is_readonly_inner_tokens",
    "readonly_inner_tokens_reason",
    "READONLY_PROBE_FIX_HINT",
    "is_readonly_kubectl_exec",
    "kubectl_exec_rejection_reason",
    "is_readonly_host_command",
    "contains_shell_metachar",
    "host_command_rejection_reason",
]
