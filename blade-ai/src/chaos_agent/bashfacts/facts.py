"""Fact data model (design doc 4.2).

Three iron rules:
  1. ABSOLUTE OFFSETS — every pos/end indexes the caller's ``source``
     directly; nothing is sliced or rebased. (The decoded-backtick body is
     the single exception: that nested script records its ``decoded_source``
     and the isolation is documented on the part.)
  2. FULLY MATERIALIZED — ``parts`` are complete before ``parse_script``
     returns. Traversal completeness for a security guard outranks unbash's
     lazy-getter optimization.
  3. LOSSLESS RECONSTRUCTION — ``"".join(part.text)`` must equal the word's
     source interval, layer by layer (locked by the 5.2 property tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator


class IssueKind(Enum):
    UNTERMINATED_QUOTE = "unterminated_quote"
    UNBALANCED_SUBST = "unbalanced_subst"
    UNKNOWN_SYNTAX = "unknown_syntax"
    BUDGET_EXCEEDED = "budget_exceeded"
    INVALID_REDIRECT = "invalid_redirect"
    INTERNAL_ERROR = "internal_error"  # parser's OWN failure — reported
    # truthfully, never disguised as the command's syntax problem (4.5)


class PartKind(Enum):
    LITERAL = "literal"
    SINGLE_QUOTED = "single_quoted"
    DOUBLE_QUOTED = "double_quoted"
    ANSI_C_QUOTED = "ansi_c_quoted"
    LOCALE_QUOTED = "locale_quoted"          # $"..."
    PARAM_EXPANSION = "param_expansion"      # $var / $1 / $! / ${...}
    COMMAND_SUBST = "command_subst"          # $(...)
    BACKTICK_SUBST = "backtick_subst"        # `...`
    ARITH_EXPANSION = "arith_expansion"      # $((...)) / $[...]
    PROCESS_SUBST = "process_subst"          # <(...) >(...)


_LITERAL_KINDS = frozenset({
    PartKind.LITERAL, PartKind.SINGLE_QUOTED, PartKind.ANSI_C_QUOTED,
})


@dataclass(frozen=True)
class WordPart:
    kind: PartKind
    text: str                      # exact source slice, source[pos:end]
    pos: int                       # absolute offset into the input string
    end: int
    value: str = ""                # unquoted/unescaped literal value
                                 # (meaningful only for literal-class parts)
    children: tuple["WordPart", ...] = ()   # expansions inside "..." / $(( ))
    script: "ScriptFacts | None" = None     # nested script of subst parts

    def is_literal_class(self) -> bool:
        """True when this part carries no executable/expandable structure.

        DOUBLE_QUOTED and LOCALE_QUOTED are literal-class only when they
        contain no expansion children — bash expands ``$"..."`` content
        exactly like plain double quotes (in C/POSIX locale the ``$`` is
        simply ignored), so ``$"$(id)"`` DOES run id and must never read
        as a literal. ANSI-C is literal (decoded once, never re-parsed).
        """
        if self.kind in _LITERAL_KINDS:
            return True
        if self.kind in (PartKind.DOUBLE_QUOTED, PartKind.LOCALE_QUOTED):
            return all(c.is_literal_class() for c in self.children)
        return False


@dataclass(frozen=True)
class WordFacts:
    text: str
    pos: int
    end: int
    parts: tuple[WordPart, ...] = field(default_factory=tuple)

    @property
    def is_plain_literal(self) -> bool:
        return bool(self.parts) and all(p.is_literal_class() for p in self.parts)

    @property
    def value(self) -> str | None:
        """Dequoted argv value. None when the word carries structure —
        policy code must then fall back to structural predicates (4.6
        value discipline: never substring-scan values for metacharacters)."""
        if not self.is_plain_literal:
            return None
        return "".join(p.value for p in self.parts)


@dataclass(frozen=True)
class RedirectFacts:
    operator: str                  # > >> < << <<- <<< <> >& <& >| &> &>>
    # NOTE for the Phase-2 policy layer: ``<>`` opens read-WRITE (creating
    # the target) — redirect classification tables must group it with the
    # write-capable operators, never with read-only ``<``.
    target: WordFacts | None       # None when missing/invalid
    fd: int | None                 # explicit fd prefix (2>...), else None
    heredoc_quoted: bool           # <<'EOF' / <<"EOF" — body is pure literal
    body: WordFacts | None         # heredoc/here-string body
    pos: int
    end: int


@dataclass(frozen=True)
class CommandFacts:
    name: WordFacts | None
    args: tuple[WordFacts, ...]
    redirects: tuple[RedirectFacts, ...]
    pos: int
    end: int


@dataclass(frozen=True)
class Segment:
    """One pipeline stage / sequence element. ``command`` is CommandFacts for
    a simple command, or ScriptFacts for a ``( ... )`` subshell."""
    command: "CommandFacts | ScriptFacts"
    background: bool


@dataclass(frozen=True)
class ParseIssue:
    message: str
    pos: int
    kind: IssueKind


@dataclass(frozen=True)
class ScriptFacts:
    source: str
    segments: tuple[Segment, ...]
    operators: tuple[str, ...]     # | ; && || & \n — aligned with segment gaps
    errors: tuple[ParseIssue, ...]  # nested scripts' errors bubble up here
    budget_exhausted: bool

    def walk_scripts(self) -> Iterator["ScriptFacts"]:
        """Mechanically enumerate self and EVERY nested script (command /
        process substitution bodies, subshells, sh -c payloads parsed into
        parts). Completeness is locked by the 5.2 property tests."""
        yield self
        for seg in self.segments:
            cmd = seg.command
            if isinstance(cmd, ScriptFacts):
                yield from cmd.walk_scripts()
                continue
            for word in self._command_words(cmd):
                yield from self._word_scripts(word)

    @staticmethod
    def _command_words(cmd: CommandFacts) -> Iterator[WordFacts]:
        if cmd.name is not None:
            yield cmd.name
        yield from cmd.args
        for red in cmd.redirects:
            if red.target is not None:
                yield red.target
            if red.body is not None:
                yield red.body

    @classmethod
    def _word_scripts(cls, word: WordFacts) -> Iterator["ScriptFacts"]:
        for part in word.parts:
            if part.script is not None:
                yield from part.script.walk_scripts()
            for child in part.children:
                yield from cls._word_scripts_child(child)

    @classmethod
    def _word_scripts_child(cls, part: WordPart) -> Iterator["ScriptFacts"]:
        if part.script is not None:
            yield from part.script.walk_scripts()
        for child in part.children:
            yield from cls._word_scripts_child(child)


def iter_parts(word: WordFacts) -> Iterator[WordPart]:
    """Depth-first part enumeration including nested children."""
    for part in word.parts:
        yield part
        yield from _iter_child_parts(part)


def _iter_child_parts(part: WordPart) -> Iterator[WordPart]:
    for child in part.children:
        yield child
        yield from _iter_child_parts(child)
