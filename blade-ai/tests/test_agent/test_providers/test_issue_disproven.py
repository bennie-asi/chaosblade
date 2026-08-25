"""issue_disproven — result-aware revocation of issue-time attribution (R1).

Issue-time (channel A) attribution commits a UID-less native method the
moment the tool call is issued, before the result exists. These tests pin
the counter-evidence semantics that revoke a proven-failed attempt:

- only TRUSTWORTHY results disprove (object-write: the API server's Error:
  proves the write never landed);
- the forensic paradox is never disprovable (command-mode exec, host-native:
  the fault may sever its own feedback channel, so an Error: result is as
  likely from a SUCCESSFUL injection);
- absence of a result (pending / severed) is never counter-evidence;
- only the LATEST attempt is judged, EXCEPT that any LANDED object-write
  confirms the attribution irrevocably (a later failure never revokes it).
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chaos_agent.agent.providers.message_scanning import scan_native_issue_disproven
from chaos_agent.agent.providers.chaosblade.provider import ChaosbladeProvider
from chaos_agent.agent.providers.chaosblade.python_provider import (
    ChaosbladePythonProvider,
)
from chaos_agent.agent.providers.host_shell.provider import HostShellProvider
from chaos_agent.agent.providers.k8s_native.provider import K8sNativeProvider

WRITE_SUBS = frozenset({"scale", "patch", "cordon"})
COMMAND_SUBS = frozenset({"exec", "debug"})


def _mutating(v_args: str) -> bool:
    return "iptables" in v_args


def _attempt(subcommand: str, v_args: str = "", tc_id: str = "tc1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "kubectl",
            "args": {"subcommand": subcommand, "v_args": v_args},
            "id": tc_id,
        }],
    )


def _result(tc_id: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tc_id, name="kubectl")


# -- scan semantics -----------------------------------------------------------

class TestScanNativeIssueDisproven:
    def test_failed_object_write_is_disproven(self):
        msgs = [
            _attempt("scale", "deployment/app --replicas=0"),
            _result("tc1", "Error: deployments.apps \"app\" not found"),
        ]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is True

    def test_successful_object_write_stands(self):
        msgs = [
            _attempt("scale", "deployment/app --replicas=0"),
            _result("tc1", "deployment.apps/app scaled"),
        ]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is False

    def test_pending_attempt_is_never_counter_evidence(self):
        """No ToolMessage yet (same-turn issue-time commit, or severed
        channel) — absence of result must not revoke."""
        msgs = [_attempt("scale", "deployment/app --replicas=0")]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is False

    def test_command_mode_attempt_shields_earlier_failed_write(self):
        """A later command-mode attempt is not judgeable (forensic paradox)
        and must not be revoked on an EARLIER failed object-write."""
        msgs = [
            _attempt("scale", "deployment/app --replicas=0", tc_id="t1"),
            _result("t1", "Error: not found"),
            _attempt("exec", "iptables -A INPUT -j DROP", tc_id="t2"),
            _result("t2", "Error: connection refused"),
        ]
        assert scan_native_issue_disproven(
            msgs, WRITE_SUBS,
            command_subcommands=COMMAND_SUBS,
            is_mutating_command=_mutating,
        ) is False

    def test_failed_write_after_success_never_disproves(self):
        """Result-born confirmation guard: the EARLIER write landed, so a live
        fault exists regardless of the later failure (a multi-step skill's
        second step, or a self-undo retry). Revoking on the latest failure
        would orphan the still-live mutation from the successful write."""
        msgs = [
            _attempt("scale", "--replicas=0", tc_id="t1"),
            _result("t1", "deployment.apps/app scaled"),
            _attempt("patch", "{}", tc_id="t2"),
            _result("t2", "Error: patch rejected"),
        ]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is False

    def test_latest_success_after_failure_stands(self):
        """A retry that landed clears the counter-evidence."""
        msgs = [
            _attempt("scale", "--replicas=0", tc_id="t1"),
            _result("t1", "Error: not found"),
            _attempt("scale", "--replicas=0", tc_id="t2"),
            _result("t2", "deployment.apps/app scaled"),
        ]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is False

    def test_parallel_calls_in_one_message_success_shields_sibling_failure(self):
        """Two writes issued in ONE AIMessage (parallel tool_calls): the
        landed one confirms the attribution even though the main scan's
        reversed order meets the sibling's Error: first."""
        parallel = AIMessage(
            content="",
            tool_calls=[
                {"name": "kubectl", "args": {"subcommand": "scale", "v_args": "--replicas=0"}, "id": "t1"},
                {"name": "kubectl", "args": {"subcommand": "patch", "v_args": "{}"}, "id": "t2"},
            ],
        )
        msgs = [
            parallel,
            _result("t1", "deployment.apps/app scaled"),
            _result("t2", "Error: patch rejected"),
        ]
        assert scan_native_issue_disproven(msgs, WRITE_SUBS) is False

    def test_blade_via_exec_is_not_a_native_attempt(self):
        """A blade delivery through exec belongs to kubectl_exec — skipped,
        the earlier failed object-write is still judged."""
        msgs = [
            _attempt("scale", "--replicas=0", tc_id="t1"),
            _result("t1", "Error: not found"),
            _attempt("exec", "blade create k8s pod-cpu fullload", tc_id="t2"),
        ]
        assert scan_native_issue_disproven(
            msgs, WRITE_SUBS,
            command_subcommands=COMMAND_SUBS,
            is_mutating_command=_mutating,
        ) is True

    def test_non_mutating_exec_is_not_an_attempt(self):
        msgs = [
            _attempt("scale", "--replicas=0", tc_id="t1"),
            _result("t1", "Error: not found"),
            _attempt("exec", "cat /etc/hosts", tc_id="t2"),
        ]
        assert scan_native_issue_disproven(
            msgs, WRITE_SUBS,
            command_subcommands=COMMAND_SUBS,
            is_mutating_command=_mutating,
        ) is True

    def test_no_attempts(self):
        assert scan_native_issue_disproven(
            [HumanMessage(content="hi")], WRITE_SUBS
        ) is False


