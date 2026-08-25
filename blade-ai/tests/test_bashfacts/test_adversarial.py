"""5.4 adversarial structural assertions — the fact-layer half.

Each case asserts the STRUCTURE the fact layer must expose (so the Phase-2
policy layer can make its allow/deny call on facts, never on substrings).
Judgment-direction assertions (allow/deny + reason category) belong to the
surface wiring phase; here we pin the facts those decisions will rest on.
"""
from __future__ import annotations

from chaos_agent.bashfacts import (
    IssueKind,
    PartKind,
    has_shell_structure,
    parse_script,
    unwrap_sh_c,
)
from chaos_agent.bashfacts.facts import ScriptFacts


def _kinds(root: ScriptFacts) -> list[str]:
    return [e.kind.value for e in root.errors]


def _arg(root: ScriptFacts, i: int):
    cmd = root.segments[0].command
    return cmd.args[i]


class TestSubstitutionVisibility:
    def test_wrapper_arg_subst_is_literal_fact(self):
        """watch echo '$(rm -rf /)' — single quotes make the payload a LITERAL
        at the bash level; the danger is watch re-parsing argv via sh -c, and
        THAT second pass is the policy layer's SHELL_EXEC_WRAPPER concern.
        The fact layer must deliver the intact payload VALUE (no quote loss)
        so the policy can re-scan it."""
        root = parse_script("watch echo '$(rm -rf /)'")
        w = _arg(root, 1)
        assert w.is_plain_literal
        assert w.value == "$(rm -rf /)"
        assert [p.kind for p in w.parts] == [PartKind.SINGLE_QUOTED]
        # and when a policy re-parses that value as a script, the payload
        # is fully visible structurally: the word IS a command subst whose
        # nested script's command is ``rm``
        inner = parse_script(w.value)
        name_word = inner.segments[0].command.name
        subst = name_word.parts[0]
        assert subst.kind is PartKind.COMMAND_SUBST
        nested = subst.script
        assert nested is not None
        assert nested.segments[0].command.name.value == "rm"

    def test_arith_expansion_hides_nothing(self):
        root = parse_script("echo $(( $(id) + 1 ))")
        w = _arg(root, 0)
        assert [p.kind for p in w.parts] == [PartKind.ARITH_EXPANSION]
        subst = [c for c in w.parts[0].children
                 if c.kind is PartKind.COMMAND_SUBST]
        assert len(subst) == 1 and subst[0].script is not None
        assert not w.is_plain_literal

    def test_quote_concatenation_keeps_structure(self):
        root = parse_script('echo "$(id)"x')
        w = _arg(root, 0)
        assert [p.kind for p in w.parts] == [PartKind.DOUBLE_QUOTED,
                                             PartKind.LITERAL]
        assert not w.is_plain_literal
        dq = w.parts[0]
        assert any(c.kind is PartKind.COMMAND_SUBST for c in dq.children)

    def test_backtick_nested_script_uses_decoded_source(self):
        root = parse_script("echo `id`")
        w = _arg(root, 0)
        bt = w.parts[0]
        assert bt.kind is PartKind.BACKTICK_SUBST
        assert bt.script is not None
        # the documented iron-rule-1 exception: the nested script's source
        # IS the decoded body, and its offsets index that decoded text
        assert bt.script.source == "id"
        assert bt.script.segments[0].command.name.text == "id"

    def test_process_subst_both_directions(self):
        root = parse_script("diff <(ls /) >(cat > /dev/null)")
        args = root.segments[0].command.args
        assert [p.kind for a in args for p in a.parts] == [
            PartKind.PROCESS_SUBST, PartKind.PROCESS_SUBST]
        assert all(a.parts[0].script is not None for a in args)
        # the OUTPUT subst's inner redirect must be visible in its own script
        out_script = args[1].parts[0].script
        inner_redirects = out_script.segments[0].command.redirects
        assert [r.operator for r in inner_redirects] == [">"]

    def test_heredoc_unquoted_body_expands_quoted_body_literal(self):
        root = parse_script("cat <<EOF\n$(id)\nEOF")
        red = root.segments[0].command.redirects[0]
        assert not red.heredoc_quoted
        assert any(p.kind is PartKind.COMMAND_SUBST for p in red.body.parts)

        root2 = parse_script("cat <<'EOF'\n$(id)\nEOF")
        red2 = root2.segments[0].command.redirects[0]
        assert red2.heredoc_quoted
        assert all(p.is_literal_class() for p in red2.body.parts)


