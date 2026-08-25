"""Facts-based read-only judge — the bashfacts engine behind the Phase-2
readonly surfaces (design doc 4.6/4.7).

Re-judges the RAW-STRING surfaces of ``readonly.py`` on structural
facts instead of substring screens, plus the one pure-ARGV surface whose
structural hole is recoverable post-tokenisation:

  - ``host_command_rejection_reason_facts``  — bare host command
  - ``contains_shell_metachar_facts``        — host_inject's skip_guard screen
  - ``kubectl_exec_rejection_reason_facts``  — kubectl exec/debug inner command
  - ``argv_rejection_reason_facts``          — argv vector (face 8: adds the
    ``watch`` re-parse the legacy argv chain never modelled)

The legacy chain asks "does the raw string CONTAIN a metacharacter"; this
engine asks "does the command CARRY structure" — a quoted ``>`` is a
literal, an unquoted one is a redirect. That single distinction is the P1
fix (``awk 'NR>1{print $1}'`` stops being refused) and it is more precise
in BOTH directions: quoted literals stop tripping the screen, while real
structure that substring scans misread is still structure (every parse
issue fails closed).

What does NOT change: the per-binary verdict itself. Words are rendered to
tokens (``bashfacts.word_token``) and judged by the SAME ``_classify_argv``
the legacy chain uses, so the dual-use vocabulary stays single-source
(including the awk in-program guard, which judges the dequoted program
argument).

Intentional deviations from the legacy chain (each one surfaces in the
dual-run diff and is registered in the adjudication list):

  - ``sh -c`` unwrap is the strict three-word form (``unwrap_sh_c``): flags
    before ``-c`` (``bash --init-file /tmp/x -c id``) are NO LONGER peeled —
    they fall to ``_classify_argv`` and fail closed as an unknown binary.
  - nested ``sh -c`` layers peel recursively within the nesting budget
    (legacy peeled exactly one layer, then rejected the inner ``sh``).
  - a ``sh -c`` layer is retried after wrapper stripping, so
    ``timeout 5 sh -c 'df -h'`` is seen through (legacy stopped there).
  - parse issues on ``contains_shell_metachar`` return True (legacy's pure
    substring scan returned False for an unbalanced-but-metachar-free
    string) — fail-closed.
  - ANSI-C words dequote to their DECODED value (``$'-s'`` judges as the
    ``-s`` flag bash would deliver), where shlex left them opaque.
  - ``watch`` payloads are RE-PARSED the way watch itself runs them (see
    ``_watch_payload``) — the legacy chain only stayed safe here because an
    upstream substring screen happened to fire first (design doc P2's
    implicit-ordering dependency); the check is now folded into the judge.
"""

from __future__ import annotations

import shlex

from chaos_agent.bashfacts import (
    Budget,
    CommandFacts,
    IssueKind,
    PartKind,
    ScriptFacts,
    WordFacts,
    has_shell_structure,
    iter_parts,
    parse_script,
    unwrap_sh_c,
    word_token,
)
from chaos_agent.tools.readonly import (
    _COMMAND_WRAPPERS,
    _DURATION_RE,
    _ESCAPE_PRIMITIVES,
    _WRAPPER_VALUE_FLAGS,
    _classify_argv,
    _strip_wrappers,
    _unwrap_escape,
)

__all__ = [
    "argv_rejection_reason_facts",
    "contains_shell_metachar_facts",
    "host_command_rejection_reason_facts",
    "inner_raw_after_double_dash",
    "kubectl_exec_rejection_reason_facts",
]

# Message tails mirror the deleted legacy chain's phrasing so a model
# refused by the facts engine gets the same fix path it always saw.
_TAIL_PROBE = (
    " (redirect/command chain/background/substitution), which a read-only"
    " probe does not allow"
)
_TAIL_HOST = (
    " (host_read only runs a single read-only diagnostic with no pipe/redirect)"
)


# --- word → token rendering ------------------------------------------------

