"""Tests for transport-safe, capability-global Intent discovery."""

from langchain_core.messages import AIMessage

from chaos_agent.agent.nodes.planning.intent_screener import intent_screener


def _tool_call(name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": name,
        "id": "probe-1",
        "args": {"command": "df -h"},
    }])


def test_rejects_host_probe_on_k8s_transport_without_rejecting_host_semantics():
    result = intent_screener({
        "kube_connection_mode": "kubeconfig",
        "fault_spec": {"scope": "host", "blade_target": "cpu", "blade_action": "fullload"},
        "messages": [_tool_call("host_read")],
    })

    assert result["intent_screener_route"] == "retry"
    assert result["messages"][0].name == "host_read"


def test_refusal_names_the_transport_in_force():
    # The refusal used to be one fixed sentence ("unavailable for the current
    # environment") for every tool in every profile — it never said WHICH
    # environment was connected, leaving the model to retry variations. The
    # capability gate already holds the resolved profile (the same rule the
    # execute-phase screener follows), so the message must name it.
    result = intent_screener({
        "kube_connection_mode": "kubeconfig",
        "fault_spec": {"scope": "host", "blade_target": "cpu", "blade_action": "fullload"},
        "messages": [_tool_call("host_read")],
    })

    content = result["messages"][0].content
    assert content.startswith("Error:")
    # Names the tool, the profile it belongs to, and the transport in force.
    assert "host_read" in content
    assert "k8s" in content, f"transport in force not named: {content}"
    # Keeps the standing instruction so the cause is paired with a move.
    assert "Select a tool bound to the active transport." in content
    # Not the old profile-agnostic template.
    assert "unavailable for the current environment" not in content


def test_allows_k8s_probe_even_when_semantic_intent_is_host():
    result = intent_screener({
        "kube_connection_mode": "kubeconfig",
        "fault_spec": {"scope": "host", "blade_target": "cpu", "blade_action": "fullload"},
        "messages": [_tool_call("kubectl_read")],
    })

    assert result["intent_screener_route"] == "pass"


def test_plan_builder_rejects_stale_host_tool_on_k8s_transport():
    """Same contract, now enforced by the plan_builder_screener edge node.

    A ``plan_builder_screener`` node used to live in ``intent_screener`` with
    this exact rule, but was never wired into ``build_pipeline_graph`` — so this
    test passed while ``plan_builder_tools`` actually ran unscreened. That gap
    is closed by ``make_phase_screener(capability_phase="plan", ...)`` wired
    into the pipeline graph. The screener refuses the WHOLE batch
    (phase1/tool_screener protocol): the offending call gets a rejection and
    the legitimate sibling a skipped notice — nothing is dispatched.
    """
    import asyncio

    from chaos_agent.agent.nodes._phase_screener import make_phase_screener
    from chaos_agent.agent.providers import FaultProviderRegistry

    FaultProviderRegistry.register_builtins()
    screener, route = make_phase_screener(
        capability_phase="plan", stop_retry_hint=True,
    )
    state = {
        "kube_connection_mode": "kubeconfig",
        "fault_spec": {"scope": "pod", "blade_target": "cpu", "blade_action": "fullload"},
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "host_read", "id": "probe-1", "args": {"command": "df -h"}},
            {"name": "kubectl_read", "id": "probe-2",
             "args": {"subcommand": "get", "v_args": "pods"}},
        ])],
    }

    out = asyncio.run(screener(state))

    assert out["screener_route"] == "retry"
    assert route({**state, **out}) == "retry"
    by_id = {m.tool_call_id: m for m in out["messages"]}
    assert by_id["probe-1"].name == "host_read"
    assert by_id["probe-1"].content.startswith("Error:")
    assert by_id["probe-1"].status == "error"
    # The legitimate sibling does NOT run — it gets the skipped notice and the
    # whole batch goes back to the model to be re-issued cleanly.
    assert "skipped" in by_id["probe-2"].content
    assert by_id["probe-2"].status == "error"