class TestShellUnwrapChain:
    def test_three_layer_sh_c_reaches_payload(self):
        root = parse_script("sh -c 'sh -c \"sh -c \\\"id\\\"\"'")

        def words_of(script):
            cmd = script.segments[0].command
            return [cmd.name] + list(cmd.args)

        lvl1 = unwrap_sh_c(words_of(root))
        assert lvl1 is not None and not lvl1.errors
        lvl2 = unwrap_sh_c(words_of(lvl1))
        assert lvl2 is not None and not lvl2.errors
        lvl3 = unwrap_sh_c(words_of(lvl2))
        assert lvl3 is not None
        assert lvl3.segments[0].command.name.value == "id"

    def test_unwrap_rejects_non_shell(self):
        root = parse_script("echo hello")
        cmd = root.segments[0].command
        assert unwrap_sh_c([cmd.name] + list(cmd.args)) is None

    def test_unwrap_rejects_extra_operands(self):
        # sh -c script name args... — the $0/args shape is left for policy
        root = parse_script("sh -c 'echo hi' extra")
        cmd = root.segments[0].command
        assert unwrap_sh_c([cmd.name] + list(cmd.args)) is None


class TestAnsiC:
    def test_ansi_c_literal_arg_decodes_once(self):
        root = parse_script("echo $'\\x3b'")   # decodes to ';'
        w = _arg(root, 0)
        assert w.is_plain_literal
        assert w.value == ";"
        assert [p.kind for p in w.parts] == [PartKind.ANSI_C_QUOTED]

    def test_ansi_c_semicolon_is_not_a_separator(self):
        root = parse_script("echo $'a;b'")
        assert len(root.segments) == 1  # the decoded ';' splits NOTHING
        assert not root.errors


class TestBudgetAndErrors:
    def test_65_layer_subst_exhausts_budget(self):
        from chaos_agent.bashfacts import MAX_NESTING
        assert MAX_NESTING == 64  # the documented budget
        root = parse_script("echo " + "$(" * 65 + "id" + ")" * 65)
        assert root.budget_exhausted
        assert IssueKind.BUDGET_EXCEEDED.value in _kinds(root)

    def test_compound_keyword_is_unknown_syntax(self):
        root = parse_script("if true; then id; fi")
        assert IssueKind.UNKNOWN_SYNTAX.value in _kinds(root)

    def test_dangling_pipe_is_unknown_syntax(self):
        root = parse_script("cat /a |")
        assert IssueKind.UNKNOWN_SYNTAX.value in _kinds(root)

    def test_unterminated_quote(self):
        root = parse_script("cat '/etc")
        assert IssueKind.UNTERMINATED_QUOTE.value in _kinds(root)

    def test_redirect_missing_target(self):
        root = parse_script("cat /a >")
        assert IssueKind.INVALID_REDIRECT.value in _kinds(root)

    def test_internal_error_is_truthful(self, monkeypatch):
        """4.5: the parser's OWN failure must surface as INTERNAL_ERROR —
        never disguised as the command's syntax problem."""
        import chaos_agent.bashfacts.parser as parser_mod

        def boom(*a, **k):
            raise RuntimeError("injected parser bug")

        monkeypatch.setattr(parser_mod, "_parse_range", boom)
        root = parser_mod.parse_script("cat /etc/passwd")
        assert len(root.errors) == 1
        assert root.errors[0].kind is IssueKind.INTERNAL_ERROR
        assert "guard's own failure" in root.errors[0].message


class TestAwkProgramStrings:
    """The P1 pair: at the FACT layer both forms have zero bash redirects;
    telling them apart is the interpreter program-string guard (Phase-2
    policy, 2.2). Facts must not invent a redirect that bash does not see."""

    def test_awk_comparison_has_no_redirect(self):
        root = parse_script("awk 'NR>1{print $1}' /etc/passwd")
        assert not root.errors
        assert not root.segments[0].command.redirects
        prog = root.segments[0].command.args[0]
        assert prog.value == "NR>1{print $1}"

    def test_awk_write_also_has_no_bash_redirect(self):
        root = parse_script("awk '{print > \"/etc/cron.d/evil\"}' /tmp/x")
        assert not root.errors
        assert not root.segments[0].command.redirects
        # and the program string arrives intact for the policy layer
        prog = root.segments[0].command.args[0]
        assert prog.value == '{print > "/etc/cron.d/evil"}'


