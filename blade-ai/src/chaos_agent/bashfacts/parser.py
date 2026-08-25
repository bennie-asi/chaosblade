"""Recursive-descent parser (design doc 4.3).

Consumes scanner tokens, materializes RawPart skeletons into frozen facts,
recursing into subst/subshell bodies with the SHARED Budget. Never raises:
every parse failure is collected as a ParseIssue; any unexpected internal
exception is caught at the top level and reported truthfully as
INTERNAL_ERROR (never disguised as the command's syntax problem — 4.5).

Compound syntax (if/for/while/until/case/select/function/coproc/[[ ]]
/{ ...; }) is explicitly NOT parsed into structure: its appearance yields
UNKNOWN_SYNTAX and the policy layer denies (4.1 — every syntax face is an
explicit review decision).

Errors bubble: each nesting level collects a local issue list; the parent
extends its own with the child's, so the root ScriptFacts.errors aggregates
the whole tree while walk_scripts() can still locate issues layer by layer.
"""
from __future__ import annotations

from .budget import Budget
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
)
from .scanner import RawPart, RawWord, Scanner, T_OP, T_REDIR, T_SUBSHELL, T_WORD

# 4.1: compound keywords are outside the whitelisted grammar subset
_COMPOUND_KEYWORDS = frozenset({
    "if", "for", "while", "until", "case", "select", "function", "coproc",
    "[[", "]]", "{", "}", "((", "time", "!",
})

_SEQUENCE_OPS = ("|", "|&", ";", "&&", "||", "&", "\n")


def parse_script(source: str, *, budget: Budget | None = None) -> ScriptFacts:
    """Parse a bash command string into ScriptFacts. NEVER raises."""
    budget = budget if budget is not None else Budget()
    try:
        return _parse_range(source, 0, len(source), budget)
    except Exception as exc:  # noqa: BLE001 — fail-closed double insurance
        issue = ParseIssue(
            f"internal parser error (the guard's own failure, not the "
            f"command's): {type(exc).__name__}: {exc}",
            0, IssueKind.INTERNAL_ERROR)
        return ScriptFacts(source, (), (), (issue,), budget.exhausted)


def _parse_range(source: str, start: int, end: int,
                 budget: Budget) -> ScriptFacts:
    """Parse ``source[start:end)``. ``source`` is the ROOT command string so
    every pos/end indexes it directly (iron rule 1); the decoded-backtick
    body is the single documented exception (that script's source IS the
    decoded text)."""
    local: list[ParseIssue] = []
    scanner = Scanner(source, local)
    tokens = scanner.scan_tokens(start, end)
    segments: list[Segment] = []
    operators: list[str] = []
    pending_op: str | None = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == T_OP:
            if tok.op == "\n":
                if pending_op is None and segments:
                    pending_op = "\n"  # record only between two segments
                i += 1
                continue
            if not segments and pending_op is None:
                local.append(ParseIssue(
                    f"operator {tok.op!r} with no preceding command",
                    tok.pos, IssueKind.UNKNOWN_SYNTAX))
            elif pending_op is not None:
                # ``&&\n`` continuation is already absorbed by the \n branch;
                # anything reaching here is a doubled separator (``\n;``,
                # ``; &&``) which bash itself rejects
                local.append(ParseIssue(
                    f"operator {tok.op!r} follows another operator",
                    tok.pos, IssueKind.UNKNOWN_SYNTAX))
            else:
                if tok.op == "&" and segments:
                    seg = segments[-1]
                    segments[-1] = Segment(seg.command, background=True)
                pending_op = tok.op
            i += 1
            continue
        seg, i = _collect_segment(source, tokens, i, budget, local)
        if seg is not None:
            if pending_op is not None:
                operators.append(pending_op)
                pending_op = None
            segments.append(seg)

    if pending_op is not None and pending_op in ("&&", "||", "|", "|&"):
        local.append(ParseIssue(
            f"dangling operator {pending_op!r} at end of input",
            end, IssueKind.UNKNOWN_SYNTAX))
    return ScriptFacts(source, tuple(segments), tuple(operators),
                       tuple(local), budget.exhausted)