# -- provider hooks ------------------------------------------------------------

class TestProviderIssueDisproven:
    def test_kubectl_native_object_write_disproven(self):
        p = K8sNativeProvider()
        msgs = [
            _attempt("scale", "deployment/app --replicas=0"),
            _result("tc1", "Error: not found"),
        ]
        assert p.issue_disproven(msgs) is True

    def test_kubectl_native_success_stands(self):
        p = K8sNativeProvider()
        msgs = [
            _attempt("scale", "deployment/app --replicas=0"),
            _result("tc1", "deployment.apps/app scaled"),
        ]
        assert p.issue_disproven(msgs) is False

    def test_kubectl_native_real_command_mode_is_unjudgeable(self, monkeypatch):
        """A mutating exec (e.g. an iptables drop) whose result errors out is
        the forensic paradox — never counter-evidence. The inner-command
        classifier is injected (the production one delegates to the readonly
        engine, which is configuration-dependent)."""
        import chaos_agent.agent.providers.k8s_native.provider as k8s_native_mod

        monkeypatch.setattr(
            k8s_native_mod, "exec_inner_command_mutates",
            lambda v_args: "iptables" in v_args,
        )
        p = K8sNativeProvider()
        msgs = [
            _attempt("exec", "iptables -A INPUT -j DROP"),
            _result("tc1", "Error: command terminated with exit code 137"),
        ]
        assert p.issue_disproven(msgs) is False

    def test_host_native_is_never_disprovable(self):
        """Host results are untrustworthy by nature (severed channel)."""
        p = HostShellProvider()
        msgs = [
            AIMessage(content="", tool_calls=[{
                "name": "host_inject",
                "args": {"command": "iptables -A INPUT -j DROP"},
                "id": "h1",
            }]),
            ToolMessage(content="Error: ssh connection lost",
                          tool_call_id="h1", name="host_inject"),
        ]
        assert p.issue_disproven(msgs) is False

    def test_experiment_carriers_have_nothing_to_revoke(self):
        assert ChaosbladeProvider().issue_disproven([]) is False
        assert ChaosbladePythonProvider().issue_disproven([]) is False


# -- execute-loop seams ---------------------------------------------------------

