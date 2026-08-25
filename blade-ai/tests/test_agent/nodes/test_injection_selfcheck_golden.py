"""Phase-8 golden fixation: byte-exact snapshots of the native carriers'
step self-check outputs plus the D4 narrowing pins.

The two byte-exact tests are the mechanical proof that the T3.5 rewrite
(``scan_step_actions`` hook dispatch, provider-owned vocabulary) kept the
native outputs IDENTICAL. The D4 tests pin the explicitly-decided behaviour
change: experiment methods (``host_blade`` / ``kubectl_exec``) no longer
fall into a kubectl-verbs branch — they return ``None`` (experiment
completion is judged by the UID evidence chain). Their pre-rewrite
ancestors recorded the former behaviour on purpose, so the narrowing diff
is documented, not silent.

Also pins the ``_was_kubectl_injection_attempted`` wrapper's dispatch
vocabulary (one mutating / one read-only representative; the 13-case
suite in ``test_verifier.py`` carries the full behavioural coverage)."""

from langchain_core.messages import AIMessage, ToolMessage

from chaos_agent.agent.nodes.execute._injection_detection import (
    _was_kubectl_injection_attempted,
    build_injection_step_selfcheck,
)

K8S_CASE = """**演练步骤**：
1. 使用 kubectl 将该节点标记为不可调度：`kubectl cordon <node>`
2. 给该节点添加污点：`kubectl taint nodes <node> key=val:NoSchedule`
3. 删除该节点上的 Pod，触发重建

**注入验证**：
1. 执行 `kubectl get nodes`
"""

HOST_CASE = """**演练步骤**：
1. 记录当前连接基线：`ss -s`
2. 使用 iptables 丢弃目标端口入向流量
3. 武装定时恢复

**注入验证**：
1. 确认连接超时
"""

_MESSAGES_K8S = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "kubectl",
                "args": {"subcommand": "cordon", "v_args": "node-1"},
                "id": "tc-1",
            }
        ],
    ),
    ToolMessage(content="node/node-1 cordoned", name="kubectl", tool_call_id="tc-1"),
]

_TAIL = (
    "\nReconsider (this check is heuristic and may be inaccurate): if you "
    "have ALREADY performed the actions needed for the fault effect — a tool "
    "may have timed out but still applied — STOP calling tools and let "
    "verification confirm it. Do NOT repeat actions already done, and do NOT "
    "loop deleting/observing to watch the effect (observation is the "
    "verification phase's job). If an action was genuinely SKIPPED, do it now."
)

GOLDEN_K8S_NATIVE = (
    "[Step self-check] This multi-step skill case has an injection action "
    "that may not have been performed yet. Steps outlined:\n"
    "1. 使用 kubectl 将该节点标记为不可调度：`kubectl cordon <node>`\n"
    "2. 给该节点添加污点：`kubectl taint nodes <node> key=val:NoSchedule`\n"
    "3. 删除该节点上的 Pod，触发重建\n"
    "\nPossibly not yet performed: taint (给该节点添加污点：`kubectl taint "
    "nodes <node> key=val:NoSchedule`), delete (删除该节点上的 Pod，触发重建)\n"
    + _TAIL
)

GOLDEN_HOST_NATIVE = (
    "[Step self-check] This multi-step skill case has an injection action "
    "that may not have been performed yet. Steps outlined:\n"
    "1. 记录当前连接基线：`ss -s`\n"
    "2. 使用 iptables 丢弃目标端口入向流量\n"
    "3. 武装定时恢复\n"
    "\nPossibly not yet performed: iptables (使用 iptables 丢弃目标端口入向流量)\n"
    + _TAIL
)


def test_kubectl_native_selfcheck_output_is_byte_exact():
    """Golden: cordon executed, taint/delete missing → soft reminder listing
    exactly the missing verbs, byte-identical through the T3.5 rewrite."""
    out = build_injection_step_selfcheck(K8S_CASE, _MESSAGES_K8S, "kubectl_native")
    assert out == GOLDEN_K8S_NATIVE


def test_host_native_selfcheck_output_is_byte_exact():
    """Golden: baseline step filtered by intent-prefix, iptables missing →
    soft reminder, byte-identical through the T3.5 rewrite."""
    out = build_injection_step_selfcheck(HOST_CASE, [], "host_native")
    assert out == GOLDEN_HOST_NATIVE


def test_d4_experiment_methods_do_not_claim_step_selfcheck():
    """D4 POST-REWRITE PIN (phase-8 narrowing, explicitly decided):
    ``kubectl_exec`` (an experiment method) resolves to the ChaosBlade
    backend, which does not claim the ``scan_step_actions`` hook → the
    self-check returns ``None``. Experiment completion is judged by the
    UID evidence chain, not step-verb heuristics; the pre-rewrite behaviour
    (falling into the kubectl-verbs branch) was a historical approximation,
    pre-pinned by this test's ancestor before the T3.5 rewrite."""
    out = build_injection_step_selfcheck(K8S_CASE, _MESSAGES_K8S, "kubectl_exec")
    assert out is None


def test_d4_host_blade_does_not_claim_step_selfcheck():
    """D4 POST-REWRITE PIN: ``host_blade`` does not claim the hook either —
    ``None`` for both a kubectl-verb case (formerly the shared kubectl
    branch) and a host-binary case (formerly ``None`` only incidentally,
    because the kubectl branch found no verbs in it)."""
    out = build_injection_step_selfcheck(K8S_CASE, _MESSAGES_K8S, "host_blade")
    assert out is None
    assert build_injection_step_selfcheck(HOST_CASE, [], "host_blade") is None


def test_unresolved_method_does_not_claim_step_selfcheck():
    """Pinned side-effect of the same narrowing: ``injection_method=None``
    (no attribution) also returns ``None`` — the pre-rewrite code fell it
    into the kubectl-verbs ``else`` branch. Unreachable on the main path:
    the execute loop gates the self-check behind
    ``resolve_by_method(method) is not None and provider.is_multi_step``,
    so a method that resolves to no provider never reaches this function
    in production; this pin holds the function-level contract for direct
    callers (and mirrors the D4 experiment-method pins above)."""
    assert build_injection_step_selfcheck(K8S_CASE, _MESSAGES_K8S, None) is None


def test_backscan_dispatch_vocabulary_pinned():
    """Golden: the attempted-injection back-scan credits a mutating
    ``kubectl exec`` fallback (after a failed blade_create) and does NOT
    credit a read-only exec — the wrapper's provider vocabulary stays
    equivalent through the T3.3 move."""
    failed_blade_then = lambda inner_cmd, tc_id: [  # noqa: E731
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "blade_create",
                    "args": {"command": "create cpu fullload"},
                    "id": "tc-b",
                }
            ],
        ),
        ToolMessage(
            content='{"code": 500, "success": false}', name="blade_create",
            tool_call_id="tc-b",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "kubectl",
                    "args": {"subcommand": "exec", "v_args": f"pod-1 -- {inner_cmd}"},
                    "id": tc_id,
                }
            ],
        ),
        ToolMessage(content="ok", name="kubectl", tool_call_id=tc_id),
    ]

    assert (
        _was_kubectl_injection_attempted(
            failed_blade_then("python -c stress", "tc-e")
        )
        is True
    )
    assert (
        _was_kubectl_injection_attempted(
            failed_blade_then("cat /etc/hosts", "tc-e2")
        )
        is False
    )