class TestReviewFixes20260817:
    """Regression pins for the 2026-08-17 self-review findings — each test
    names the bug it locks so a future refactor cannot silently regress."""

    def test_amp_redirect_is_one_token_not_phantom_background(self):
        # B1: `&>>` once split into a background `&` plus a phantom `>>`
        # segment (the fix branch itself had the literals transposed and
        # still matched `>&` instead — caught by probe before shipping)
        root = parse_script("echo &>>/log")
        assert not root.errors
        assert len(root.segments) == 1
        seg = root.segments[0]
        assert not seg.background
        cmd = seg.command
        assert cmd.name.value == "echo"
        assert [(r.operator, r.target.value) for r in cmd.redirects] == [
            ("&>>", "/log")]

    def test_fd_dup_redirect_keeps_its_label(self):
        # the transposed branch once mislabeled plain `>&` as `&>`
        root = parse_script("echo hi >&2")
        reds = root.segments[0].command.redirects
        assert [(r.operator, r.fd, r.target.value) for r in reds] == [
            (">&", None, "2")]
        root2 = parse_script("echo hi 2>&1")
        reds2 = root2.segments[0].command.redirects
        assert [(r.operator, r.fd, r.target.value) for r in reds2] == [
            (">&", 2, "1")]

    def test_all_redirect_ops_missing_target_fail_closed(self):
        # the parser once exempted >& <& &> &>> from the missing-target
        # issue; bash rejects every one of them
        for op in (">", ">>", "<", ">&", "<&", "&>", "&>>"):
            root = parse_script(f"echo hi {op}")
            assert IssueKind.INVALID_REDIRECT.value in _kinds(root), op

    def test_double_quoted_heredoc_delimiter(self):
        # <<"EOF" once computed an EMPTY delimiter (DQ parts carry no
        # scanner-level value), mis-reporting unterminated + swallowing body
        root = parse_script('cat <<"EOF"\n$(id)\nEOF')
        assert not root.errors
        red = root.segments[0].command.redirects[0]
        assert red.heredoc_quoted
        assert red.body.text == "$(id)\n"
        assert all(p.is_literal_class() for p in red.body.parts)

    def test_spaced_multi_heredoc_delims_assign_fifo(self):
        root = parse_script("cat << A << B\nbodyA\nA\nbodyB\nB\n")
        assert not root.errors
        reds = root.segments[0].command.redirects
        assert [r.body.text for r in reds] == ["bodyA\n", "bodyB\n"]

    def test_locale_quoted_subst_is_not_a_literal(self):
        # bash expands $"..." content like plain double quotes — a subst
        # inside must never collapse into a fake literal (soundness)
        root = parse_script('echo $"$(id)"')
        w = root.segments[0].command.args[0]
        assert not w.is_plain_literal
        assert w.value is None
        loc = w.parts[0]
        assert loc.kind is PartKind.LOCALE_QUOTED
        assert any(c.kind is PartKind.COMMAND_SUBST for c in loc.children)

    def test_locale_quoted_pure_text_stays_literal(self):
        root = parse_script('echo $"hello"')
        w = root.segments[0].command.args[0]
        assert w.is_plain_literal and w.value == "hello"

    def test_trailing_escaped_quote_is_unterminated(self):
        # the DQ scanner once trusted the last CHARACTER (a `"` even when
        # escaped) instead of a real terminated flag
        root = parse_script('echo "a\\"')
        assert IssueKind.UNTERMINATED_QUOTE.value in _kinds(root)

    def test_comment_positions(self):
        root = parse_script("echo foo # trailing comment")
        assert not root.errors
        cmd = root.segments[0].command
        assert [cmd.name.value] + [a.value for a in cmd.args] == [
            "echo", "foo"]
        assert not parse_script("# full line").segments
        root3 = parse_script("echo a#b")  # mid-word # stays literal
        assert root3.segments[0].command.args[0].value == "a#b"

    def test_comment_after_redirect_reports_missing_target(self):
        # bash rejects `> #f` as a missing target; so must we
        root = parse_script("echo hi > #f")
        assert IssueKind.INVALID_REDIRECT.value in _kinds(root)

    def test_unwrap_refuses_flags_before_c(self):
        # `bash --init-file /tmp/x -c id` RUNS /tmp/x before the script —
        # unwrapping to the script alone would hide half the command (both
        # legacy peel copies had this hole)
        for cmd in ("bash --init-file /tmp/e -c id", "sh -x -c id",
                    "sh -xc id"):
            root = parse_script(cmd)
            cmdf = root.segments[0].command
            assert unwrap_sh_c([cmdf.name] + list(cmdf.args)) is None, cmd

    def test_unwrap_exact_form_still_works(self):
        root = parse_script("bash -c 'id'")
        cmdf = root.segments[0].command
        inner = unwrap_sh_c([cmdf.name] + list(cmdf.args))
        assert inner is not None and not inner.errors
        assert inner.segments[0].command.name.value == "id"