class TestRevocationSeams:
    def test_revocation_clears_attribution_facts(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _maybe_revoke_issue_time_attribution,
        )

        state = {"messages": [], "injection_method": "kubectl_native"}
        result = {}
        msgs = [
            _attempt("scale", "deployment/app --replicas=0"),
            _result("tc1", "Error: not found"),
        ]
        assert _maybe_revoke_issue_time_attribution(
            state, result, msgs, "kubectl_native"
        ) is True
        assert result["injection_method"] is None
        assert result["fault_handle"] is None
        assert result["injection_start_time"] is None

    def test_revocation_respects_epoch_boundary(self):
        """A PRE-seam failed attempt must not revoke a post-seam attribution:
        the counter-evidence scan is epoch-bounded like the RESUME scan."""
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _maybe_revoke_issue_time_attribution,
        )

        pre_seam = [
            _attempt("scale", "--replicas=0", tc_id="old"),
            _result("old", "Error: not found"),
        ]
        boundary = len(pre_seam)  # replan seam lands here
        post_seam = [_attempt("scale", "--replicas=0", tc_id="new")]
        state = {
            "messages": [],
            "injection_method": "kubectl_native",
            "attribution_epoch_index": boundary,
        }
        result = {}
        assert _maybe_revoke_issue_time_attribution(
            state, result, pre_seam + post_seam, "kubectl_native"
        ) is False
        assert "injection_method" not in result

    def test_successful_attempt_is_not_revoked(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _maybe_revoke_issue_time_attribution,
        )

        state = {"messages": [], "injection_method": "kubectl_native"}
        msgs = [
            _attempt("scale", "--replicas=0"),
            _result("tc1", "deployment.apps/app scaled"),
        ]
        assert _maybe_revoke_issue_time_attribution(
            state, {}, msgs, "kubectl_native"
        ) is False

    def test_experiment_attribution_is_never_revoked(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _maybe_revoke_issue_time_attribution,
        )

        state = {"messages": []}
        msgs = [
            _attempt("scale", "--replicas=0"),
            _result("tc1", "Error: not found"),
        ]
        assert _maybe_revoke_issue_time_attribution(
            state, {}, msgs, "host_blade"
        ) is False

    def test_resume_veto_uses_same_predicate(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _issue_disproven_in_epoch,
        )
        from chaos_agent.agent.providers import FaultProviderRegistry

        FaultProviderRegistry.register_builtins()
        p = FaultProviderRegistry.resolve_by_method("kubectl_native")
        msgs = [
            _attempt("scale", "--replicas=0"),
            _result("tc1", "Error: not found"),
        ]
        assert _issue_disproven_in_epoch({"messages": []}, msgs, p) is True
        # host_native: paradox — never vetoed
        hp = FaultProviderRegistry.resolve_by_method("host_native")
        assert _issue_disproven_in_epoch({"messages": []}, msgs, hp) is False


# -- tail projection -----------------------------------------------------------

class TestTailProjection:
    """``_project_fault_handle`` — the execute loop's single handle-sync
    point. Pure projection of the FINAL attribution facts (never a message
    re-scan), write-on-change only."""

    def test_projection_writes_blade_handle_from_facts(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _project_fault_handle,
        )
        from chaos_agent.agent.providers import FaultProviderRegistry

        FaultProviderRegistry.register_builtins()
        state = {"messages": [], "experiment_uid": "uid-x"}
        result = {}
        _project_fault_handle(state, result)
        assert result["fault_handle"] == {
            "kind": "experiment_uid", "value": "uid-x", "method": "",
        }

    def test_projection_writes_native_handle_without_uid(self):
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _project_fault_handle,
        )
        from chaos_agent.agent.providers import FaultProviderRegistry

        FaultProviderRegistry.register_builtins()
        state = {"messages": []}
        result = {"injection_method": "kubectl_native"}
        _project_fault_handle(state, result)
        assert result["fault_handle"] == {
            "kind": "native", "method": "kubectl_native",
        }

    def test_projection_no_change_writes_nothing(self):
        """An untouched turn (handle already matches the derived one) must
        not emit a redundant state update."""
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _project_fault_handle,
        )
        from chaos_agent.agent.providers import FaultProviderRegistry

        FaultProviderRegistry.register_builtins()
        handle = {"kind": "experiment_uid", "value": "uid-x", "method": ""}
        state = {"messages": [], "experiment_uid": "uid-x", "fault_handle": handle}
        result = {}
        _project_fault_handle(state, result)
        assert "fault_handle" not in result

    def test_projection_after_revocation_stays_none(self):
        """A revoked attribution must stay dead: the projection derives from
        the cleared facts (method=None wins over state) instead of re-scanning
        messages, which would resurrect the invalidated commit."""
        from chaos_agent.agent.nodes.execute.execute_loop import (
            _maybe_revoke_issue_time_attribution,
            _project_fault_handle,
        )
        from chaos_agent.agent.providers import FaultProviderRegistry

        FaultProviderRegistry.register_builtins()
        state = {
            "messages": [],
            "injection_method": "kubectl_native",
            "fault_handle": {"kind": "native", "method": "kubectl_native"},
        }
        msgs = [
            _attempt("scale", "--replicas=0"),
            _result("tc1", "Error: not found"),
        ]
        result = {}
        assert _maybe_revoke_issue_time_attribution(
            state, result, msgs, "kubectl_native"
        ) is True
        _project_fault_handle(state, result)
        assert result["fault_handle"] is None