# The renderer is ``bashfacts.word_token`` (fact layer, single source): a
# literal-class word dequotes to its value; a word carrying structure keeps
# the structure's raw source text (``$(id)`` stays ``$(id)``), so a flag
# table can never match a partially-dequoted shape.


def _segment_words(cmd: CommandFacts) -> list[WordFacts]:
    words: list[WordFacts] = []
    if cmd.name is not None:
        words.append(cmd.name)
    words.extend(cmd.args)
    return words


# --- watch: the shell-executing wrapper (design doc P2) ----------------------

# ``watch`` is the ONLY shell-executing wrapper in ``_COMMAND_WRAPPERS``:
# procps watch joins its argv and hands the string to ``sh -c`` — outer
# quotes do NOT survive the re-parse, so ``watch echo '$(rm -rf /)'`` REALLY
# runs ``rm`` (bash delivers ``$(rm -rf /)`` as a literal argv word; watch's
# sh -c then expands it). Exec-style wrappers (timeout/nice/env/...) deliver
# argv verbatim, where a quoted literal stays a literal. Model watch
# faithfully: join the dequoted payload exactly as watch does and re-judge
# the joined text as a fresh script. This removes the legacy implicit
# ordering dependency (an upstream substring screen happened to catch the
# shape first) by folding the check into the judge itself.
_WATCH_REASON_PREFIX = (
    "'watch' hands its arguments to sh -c (the only shell-executing wrapper),"
    " and the re-parsed payload is not read-only: "
)

