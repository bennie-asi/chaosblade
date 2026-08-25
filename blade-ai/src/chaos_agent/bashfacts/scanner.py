"""Quote-aware lexical scanner (design doc 4.3).

Hand-written iterative scanner — no regex anywhere in the lexical layer.
Produces raw tokens over ``source[start:end)`` with parts materialized as
mutable skeletons; the parser (parser.py) turns subst skeletons into nested
ScriptFacts. Keeping this module free of any parser import is what lets the
two layers stay separately testable.

Lexical semantics:
  - single quotes: fully literal, no escape processing;
  - double quotes: only ``$``, backtick, ``\\``, ``"`` are special;
  - ANSI-C ``$'...'``: decoded per the C escape table, never re-parsed;
  - backslash outside quotes: escapes the next char (value-level);
  - heredoc bodies are read at the newline following the command line,
    ``<<-`` strips leading tabs, a quoted delimiter makes the body literal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .facts import ParseIssue, IssueKind, PartKind

# Token / part kinds are plain strings here; the parser maps them to facts.
T_WORD = "word"
T_OP = "op"            # | |& ; && || & \n
T_REDIR = "redir"      # > >> < << <<- <<< >& <& >| &> &>>
T_SUBSHELL = "subshell"  # ( ... ) — body range carried for recursion

OPERATORS = ("|&", "&&", "||", "|", ";", "&", "\n")
# longest-first: ``&>>`` must precede ``&>`` (startswith matching). ``&>`` /
# ``&>>`` do NOT appear here — they start with ``&`` and are handled by the
# dedicated branch in ``_match_redir`` (which runs BEFORE ``_match_op`` so
# ``echo &>>/log`` is one redirect, never a phantom background + redirect).
REDIR_OPS = ("<<-", "<<<", "<<", ">>", "<>", ">&", "<&", ">|", ">", "<")
_METACHARS = set(" \t|;&<>\n")

_C_ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
    "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
}


@dataclass
class RawPart:
    kind: PartKind
    pos: int
    end: int
    text: str
    value: str = ""
    children: list["RawPart"] = field(default_factory=list)
    body_start: int = -1          # subst body range in the ROOT source
    body_end: int = -1            # (-1, -1) when the part has no body
    decoded_body: str | None = None  # decoded backtick body (iron-rule-1
    # exception: that nested script is parsed over the decoded text and its
    # offsets index the decoded text, never the root source)


@dataclass
class RawWord:
    pos: int
    end: int
    text: str
    parts: list[RawPart] = field(default_factory=list)


@dataclass
class Token:
    kind: str
    pos: int
    end: int
    text: str
    word: RawWord | None = None
    op: str = ""
    redir_op: str = ""
    redir_fd: int | None = None
    heredoc_body: RawWord | None = None      # filled for << / <<- tokens
    heredoc_quoted: bool = False
    body_start: int = -1                     # T_SUBSHELL body range
    body_end: int = -1


@dataclass
class _Heredoc:
    delim: str
    quoted: bool
    strip_tabs: bool
    token: Token


class Scanner:
    def __init__(self, source: str, issues: list[ParseIssue]):
        self.s = source
        self.issues = issues                  # shared root-level collector
        self._pending: list[_Heredoc] = []

    # ------------------------------------------------------------ public
    def scan_tokens(self, start: int, end: int) -> list[Token]:
        """Tokenize ``source[start:end)``. ``end`` is the hard boundary
        (a nested subst body uses its close-paren position as ``end``)."""
        tokens: list[Token] = []
        pos = start
        while pos < end:
            ch = self.s[pos]
            if ch in " \t":
                pos += 1
                continue
            if ch == "#":
                # the main loop is ALWAYS at a word start (scan_word consumes
                # mid-word ``#`` as literal), so ``#`` here always begins a
                # bash comment — including after a word (``echo a # c``) and
                # after a redirect operator (``> #f``, which then correctly
                # reports a missing redirect target, as bash does).
                nl = self.s.find("\n", pos, end)
                pos = end if nl == -1 else nl
                continue
            redir = self._match_redir(pos, end)
            if redir is not None:
                op_text, fd, npos = redir
                tok = Token(T_REDIR, pos, npos, self.s[pos:npos],
                            redir_op=op_text, redir_fd=fd)
                tokens.append(tok)
                pos = npos
                if op_text in ("<<", "<<<", "<<-"):
                    # delimiter word follows; registered when that word scans
                    self._pending.append(_Heredoc("", False, op_text == "<<-", tok))
                continue
            op = self._match_op(pos, end)
            if op is not None:
                tok = Token(T_OP, pos, pos + len(op), op, op=op)
                tokens.append(tok)
                pos += len(op)
                if op == "\n" and self._pending:
                    pos = self._read_heredoc_bodies(pos, end)
                continue
            if ch == "(" and self._at_word_boundary(tokens):
                close = self._match_parens(pos, end)
                if close is None:
                    self.issues.append(ParseIssue(
                        "unbalanced subshell ( ... )", pos,
                        IssueKind.UNBALANCED_SUBST))
                    close = end
                tokens.append(Token(
                    T_SUBSHELL, pos, close, self.s[pos:close],
                    body_start=pos + 1, body_end=max(pos + 1, close - 1)))
                pos = close
                continue
            if ch == ")":
                self.issues.append(ParseIssue(
                    "unmatched ')'", pos, IssueKind.UNKNOWN_SYNTAX))
                pos += 1
                continue
            word, pos = self.scan_word(pos, end)
            if word is None:  # defensive: always make progress
                pos += 1
                continue
            tokens.append(Token(T_WORD, word.pos, word.end, word.text, word=word))
            # FIFO: the delimiter belongs to the OLDEST pending heredoc still
            # missing one (``cat << A << B`` assigns A then B, in order)
            hd = next((h for h in self._pending if h.delim == ""), None)
            if hd is not None:
                hd.delim = self._heredoc_delim(word)
                hd.quoted = any(
                    p.kind in (PartKind.SINGLE_QUOTED, PartKind.DOUBLE_QUOTED)
                    for p in word.parts
                )
                if hd.token.redir_op == "<<<":
                    hd.token.heredoc_body = word  # here-string: word IS body
                    self._pending.remove(hd)
        return tokens

    # ------------------------------------------------------------ operators
    def _at_word_boundary(self, tokens: list[Token]) -> bool:
        return not tokens or tokens[-1].kind in (T_OP, T_REDIR)

    def _match_op(self, pos: int, end: int) -> str | None:
        for op in OPERATORS:
            if self.s.startswith(op, pos) and pos + len(op) <= end:
                if op in ("|", "&") and self.s.startswith(op * 2, pos):
                    continue  # longer form already matched first
                return op
        return None

    def _match_redir(self, pos: int, end: int) -> tuple[str, int | None, int] | None:
        # ``&>`` / ``&>>`` start with ``&`` — matched here, before the
        # operator path, so they never split into phantom background + redirect
        if self.s.startswith("&>", pos) and pos + 2 <= end:
            if self.s.startswith("&>>", pos) and pos + 3 <= end:
                return "&>>", None, pos + 3
            return "&>", None, pos + 2
        fd: int | None = None
        p = pos
        # optional fd prefix immediately followed by a redir operator
        if self.s[p].isdigit():
            q = p
            while q < end and self.s[q].isdigit():
                q += 1
            if q < end and self.s[q] in "<>":
                fd = int(self.s[p:q])
                p = q
        if self.s[p] not in "<>" or (p > pos and fd is None):
            return None
        # ``<(`` / ``>(`` is a process substitution word, not a redirect
        for op in REDIR_OPS:
            if self.s.startswith(op, p) and p + len(op) <= end:
                if op in (">", "<") and p + 1 < end and self.s[p + 1] == "(":
                    return None
                return op, fd, p + len(op)
        return None

    # ------------------------------------------------------------ heredoc
    def _heredoc_delim(self, word: RawWord) -> str:
        # delimiter = the word after QUOTE REMOVAL (bash never expands it).
        # Literal/ANSI-C parts contribute their decoded value; DQ/LOCALE
        # parts contribute their raw INNER text — a DQ part's scanner-level
        # value is not folded yet ("" by default), so joining ``p.value``
        # computed an EMPTY delimiter for the common ``<<"EOF"`` form and
        # mis-swallowed the body (found in the 2026-08-17 self-review).
        out: list[str] = []
        for p in word.parts:
            if p.kind in (PartKind.LITERAL, PartKind.SINGLE_QUOTED,
                          PartKind.ANSI_C_QUOTED):
                out.append(p.value)
            elif p.kind in (PartKind.DOUBLE_QUOTED, PartKind.LOCALE_QUOTED):
                t = p.text
                if t.startswith('$"'):
                    t = t[2:]
                elif t.startswith('"'):
                    t = t[1:]
                if t.endswith('"') and t:
                    t = t[:-1]
                out.append(t)
            else:
                out.append(p.text)
        return "".join(out)

    def _read_heredoc_bodies(self, pos: int, end: int) -> int:
        for hd in self._pending:
            body_start = pos
            cur = pos
            found = False
            while cur <= end:
                nl = self.s.find("\n", cur, end)
                line_end = end if nl == -1 else nl
                line = self.s[cur:line_end]
                if hd.strip_tabs:
                    line = line.lstrip("\t")
                if line == hd.delim:
                    found = True
                    break
                if nl == -1:
                    break
                cur = nl + 1
            if not found:
                self.issues.append(ParseIssue(
                    f"unterminated heredoc (delimiter {hd.delim!r} never seen)",
                    body_start, IssueKind.UNKNOWN_SYNTAX))
                body_end, pos = end, end
            else:
                body_end = cur
                pos = self.s.find("\n", cur, end)
                pos = end if pos == -1 else pos + 1
            hd.token.heredoc_body = self._scan_heredoc_body(
                body_start, body_end, hd.quoted)
            hd.token.heredoc_quoted = hd.quoted
        self._pending = [h for h in self._pending if h.token.heredoc_body is None]
        return pos

    def _scan_heredoc_body(self, start: int, end: int, quoted: bool) -> RawWord:
        text = self.s[start:end]
        word = RawWord(start, end, text)
        if quoted or not text:
            word.parts.append(RawPart(PartKind.LITERAL, start, end, text, value=text))
            return word
        # unquoted heredoc body follows double-quote semantics
        parts, _, _ = self._scan_dq_like(start, end, quote_end=None)
        word.parts = parts
        return word

    # ------------------------------------------------------------ words
    def scan_word(self, pos: int, end: int) -> tuple[RawWord | None, int]:
        start = pos
        parts: list[RawPart] = []
        lit_start = -1  # pending LITERAL accumulation
        lit_value: list[str] = []

        def flush_lit(upto: int) -> None:
            nonlocal lit_start
            if lit_start >= 0:
                parts.append(RawPart(
                    PartKind.LITERAL, lit_start, upto,
                    self.s[lit_start:upto], value="".join(lit_value)))
                lit_start = -1
                lit_value.clear()

        while pos < end:
            ch = self.s[pos]
            if ch in _METACHARS and not (
                ch in "<>" and pos + 1 < end and self.s[pos + 1] == "("
            ):
                break
            if ch == "\\":
                if pos + 1 >= end:
                    if lit_start < 0:
                        lit_start = pos
                    lit_value.append("\\")
                    pos += 1
                    continue
                nxt = self.s[pos + 1]
                if lit_start < 0:
                    lit_start = pos
                lit_value.append("" if nxt == "\n" else nxt)
                pos += 2
                continue
            if ch == "'":
                flush_lit(pos)
                part, pos = self._scan_sq(pos, end)
                parts.append(part)
                continue
            if ch == '"':
                flush_lit(pos)
                part, pos = self._scan_dq(pos, end)
                parts.append(part)
                continue
            if ch == "$":
                part, npos = self._scan_dollar(pos, end)
                if part is not None:
                    flush_lit(pos)
                    parts.append(part)
                    pos = npos
                    continue
                if lit_start < 0:
                    lit_start = pos
                lit_value.append("$")
                pos += 1
                continue
            if ch == "`":
                flush_lit(pos)
                part, pos = self._scan_backtick(pos, end)
                parts.append(part)
                continue
            if ch in "<>" and pos + 1 < end and self.s[pos + 1] == "(":
                flush_lit(pos)
                part, pos = self._scan_process_subst(pos, end)
                parts.append(part)
                continue
            if ch in "()":
                # A bare unquoted paren is NEVER valid in the supported
                # subset (no case/array/function-def syntax; extglob off):
                # bash rejects ``echo foo(bar)`` / ``ls (`` as syntax errors.
                # Flag it so callers fail closed. The char still accumulates
                # as a literal so part-level reconstruction stays lossless.
                self.issues.append(ParseIssue(
                    f"unexpected '{ch}' outside quotes/expansions", pos,
                    IssueKind.UNKNOWN_SYNTAX))
            if lit_start < 0:
                lit_start = pos
            lit_value.append(ch)
            pos += 1

        flush_lit(pos)
        if pos == start and not parts:
            return None, pos + 1
        return RawWord(start, pos, self.s[start:pos], parts), pos

    # ------------------------------------------------------------ quotes
    def _scan_sq(self, pos: int, end: int) -> tuple[RawPart, int]:
        close = self.s.find("'", pos + 1, end)
        if close == -1:
            self.issues.append(ParseIssue(
                "unterminated single quote", pos, IssueKind.UNTERMINATED_QUOTE))
            text = self.s[pos:end]
            return RawPart(PartKind.SINGLE_QUOTED, pos, end, text,
                           value=text[1:]), end
        text = self.s[pos:close + 1]
        return RawPart(PartKind.SINGLE_QUOTED, pos, close + 1, text,
                       value=self.s[pos + 1:close]), close + 1

    def _scan_dq(self, pos: int, end: int) -> tuple[RawPart, int]:
        children, close_pos, terminated = self._scan_dq_like(
            pos + 1, end, quote_end='"')
        if not terminated:
            self.issues.append(ParseIssue(
                "unterminated double quote", pos, IssueKind.UNTERMINATED_QUOTE))
        text = self.s[pos:close_pos]
        return RawPart(PartKind.DOUBLE_QUOTED, pos, close_pos, text,
                       children=children), close_pos

    def _find_dq_close(self, pos: int, end: int) -> int:
        p = pos
        while p < end:
            ch = self.s[p]
            if ch == "\\":
                p += 2
                continue
            if ch == '"':
                return p + 1
            p += 1
        return end

    def _scan_dq_like(self, start: int, end: int,
                      quote_end: str | None
                      ) -> tuple[list[RawPart], int, bool]:
        """Double-quote-semantics body scan: only ``$`` backtick ``\\`` (and
        the closing quote when given) are special. Also used for unquoted
        heredoc bodies and arithmetic/${} bodies (quote_end=None).
        Returns ``(children, stop_pos, terminated)`` — terminated is True
        only when the scan stopped ON the closing quote; a trailing escaped
        quote (``"a\\"``) consumes the quote as content and reports False,
        so callers must trust this flag, never the last character."""
        children: list[RawPart] = []
        lit_start = -1
        lit_value: list[str] = []
        pos = start

        def flush(upto: int) -> None:
            nonlocal lit_start
            if lit_start >= 0:
                children.append(RawPart(
                    PartKind.LITERAL, lit_start, upto,
                    self.s[lit_start:upto], value="".join(lit_value)))
                lit_start = -1
                lit_value.clear()

        while pos < end:
            ch = self.s[pos]
            if quote_end is not None and ch == quote_end:
                flush(pos)
                # the closing quote is the DELIMITER: it stays in the part's
                # text (losslessness lives at part level) but is NOT a child —
                # children are the semantic decomposition of the content.
                return children, pos + 1, True
            if ch == "\\":
                nxt = self.s[pos + 1] if pos + 1 < end else ""
                if nxt in ('$', '`', '"', "\\", "\n"):
                    if lit_start < 0:
                        lit_start = pos
                    lit_value.append("" if nxt == "\n" else nxt)
                    pos += 2
                    continue
                if lit_start < 0:
                    lit_start = pos
                lit_value.append("\\")
                pos += 1
                continue
            if ch == "$":
                part, npos = self._scan_dollar(pos, end)
                if part is not None:
                    flush(pos)
                    children.append(part)
                    pos = npos
                    continue
                if lit_start < 0:
                    lit_start = pos
                lit_value.append("$")
                pos += 1
                continue
            if ch == "`":
                flush(pos)
                part, pos = self._scan_backtick(pos, end)
                children.append(part)
                continue
            if lit_start < 0:
                lit_start = pos
            lit_value.append(ch)
            pos += 1
        flush(pos)
        return children, pos, False

    # ------------------------------------------------------------ expansions
    def _scan_dollar(self, pos: int, end: int) -> tuple[RawPart | None, int]:
        nxt = self.s[pos + 1] if pos + 1 < end else ""
        if nxt == "(":
            if pos + 2 < end and self.s[pos + 2] == "(":
                return self._scan_arith(pos, end)
            return self._scan_command_subst(pos, end)
        if nxt == "{":
            return self._scan_brace_param(pos, end)
        if nxt == "'":
            return self._scan_ansi_c(pos, end)
        if nxt == '"':
            dq, npos = self._scan_dq(pos + 1, end)
            # value stays "" — like DOUBLE_QUOTED, the parser folds it from
            # children ONLY when every child is literal-class, so a subst
            # inside $"..." never collapses into a fake literal value
            return RawPart(PartKind.LOCALE_QUOTED, pos, npos,
                           self.s[pos:npos], children=dq.children), npos
        if nxt == "[":
            close = self._match_bracket(pos + 1, "[", "]", end)
            return self._arith_part(pos, close, end)
        if nxt.isalpha() or nxt == "_":
            p = pos + 1
            while p < end and (self.s[p].isalnum() or self.s[p] == "_"):
                p += 1
            return RawPart(PartKind.PARAM_EXPANSION, pos, p,
                           self.s[pos:p]), p
        if nxt.isdigit() or nxt in "!@#*?$-":
            return RawPart(PartKind.PARAM_EXPANSION, pos, pos + 2,
                           self.s[pos:pos + 2]), pos + 2
        return None, pos  # lone ``$`` is a literal — caller handles

    def _scan_command_subst(self, pos: int, end: int) -> tuple[RawPart, int]:
        close = self._match_parens(pos + 1, end)  # index past the '('
        if close is None:
            self.issues.append(ParseIssue(
                "unbalanced $( ... )", pos, IssueKind.UNBALANCED_SUBST))
            close = end
        return RawPart(PartKind.COMMAND_SUBST, pos, close, self.s[pos:close],
                       body_start=pos + 2, body_end=max(pos + 2, close - 1)), close

    def _scan_process_subst(self, pos: int, end: int) -> tuple[RawPart, int]:
        close = self._match_parens(pos + 1, end)
        if close is None:
            self.issues.append(ParseIssue(
                "unbalanced process substitution", pos,
                IssueKind.UNBALANCED_SUBST))
            close = end
        return RawPart(PartKind.PROCESS_SUBST, pos, close, self.s[pos:close],
                       body_start=pos + 2, body_end=max(pos + 2, close - 1)), close

    def _scan_arith(self, pos: int, end: int) -> tuple[RawPart, int]:
        close = self._match_parens(pos + 2, end, depth=2)  # $(( opens two
        if close is None:
            self.issues.append(ParseIssue(
                "unbalanced $(( ... ))", pos, IssueKind.UNBALANCED_SUBST))
            close = end
        return self._arith_part(pos, close, end)

    def _arith_part(self, pos: int, close: int, end: int) -> tuple[RawPart, int]:
        inner_start = pos + (3 if self.s.startswith("$((", pos) else 2)
        inner_end = max(inner_start, close - (2 if self.s.startswith("$((", pos) else 1))
        # scan the arithmetic body with DQ semantics so embedded command
        # substitutions surface as children (design 4.1: never parse the
        # arithmetic grammar, always expose the substs)
        children, _, _ = self._scan_dq_like(inner_start, min(inner_end, end),
                                            quote_end=None)
        return RawPart(PartKind.ARITH_EXPANSION, pos, close,
                       self.s[pos:close], children=children), close

    def _scan_brace_param(self, pos: int, end: int) -> tuple[RawPart, int]:
        close = self._match_bracket(pos + 1, "{", "}", end)
        if close is None:
            self.issues.append(ParseIssue(
                "unbalanced ${ ... }", pos, IssueKind.UNBALANCED_SUBST))
            close = end
        # operators (:-  //  @ ...) are recorded, never interpreted — but the
        # body is scanned with DQ semantics so a nested subst (``${x:-$(c)}``)
        # still surfaces structurally instead of hiding inside the text
        children, _, _ = self._scan_dq_like(pos + 2,
                                            max(pos + 2, close - 1),
                                            quote_end=None)
        return RawPart(PartKind.PARAM_EXPANSION, pos, close,
                       self.s[pos:close], children=children), close

    def _scan_ansi_c(self, pos: int, end: int) -> tuple[RawPart, int]:
        # $'...' — C escape table decode; produces no nesting (4.4)
        p = pos + 2
        value: list[str] = []
        terminated = False
        while p < end:
            ch = self.s[p]
            if ch == "'":
                terminated = True
                p += 1
                break
            if ch == "\\" and p + 1 < end:
                esc = self.s[p + 1]
                if esc == "x":
                    hexs = self.s[p + 2:p + 4]
                    if all(c in "0123456789abcdefABCDEF" for c in hexs) and hexs:
                        value.append(chr(int(hexs, 16)))
                        p += 2 + len(hexs)
                        continue
                if esc in "0123":
                    octs = self.s[p + 1:p + 4]
                    octs = "".join(c for c in octs if c in "01234567")
                    value.append(chr(int(octs, 8)))
                    p += 1 + len(octs)
                    continue
                if esc == "u":
                    hexs = self.s[p + 2:p + 6]
                    if len(hexs) == 4 and all(
                            c in "0123456789abcdefABCDEF" for c in hexs):
                        value.append(chr(int(hexs, 16)))
                        p += 6
                        continue
                value.append(_C_ESCAPES.get(esc, "\\" + esc))
                p += 2
                continue
            value.append(ch)
            p += 1
        if not terminated:
            self.issues.append(ParseIssue(
                "unterminated ANSI-C quote", pos, IssueKind.UNTERMINATED_QUOTE))
            p = end
        return RawPart(PartKind.ANSI_C_QUOTED, pos, p, self.s[pos:p],
                       value="".join(value)), p

    def _scan_backtick(self, pos: int, end: int) -> tuple[RawPart, int]:
        p = pos + 1
        raw: list[str] = []
        terminated = False
        while p < end:
            ch = self.s[p]
            if ch == "\\" and p + 1 < end and self.s[p + 1] in ('`', '$', "\\"):
                raw.append(self.s[p:p + 2])
                p += 2
                continue
            if ch == "`":
                terminated = True
                p += 1
                break
            raw.append(ch)
            p += 1
        if not terminated:
            self.issues.append(ParseIssue(
                "unterminated backtick", pos, IssueKind.UNTERMINATED_QUOTE))
            p = end
        raw_body = "".join(raw)
        # escape-decoded body — the nested script is parsed over the DECODED
        # text (iron-rule-1 documented exception); one decode level per layer
        decoded = raw_body.replace("\\`", "`").replace("\\$", "$") \
            .replace("\\\\", "\\")
        return RawPart(PartKind.BACKTICK_SUBST, pos, p, self.s[pos:p],
                       decoded_body=decoded), p

    # ------------------------------------------------------------ matching
    def _match_parens(self, open_paren: int, end: int,
                      depth: int = 1) -> int | None:
        """Quote-aware paren matcher. ``open_paren`` indexes the LAST opening
        paren already counted into ``depth``; scanning starts AFTER it.
        Returns the index just past the matching closer, None if unbalanced.

        Comment-aware (design 4.10 D10): a ``#`` at word-start position —
        body start, or right after a blank / newline / ``;|&<>(`` — hides
        everything up to the next newline, including ``)``. Residual: only a
        ``#`` immediately after ``)`` is still treated as a literal, because
        the closer of ``$(( ))`` / ``$( )`` is word content and a trailing
        ``#`` there continues the word — getting that wrong would
        false-reject legal arithmetic forms. (A ``#`` after an escaped blank
        IS comment-skipped — the lookbehind sees the raw blank — which
        matches bash: ``$(echo a\\ #)`` is a syntax error there too.)
        The residual direction is fail-closed (an unmatched-paren issue),
        never a hidden structure."""
        p = open_paren + 1
        while p < end:
            ch = self.s[p]
            if ch == "'":
                close = self.s.find("'", p + 1, end)
                p = end if close == -1 else close + 1
                continue
            if ch == '"':
                p = self._find_dq_close(p + 1, end)
                continue
            if ch == "\\":
                p += 2
                continue
            if ch == "`":
                close = self._match_backtick_pos(p, end)
                p = end if close is None else close
                continue
            if ch == "#" and (p == open_paren + 1
                              or self.s[p - 1] in " \t\n;|&<>("):
                nl = self.s.find("\n", p, end)
                p = end if nl == -1 else nl
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return p + 1
            p += 1
        return None

    def _match_backtick_pos(self, pos: int, end: int) -> int | None:
        p = pos + 1
        while p < end:
            if self.s[p] == "\\":
                p += 2
                continue
            if self.s[p] == "`":
                return p + 1
            p += 1
        return None

    def _match_bracket(self, pos: int, open_ch: str, close_ch: str,
                       end: int) -> int | None:
        depth = 1
        p = pos + 1
        while p < end:
            ch = self.s[p]
            if ch == "'":
                close = self.s.find("'", p + 1, end)
                p = end if close == -1 else close + 1
                continue
            if ch == '"':
                p = self._find_dq_close(p + 1, end)
                continue
            if ch == "\\":
                p += 2
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return p + 1
            p += 1
        return None