def _collect_segment(source: str, tokens: list, i: int, budget: Budget,
                     issues: list[ParseIssue]) -> tuple[Segment | None, int]:
    words: list[RawWord] = []
    redirects: list[RedirectFacts] = []
    subshell: ScriptFacts | None = None
    seg_start = tokens[i].pos
    seg_end = tokens[i].end

    j = i
    while j < len(tokens) and tokens[j].kind != T_OP:
        tok = tokens[j]
        seg_end = tok.end
        if tok.kind == T_WORD:
            words.append(tok.word)
            j += 1
            continue
        if tok.kind == T_REDIR:
            target: WordFacts | None = None
            body: WordFacts | None = None
            red_end = tok.end
            if j + 1 < len(tokens) and tokens[j + 1].kind == T_WORD:
                target = _materialize_word(tokens[j + 1].word, source,
                                           budget, issues)
                red_end = tokens[j + 1].end
                j += 1
            else:
                # EVERY redirect operator needs a target word in bash —
                # ``>&`` / ``<&`` / ``&>`` / ``&>>`` included (``echo hi >&``
                # is a syntax error). No exemptions: fail closed uniformly.
                issues.append(ParseIssue(
                    f"redirect {tok.redir_op!r} missing its target",
                    tok.pos, IssueKind.INVALID_REDIRECT))
            if tok.heredoc_body is not None:
                body = _materialize_word(tok.heredoc_body, source,
                                         budget, issues)
            redirects.append(RedirectFacts(
                tok.redir_op, target, tok.redir_fd,
                tok.heredoc_quoted, body, tok.pos, red_end))
            seg_end = red_end  # the segment span must cover redirect targets
            j += 1
            continue
        if tok.kind == T_SUBSHELL:
            if subshell is not None or words:
                issues.append(ParseIssue(
                    "subshell mixed with a simple command in one segment",
                    tok.pos, IssueKind.UNKNOWN_SYNTAX))
            if not budget.consume():
                issues.append(ParseIssue(
                    "nesting budget exceeded at subshell",
                    tok.pos, IssueKind.BUDGET_EXCEEDED))
            else:
                subshell = _parse_range(source, tok.body_start, tok.body_end,
                                        budget)
            j += 1
            continue
        j += 1  # unreachable kinds: skip defensively

    if subshell is not None:
        if redirects:
            # `( ... ) > file` is outside the reviewed whitelist subset —
            # fail closed rather than lose the redirect's structural
            # visibility inside a bare-ScriptFacts segment (4.1).
            issues.append(ParseIssue(
                "subshell with redirects is outside the supported subset",
                seg_start, IssueKind.UNKNOWN_SYNTAX))
        return Segment(subshell, background=False), j

    if not words and not redirects:
        return None, j

    name: WordFacts | None = None
    args: list[WordFacts] = []
    for k, raw in enumerate(words):
        word = _materialize_word(raw, source, budget, issues)
        if k == 0:
            name = word
        else:
            args.append(word)
    if name is not None:
        head = name.value if name.value is not None else name.text
        if head in _COMPOUND_KEYWORDS:
            issues.append(ParseIssue(
                f"compound syntax {head!r} is outside the supported subset "
                f"(whitelist grammar, design 4.1)",
                name.pos, IssueKind.UNKNOWN_SYNTAX))
    return Segment(CommandFacts(name, tuple(args), tuple(redirects),
                                seg_start, seg_end),
                   background=False), j


def _materialize_word(raw: RawWord, source: str, budget: Budget,
                      issues: list[ParseIssue]) -> WordFacts:
    return WordFacts(raw.text, raw.pos, raw.end,
                     tuple(_materialize_part(p, source, budget, issues)
                           for p in raw.parts))


def _materialize_part(raw: RawPart, source: str, budget: Budget,
                      issues: list[ParseIssue]) -> WordPart:
    children = tuple(_materialize_part(c, source, budget, issues)
                     for c in raw.children)
    # container literal kinds ("..." / $"...") carry their literal value in
    # the CHILDREN — fold it up so WordFacts.value can join parts uniformly
    # (only when every child is literal-class; with expansions inside, the
    # value is meaningless and is_plain_literal will be False anyway).
    value = raw.value
    if raw.kind in (PartKind.DOUBLE_QUOTED, PartKind.LOCALE_QUOTED) \
            and children \
            and all(c.is_literal_class() for c in children):
        value = "".join(c.value for c in children)
    script: ScriptFacts | None = None

    if raw.kind is PartKind.BACKTICK_SUBST and raw.decoded_body is not None:
        if not budget.consume():
            issues.append(ParseIssue(
                "nesting budget exceeded at backtick substitution",
                raw.pos, IssueKind.BUDGET_EXCEEDED))
        else:
            script = _parse_range(raw.decoded_body, 0,
                                  len(raw.decoded_body), budget)
            issues.extend(script.errors)  # bubble up to the root collector
    elif raw.body_start >= 0 and raw.kind in (
            PartKind.COMMAND_SUBST, PartKind.PROCESS_SUBST):
        if not budget.consume():
            issues.append(ParseIssue(
                f"nesting budget exceeded at {raw.kind.value}",
                raw.pos, IssueKind.BUDGET_EXCEEDED))
        else:
            # body indexes the ROOT source directly (iron rule 1)
            script = _parse_range(source, raw.body_start,
                                  raw.body_end, budget)
            issues.extend(script.errors)
    return WordPart(raw.kind, raw.text, raw.pos, raw.end, value,
                    children, script)