class TestKnownDivergenceDirection:
    """Design 4.10: D9/D10 were FIXED after the second-order review — the
    pins below lock the fixed behavior plus the one documented residual
    (fail-closed direction is the contract)."""

    def test_read_write_open_is_a_single_redirect(self):
        # D9 fixed: <> is bash's read-write open operator, one token with
        # its target (the policy layer classifies it as write-capable)
        root = parse_script("cat <>file")
        assert not root.errors
        reds = root.segments[0].command.redirects
        assert [(r.operator, r.target.value) for r in reds] == [
            ("<>", "file")]

    def test_comment_hides_paren_inside_subst(self):
        # D10 fixed: a ) on a comment line must not close the subst
        root = parse_script("echo $(echo a # )\n)")
        assert not root.errors
        arg = root.segments[0].command.args[0]
        subst = arg.parts[0]
        assert subst.kind is PartKind.COMMAND_SUBST
        assert subst.script is not None
        assert subst.script.segments[0].command.name.value == "echo"

    def test_midword_hash_inside_subst_stays_literal(self):
        root = parse_script("echo $(echo a#b)")
        assert not root.errors
        inner = root.segments[0].command.args[0].parts[0].script
        assert inner.segments[0].command.args[0].value == "a#b"

    def test_hash_after_arith_close_stays_literal(self):
        # the )) of $(()) is word content: a trailing # continues the word,
        # it must NOT start a comment (else legal arithmetic breaks)
        root = parse_script("echo $(echo $((1+2))#c)")
        assert not root.errors

    def test_residual_hash_after_close_paren_fails_closed(self):
        # documented residual: # immediately after ) is not recognized as
        # a comment, so a ) on that line mis-closes and an issue surfaces
        root = parse_script("echo $( (true)#)\n)")
        assert root.errors

    def test_hash_after_escaped_blank_is_a_comment_like_bash(self):
        # third-order review pin: the lookbehind sees the RAW blank, so a #
        # after an escaped blank IS comment-skipped — the closer on that
        # line is swallowed, matching bash (syntax error there too)
        root = parse_script("echo $(echo a\\ #)")
        assert [i.kind for i in root.errors] == [IssueKind.UNBALANCED_SUBST]


class TestBareParensRejected:
    """An unquoted bare paren is a bash syntax error outside case / array /
    function-def syntax (an unsupported subset; extglob off). Found by the
    Phase-2 fuzz referee: ``bash -n`` rejects ``cat )`` while the paren-blind
    prescan fast path waved it through. The scanner now flags every bare
    paren (fail closed) and the prescan routes parens to the parser, while
    legitimate paren constructs — subshells, quoted content, arithmetic,
    process substitution — stay clean."""

    def test_bare_parens_are_parse_issues(self):
        for cmd in ("cat )", "ls (", "echo foo(bar)", "cat ()",
                    "echo @(a|b)", "echo foo)"):
            root = parse_script(cmd)
            assert root.errors, cmd
            assert all(e.kind is IssueKind.UNKNOWN_SYNTAX
                       for e in root.errors), cmd

    def test_legitimate_parens_stay_clean(self):
        for cmd in ("(ls)", "(df -h) | grep x", "echo 'a(b)'",
                    'echo "a(b)"', "echo $((1+2))", "diff <(ls) <(ls /tmp)"):
            assert parse_script(cmd).errors == (), cmd

    def test_paren_word_stays_lossless(self):
        cmd = "echo foo(bar)"
        root = parse_script(cmd)
        w = _arg(root, 0)
        assert w.text == "foo(bar)"
        assert "".join(p.text for p in w.parts) == w.text
        assert root.source[w.pos:w.end] == w.text

    def test_prescan_sees_parens(self):
        assert has_shell_structure("cat )")
        assert has_shell_structure("ls (")
        assert not has_shell_structure("df -h")