# Per-binary value-taking flags for the WATCH walk (review finding:
# adjudication-list defect E). The shared legacy table over-approximates —
# ``-i`` takes a value for stdbuf but is VALUELESS for env
# (``--ignore-environment``). Over-skipping inside ``_strip_wrappers`` only
# shortens the suffix that gets classified, which fails closed (an unknown
# binary refuses); over-skipping HERE can eat the ``watch`` token itself —
# ``env -i watch echo '$(rm -rf /)'` slipped the payload past the re-parse
# check on all three surfaces (legacy stayed safe via its substring screen).
# ``env -S/--split-string`` is deliberately NOT modelled as value-taking: its
# value is itself a command line, so the walk breaks there and the legacy
# classifier refuses the opaque head (fail-closed) instead of guessing.
_WATCH_WALK_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "timeout": frozenset({"-k", "--kill-after", "-s", "--signal"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "watch": frozenset({"-n", "--interval", "-q", "--equexit"}),
}
# Missing entries in this table fail CLOSED, never open: at a pre-watch layer
# the flag's value becomes the walk's break token and gets classified as an
# unknown binary (deny); at the watch layer itself the value is swept into
# the re-parsed payload (a superset — more structure, not less). Likewise
# ``watch -x/--exec`` (which really uses exec(2), not sh -c) is modelled
# conservatively as a sh -c layer: an over-deny, registered.


def _watch_payload(tokens: list[str]) -> list[str] | None:
    """Tokens after the first ``watch`` layer in a wrapper chain, else None.

    Mirrors ``_strip_wrappers``' flag walk layer by layer, but with two
    deliberate divergences (both from post-delivery review findings):

    - per-binary value flags (``_WATCH_WALK_VALUE_FLAGS``, defect E) — a
      shared over-approximating table can eat the ``watch`` token itself;
    - NO depth cap (defect F) — a capped walk that returns None has NOT
      proven the absence of watch, and the downstream argv classifier keeps
      stripping past the cap (``timeout 5 timeout 5 timeout 5 watch echo
      '$(rm -rf /)'` failed open on all three surfaces). The walk strictly
      shrinks ``rest`` every layer, so an uncapped walk always terminates
      and its None is a PROOF of absence;
    - ``VAR=VAL`` skipping only for env (defect H) — env is the only wrapper
      that TAKES assignments; watch does not parse them, it joins them into
      the sh -c string, where a substitution in the RHS REALLY runs
      (``watch 'A=$(id)' df`` executes id). For every other wrapper the
      assignment word starts the payload.

    A wrapper chain with no watch in it — or a watch with no payload — is
    not this function's concern.
    """
    rest = tokens
    while rest:
        binary = rest[0].rsplit("/", 1)[-1]
        if binary not in _COMMAND_WRAPPERS:
            return None
        value_flags = _WATCH_WALK_VALUE_FLAGS.get(binary, _WRAPPER_VALUE_FLAGS)
        i = 1
        while i < len(rest):
            tok = rest[i]
            if tok in value_flags:
                i += 2  # flag consuming a separate value (``watch -n 1``)
            elif tok.startswith("-") or ("=" in tok and binary == "env"):
                i += 1  # valueless flag; ``VAR=VAL`` assignment (env only —
                # defect H: watch re-parses assignments through sh -c, so an
                # ``A=$(...)`` RHS there is real execution, not a setting)
            elif binary == "timeout" and _DURATION_RE.match(tok):
                i += 1  # timeout's DURATION positional
            else:
                break  # first real token of the wrapped command
        payload = rest[i:]
        if not payload:
            return None  # nothing wrapped — judge the wrapper itself
        if binary == "watch":
            return payload
        rest = payload
    return None


# --- structural scan --------------------------------------------------------


def _issue_reason(issue_kind: IssueKind, pos: int) -> str:
    if issue_kind is IssueKind.BUDGET_EXCEEDED:
        return (
            "analysis capacity exceeded (nesting budget) at pos"
            f" {pos}; refusing to guess"
        )
    if issue_kind is IssueKind.UNTERMINATED_QUOTE:
        return "command cannot be parsed (unbalanced shell quotes)"
    return f"command cannot be parsed ({issue_kind.value} at pos {pos}); failing closed"


def _structure_reason(
    script: ScriptFacts, *, allow_pipes: bool, tail: str
) -> str | None:
    """First structural element a read-only probe may not carry, or None.

    One script level only: nested scripts' issues bubble into
    ``script.errors``, and any substitution body is rejected wholesale by
    the substitution rule below, so no recursive walk is needed here.
    """
    for issue in script.errors:
        return _issue_reason(issue.kind, issue.pos)
    for op in script.operators:
        if op == "|" and allow_pipes:
            continue
        display = op.strip() or repr(op)
        # Wording mirrors the legacy chain's "shell control operator" so
        # either engine's refusal reads the same to the model.
        return f"contains the shell control operator '{display}'{tail}"
    for seg in script.segments:
        if isinstance(seg.command, ScriptFacts):
            # No offset available: ScriptFacts/Segment carry no pos fields
            # (fact-layer model unchanged by design 4.7).
            return f"contains a subshell '(...)'{tail}"
        cmd = seg.command
        if cmd.redirects:
            red = cmd.redirects[0]
            return (
                f"contains a shell redirect ('{red.operator}') at pos "
                f"{red.pos}{tail}"
            )
        words = _segment_words(cmd)
        for red in cmd.redirects:  # unreachable today (rejected above) —
            # kept so a future allow-list still scans redirect words
            if red.target is not None:
                words.append(red.target)
            if red.body is not None:
                words.append(red.body)
        for word in words:
            for part in iter_parts(word):
                if part.kind in (PartKind.COMMAND_SUBST, PartKind.BACKTICK_SUBST):
                    return (
                        f"contains command substitution ('{part.text}') at pos "
                        f"{part.pos}{tail}"
                    )
                if part.kind is PartKind.PROCESS_SUBST:
                    return (
                        f"contains process substitution ('{part.text}') at pos "
                        f"{part.pos}{tail}"
                    )
                if part.kind is PartKind.ARITH_EXPANSION and part.text.startswith(
                    "$(("
                ):
                    return (
                        f"contains arithmetic expansion ('$((...))') at pos "
                        f"{part.pos}{tail}"
                    )
    return None


# --- kubectl exec inner (pipeline of read-only stages) ----------------------


def _judge_stages(
    stages: list[list[WordFacts]], *, budget: Budget, depth: int
) -> tuple[bool, str | None]:
    """Classify the remaining pipeline stages via the shared argv judge.

    A tail stage ALSO gets the watch re-parse check (defect G): ``watch``
    re-runs its argv through sh -c no matter which pipeline stage it heads,
    so ``df | watch echo '$(id)'`` must not wave the payload through. Tail
    stages deliberately keep the legacy width otherwise (no sh -c peel, no
    escape unwrap) — only the fail-open direction is closed here.
    """
    for words in stages:
        if not words:
            continue  # an empty stage between two pipes — legacy skips it too
        tokens = [word_token(w) for w in words]
        watch_payload = _watch_payload(tokens)
        if watch_payload is not None:
            # Tokens are dequoted argv — exactly what watch joins for sh -c.
            joined = " ".join(watch_payload)
            if joined.strip():
                script = parse_script(joined, budget=budget)
                ok, reason = _judge_exec_script(script, budget=budget, depth=depth + 1)
                if not ok:
                    return False, _WATCH_REASON_PREFIX + reason
                continue
        ok, reason = _classify_argv(tokens)
        if not ok:
            return False, f"a pipeline stage is not read-only: {reason}"
    return True, None


def _judge_exec_words(
    words: list[WordFacts],
    rest_stages: list[list[WordFacts]],
    *,
    budget: Budget,
    depth: int,
) -> tuple[bool, str | None]:
    """Judge the head pipeline stage (peel/wrapper/escape) then the rest."""
    nested = unwrap_sh_c(list(words), budget=budget) if words else None
    if nested is not None:
        if nested.errors:
            return False, _issue_reason(nested.errors[0].kind, nested.errors[0].pos)
        if not nested.segments:
            return False, "sh -c body is empty or cannot be parsed"
        ok, reason = _judge_exec_script(nested, budget=budget, depth=depth)
        if not ok:
            return False, reason
        return _judge_stages(rest_stages, budget=budget, depth=depth)

    tokens = [word_token(w) for w in words]
    watch_payload = _watch_payload(tokens)
    if watch_payload is not None:
        # Tokens are dequoted argv — exactly what watch joins for sh -c.
        joined = " ".join(watch_payload)
        if joined.strip():
            script = parse_script(joined, budget=budget)
            ok, reason = _judge_exec_script(script, budget=budget, depth=depth + 1)
            if not ok:
                return False, _WATCH_REASON_PREFIX + reason
            return _judge_stages(rest_stages, budget=budget, depth=depth)

    stripped = _strip_wrappers(tokens)
    base = len(tokens) - len(stripped)  # both helpers return a pure SUFFIX

    # Registered deviation: retry the peel AFTER wrapper stripping
    # (``timeout 5 sh -c 'df -h'`` — legacy strips the wrapper and then
    # rejects ``sh`` as an unknown binary; the facts engine sees through).
    if base:
        nested = unwrap_sh_c(list(words[base:]), budget=budget)
        if nested is not None:
            if nested.errors:
                return False, _issue_reason(nested.errors[0].kind, nested.errors[0].pos)
            if not nested.segments:
                return False, "sh -c body is empty or cannot be parsed"
            ok, reason = _judge_exec_script(nested, budget=budget, depth=depth)
            if not ok:
                return False, reason
            return _judge_stages(rest_stages, budget=budget, depth=depth)

    entry = stripped[0].rsplit("/", 1)[-1] if stripped else ""
    if entry in _ESCAPE_PRIMITIVES:
        if depth >= 2:
            return False, (
                f"'{entry}' nesting is too deep to determine read-only status reliably"
            )
        unwrapped = _unwrap_escape(stripped)
        if not unwrapped:
            return False, (
                f"'{entry}' is followed by no parseable command, so it is"
                " treated as unsafe (a read-only probe must look like:"
                " chroot /host <read-only command>)"
            )
        cut = base + (len(stripped) - len(unwrapped))
        ok, reason = _judge_exec_words(
            words[cut:],
            rest_stages,
            budget=budget,
            depth=depth + 1,
        )
        if ok:
            return True, None
        return False, (
            f"'{entry}' does not run a read-only command once on the host: {reason}"
        )

    ok, reason = _classify_argv(stripped)
    if not ok:
        return False, reason
    return _judge_stages(rest_stages, budget=budget, depth=depth)


def _judge_exec_script(
    script: ScriptFacts, *, budget: Budget, depth: int
) -> tuple[bool, str | None]:
    bad = _structure_reason(script, allow_pipes=True, tail=_TAIL_PROBE)
    if bad is not None:
        return False, bad
    if not script.segments:
        return True, None  # bare exec (no inner command) — read-only
    stages = [_segment_words(seg.command) for seg in script.segments]
    return _judge_exec_words(stages[0], stages[1:], budget=budget, depth=depth)


# --- public surface judges --------------------------------------------------


def _is_double_dash_token(tok: str) -> bool:
    """True when a quote-preserving token's VALUE is exactly ``--``.

    posix=False keeps quotes/escapes in the token text, but a quoted or
    escaped ``--`` (``'--'`` / ``"--"`` / ``\\--``) still DEQUOTES to the
    separator in the argv the real kubectl receives — and the legacy
    posix=True splitter sees it as ``--`` too. Compare values, not text,
    or ``kubectl exec pod '--' rm -rf /`` would read as "no inner
    command" (fail-open) under this engine while legacy judged the inner.
    """
    if tok == "--":
        return True
    if tok[:1] not in ("'", '"', "\\"):
        return False
    try:
        return shlex.split(tok) == ["--"]
    except ValueError:
        return False


def inner_raw_after_double_dash(v_args: str) -> tuple[str | None, str | None]:
    """Slice the raw text after the first standalone ``--`` token.

    Returns ``(inner_raw, None)`` on success, ``(None, None)`` when no
    standalone ``--`` exists (a pure entry), and ``(None, reason)`` when
    the boundary cannot be located with confidence (fail-closed reason
    text for the caller to surface or fall back on).

    Locates the boundary with quote-PRESERVING tokens (posix=False keeps
    every token an exact substring of the source) and maps it back to a
    raw OFFSET — never a token re-join: re-joining shatters quote-adjacent
    words (``'a 'b''`` would gain a spurious word break and the strict
    ``sh -c`` unwrap would then refuse the four-word shape). Harness input
    rule 1 applies inside the judge itself. The separator test itself is
    VALUE-based (``_is_double_dash_token``) so quoting cannot hide it.
    """
    try:
        tokens = shlex.split(v_args, posix=False)
    except ValueError:
        return None, "command cannot be parsed (unbalanced shell quotes)"
    cursor = 0
    for tok in tokens:
        at = v_args.find(tok, cursor)
        if at == -1:  # defensive: posix=False tokens are exact substrings
            return None, "command cannot be parsed (token boundary lost); failing closed"
        cursor = at + len(tok)
        if _is_double_dash_token(tok):
            return v_args[cursor:], None
    return None, None


def kubectl_exec_rejection_reason_facts(v_args: str) -> str | None:
    """Facts-engine counterpart of ``kubectl_exec_rejection_reason``.

    Judges the inner command sliced from the original text by
    ``inner_raw_after_double_dash``. A quoted metachar inside the inner
    command stops tripping the screen (the P1 fix) while real structure
    still fails closed. Tokens before the ``--`` (pod, flags — or even a
    ``kubectl exec`` prefix, as the classifier's face-4 call site passes)
    are inert: judging starts after the boundary.
    """
    inner_raw, error = inner_raw_after_double_dash(v_args)
    if error is not None:
        return error
    if inner_raw is None:
        return None  # pure entry (no inner command) — read-only
    if not inner_raw.strip():
        return None
    budget = Budget()
    root = parse_script(inner_raw, budget=budget)
    ok, reason = _judge_exec_script(root, budget=budget, depth=0)
    return None if ok else reason


def host_command_rejection_reason_facts(command: str) -> str | None:
    """Facts-engine counterpart of ``host_command_rejection_reason``.

    Structure-free commands take a plain shlex fast path (the 90% hot
    path — verdicts byte-identical to the deleted legacy engine's on this
    class); anything carrying a structural character is parsed and judged
    on facts. A bare host command stays a SINGLE diagnostic: any operator
    (pipes included), redirect, substitution or subshell refuses — matching
    the deleted legacy metachar screen's verdicts, minus the quoted
    literals that screen could not tell apart.
    """
    if not command or not command.strip():
        return "empty command"
    if not has_shell_structure(command):
        try:
            tokens = shlex.split(command)
        except ValueError:
            return "command cannot be parsed (unbalanced shell quotes)"
        if not tokens:
            return "empty command"
        ok, reason = _classify_argv(tokens)
        return None if ok else reason
    root = parse_script(command)
    return _judge_host_script(root)


def _judge_host_script(root: ScriptFacts) -> str | None:
    """Host-surface verdict for a parsed script: single diagnostic command,
    no operators/redirects/substitutions, with a watch payload re-parsed the
    way watch itself will run it."""
    bad = _structure_reason(root, allow_pipes=False, tail=_TAIL_HOST)
    if bad is not None:
        return bad
    if not root.segments:
        return "empty command"
    words = _segment_words(root.segments[0].command)
    if not words:
        return "empty command"
    tokens = [word_token(w) for w in words]
    watch_payload = _watch_payload(tokens)
    if watch_payload is not None:
        joined = " ".join(watch_payload)
        if joined.strip():
            bad = _judge_host_script(parse_script(joined))
            if bad is not None:
                return _WATCH_REASON_PREFIX + bad
            return None
    ok, reason = _classify_argv(tokens)
    return None if ok else reason


def argv_rejection_reason_facts(argv: list[str]) -> str | None:
    """Facts-engine counterpart of ``is_readonly_argv`` (design 4.6 face 8).

    The argv arrives already tokenised — quoting is gone and a real
    exec(2) delivers the vector verbatim, so no structural re-parse of
    the tokens themselves is possible (or needed). The ONE structural
    recovery still required at this level is ``watch``: procps watch
    joins its argv and hands the string to ``sh -c``, so the payload is
    re-judged as a script exactly the way watch will run it (design doc
    P2). The legacy argv chain never modelled this — the raw-string
    surfaces stayed safe only because an upstream substring screen
    happened to fire first, while the ``exec_host_command`` binary+args
    path had no screen at all (an injection wrapped as
    ``watch bash -c ...`` was mis-attributed as a read-only diagnostic).
    """
    if not argv:
        return None
    watch_payload = _watch_payload(argv)
    if watch_payload is not None:
        # Tokens are dequoted argv — exactly what watch joins for sh -c.
        joined = " ".join(watch_payload)
        if joined.strip():
            bad = _judge_host_script(parse_script(joined))
            if bad is not None:
                return _WATCH_REASON_PREFIX + bad
            return None
    ok, reason = _classify_argv(argv)
    return None if ok else reason


def contains_shell_metachar_facts(command: str) -> bool:
    """Facts-engine counterpart of ``contains_shell_metachar``.

    True exactly when the command carries shell STRUCTURE (any operator,
    redirect, substitution, subshell — quoted literals excluded) or cannot
    be parsed with confidence (fail-closed; the legacy substring scan
    returned False there — a registered tightening).

    A ``watch`` payload is ALSO structure for this screen's purpose: watch
    re-parses its argv through sh -c, so structure quoted-literal HERE is
    real syntax THERE (design doc P2). The skip_guard caller pairs this
    screen with an argv-level judge that strips wrappers without modelling
    the re-parse, so the payload check must live here.
    """
    if not has_shell_structure(command):
        return False
    root = parse_script(command)
    if _structure_reason(root, allow_pipes=False, tail=_TAIL_HOST) is not None:
        return True
    for seg in root.segments:
        if isinstance(seg.command, ScriptFacts):
            continue  # subshell — already flagged by _structure_reason
        tokens = [word_token(w) for w in _segment_words(seg.command)]
        payload = _watch_payload(tokens)
        if payload is None:
            continue
        joined = " ".join(payload)
        if joined.strip() and contains_shell_metachar_facts(joined):
            return True
    return False
