"""Runtime capability screening for the read-only phases' ToolNode edges.

task-46317228: during verification on a k8s-profile session the model called
``kubectl_read`` and ``host_read`` in ONE batch. ``bind_tools`` is not an
enforcement boundary and the verify / recover_verify ToolNodes had no runtime
screen (only plan / execute / intent did), so the cross-profile read executed.

The screen now lives in graph-edge nodes built by ``make_phase_screener``
(the phase1_screener / tool_screener paradigm): a refused batch is routed
WHOLESALE back to the LLM node with fabricated ToolMessages — a rejection for
every offending call and a "skipped" notice for every legitimate sibling — so
each tool_call gets an answer and nothing disallowed is ever dispatched.

Two properties matter here:
  1. the disallowed call never reaches the ToolNode;
  2. every call in the batch gets a ToolMessage (OpenAI-compatible APIs
     reject the next request when a tool_call has no matching tool result).
"""

import pytest
from langchain_core.messages import AIMessage

from chaos_agent.agent.nodes._phase_screener import make_phase_screener
from chaos_agent.agent.providers import FaultProviderRegistry


def _call(name: str, call_id: str):
    return {"name": name, "args": {}, "id": call_id, "type": "tool_call"}


def _k8s_state(*calls):
    return {
        "fault_spec": {"scope": "node"},
        "messages": [AIMessage(content="", tool_calls=list(calls))],
    }


def _verify_screener():
    node, route = make_phase_screener(capability_phase="verify", readonly=False)
    return node, route


class TestPhaseScreenerCapability:
    @pytest.mark.asyncio
    async def test_mixed_batch_refused_wholesale_with_skipped_sibling(self):
        """Replay the accident's batch: kubectl_read + host_read together.

        The old wrapper dispatched the legitimate call and answered only the
        refused one; the unified screener refuses the WHOLE batch (phase1 /
        tool_screener semantics) and lets the model re-issue a clean batch.
        """
        FaultProviderRegistry.register_builtins()
        node, route = _verify_screener()

        out = await node(_k8s_state(
            _call("kubectl_read", "c1"), _call("host_read", "c2"),
        ))

        assert out["screener_route"] == "retry"
        assert route({**_k8s_state(), **out}) == "retry"
        by_id = {m.tool_call_id: m for m in out["messages"]}
        # The legitimate sibling is not executed — it gets a skipped notice.
        assert "skipped" in by_id["c1"].content
        assert by_id["c2"].content.startswith("Error:")
        # Truthful capability cause: names BOTH the profile the tool belongs
        # to and the environment in force (explain_tool_refusal) — the old
        # generic "capability profile" sentence named neither.
        assert "provided for the host profile" in by_id["c2"].content

    @pytest.mark.asyncio
    async def test_every_tool_call_gets_an_answer(self):
        """Each tool_call id MUST receive a ToolMessage.

        OpenAI-compatible APIs reject the next request when a tool_call has no
        matching tool result, so filtering must ANSWER the refused calls rather
        than drop them.
        """
        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        calls = [
            _call("kubectl_read", "c1"),
            _call("host_read", "c2"),
            _call("submit_verification", "c3"),
        ]
        out = await node(_k8s_state(*calls))

        answered = {m.tool_call_id for m in out["messages"]}
        assert answered == {"c1", "c2", "c3"}

    @pytest.mark.asyncio
    async def test_refusals_are_marked_as_error_messages(self):
        """Routers skip error ToolMessages when looking for a control tool.

        ``router.route_after_verifier_tools`` scans backwards for
        ``submit_verification`` and ``continue``s past error messages. An
        unmarked refusal would be read as a successful ordinary result — so
        EVERY fabricated message (rejection and skipped notice alike) carries
        ``status="error"``.
        """
        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        out = await node(_k8s_state(
            _call("submit_verification", "c1"), _call("host_read", "c2"),
        ))

        assert out["messages"], "a refused batch still answers every call"
        for msg in out["messages"]:
            assert msg.status == "error"

    @pytest.mark.asyncio
    async def test_all_allowed_passes_through_untouched(self):
        FaultProviderRegistry.register_builtins()
        node, route = _verify_screener()

        out = await node(_k8s_state(_call("kubectl_read", "c1")))

        assert out["screener_route"] == "pass"
        assert "messages" not in out, "a clean batch must not be annotated"
        assert route({**_k8s_state(), **out}) == "pass"

    @pytest.mark.asyncio
    async def test_all_disallowed_routes_back_without_stop_hint(self):
        """Verify loops are iteration-bounded, so a plain refusal suffices."""
        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        out = await node(_k8s_state(_call("host_read", "c1")))

        assert out["screener_route"] == "retry"
        assert out["messages"][0].content.startswith("Error:")
        assert "Stop calling tools" not in out["messages"][0].content

    @pytest.mark.asyncio
    async def test_plan_screener_full_refusal_carries_the_stop_hint(self):
        """A systematic refusal must tell the model retrying cannot work.

        ``should_continue_plan_builder`` has no iteration bound, so a model
        that keeps retrying would spin to the recursion limit.
        """
        FaultProviderRegistry.register_builtins()
        node, _ = make_phase_screener(
            capability_phase="plan", stop_retry_hint=True,
        )

        out = await node(_k8s_state(_call("host_read", "c1")))

        assert out["screener_route"] == "retry"
        assert "Stop calling tools" in out["messages"][0].content

    @pytest.mark.asyncio
    async def test_plan_screener_partial_refusal_does_not_say_stop(self):
        """The 'stop' instruction is only for a fully-refused batch.

        With a legitimate sibling in the batch the phase can still progress —
        telling the model to stop would cut it short.
        """
        FaultProviderRegistry.register_builtins()
        node, _ = make_phase_screener(
            capability_phase="plan", stop_retry_hint=True,
        )

        out = await node(_k8s_state(
            _call("kubectl_read", "c1"), _call("host_read", "c2"),
        ))

        for msg in out["messages"]:
            assert "Stop calling tools" not in msg.content

    @pytest.mark.asyncio
    async def test_no_tool_calls_passes_through(self):
        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        out = await node({
            "fault_spec": {"scope": "node"},
            "messages": [AIMessage(content="done")],
        })

        assert out == {"screener_route": "pass"}

    @pytest.mark.asyncio
    async def test_recover_verify_phase_is_screened_too(self):
        FaultProviderRegistry.register_builtins()
        node, _ = make_phase_screener(capability_phase="recover_verify")

        out = await node(_k8s_state(_call("host_read", "c1")))

        assert out["screener_route"] == "retry"
        assert out["messages"][0].content.startswith("Error:")


class TestGraphWiring:
    """Every ToolNode in every graph must be behind a runtime screen.

    ``bind_tools`` is not an enforcement boundary, so a ToolNode reachable
    without a screen executes whatever name the model emits. The protection
    is a dedicated screener node on the only edge into it. This pins which
    ToolNode relies on which screener, so a newly added one has to make that
    choice explicitly instead of defaulting to "unprotected" (how
    ``plan_builder_tools`` was missed).
    """

    # node name -> the screener node that guards the single edge into it.
    SCREENER_GUARDED = {
        "phase1_tools": "phase1_screener",
        "phase2_tools": "tool_screener",
        "clarification_tools": "intent_screener",
        "verifier_tools": "verifier_screener",
        "plan_builder_tools": "plan_builder_screener",
        "recover_verifier_tools": "recover_verifier_screener",
    }

    @staticmethod
    def _graphs():
        from langchain_core.tools import tool

        from chaos_agent.agent.graph import (
            build_intent_graph,
            build_pipeline_graph,
            build_recover_graph,
        )

        @tool
        def _probe(x: str = "") -> str:
            """probe"""
            return x

        tools = [_probe]
        return {
            "pipeline": build_pipeline_graph(tools, tools, tools, tools),
            "recover": build_recover_graph(tools),
            "intent": build_intent_graph(clarification_tools=tools),
        }

    def test_no_tool_node_is_left_unprotected(self):
        from langgraph.prebuilt import ToolNode

        for graph_name, graph in self._graphs().items():
            for node_name, spec in graph.nodes.items():
                if not isinstance(getattr(spec, "runnable", None), ToolNode):
                    continue
                assert node_name in self.SCREENER_GUARDED, (
                    f"{graph_name}.{node_name} is a bare ToolNode with no "
                    f"capability screen. Register the screener node that "
                    f"guards it here."
                )
                guard = self.SCREENER_GUARDED[node_name]
                assert guard in graph.nodes, (
                    f"{graph_name}.{node_name} claims to be guarded by "
                    f"{guard!r}, which is not in this graph"
                )

    def test_the_migrated_screener_nodes_exist(self):
        """Counterpart: the migrated phases must not silently lose their screen."""
        graphs = self._graphs()
        assert "verifier_screener" in graphs["pipeline"].nodes
        assert "plan_builder_screener" in graphs["pipeline"].nodes
        assert "recover_verifier_screener" in graphs["recover"].nodes


class TestGateErrorsCannotAbortTheNode:
    """A screen on the critical path must not introduce a crash.

    Before this screen existed, verify / recover_verify / plan_builder had no
    gate at all, so nothing there could fail. If the gate raises, aborting the
    node would kill the run WHILE THE FAULT IS STILL INJECTED — so it refuses
    instead and lets the phase converge.
    """

    @pytest.mark.asyncio
    async def test_gate_exception_refuses_instead_of_propagating(self, caplog):
        from unittest.mock import patch

        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        with patch(
            "chaos_agent.agent.capabilities.context"
            ".is_tool_name_allowed_for_context",
            side_effect=RuntimeError("provider registry blew up"),
        ):
            out = await node(_k8s_state(_call("kubectl_read", "c1")))

        assert out["screener_route"] == "retry", "must fail closed, not pass"
        assert out["messages"][0].tool_call_id == "c1", "the call still needs an answer"
        assert out["messages"][0].status == "error"
        # The real cause must remain visible, not be swallowed.
        assert "provider registry blew up" in caplog.text


class TestEmptyToolSetIsNotTreatedAsAGateRefusal:
    """"No tools were offered" and "the gate removed them all" differ.

    Every ReAct node binds an EMPTY tool set when the gate empties a non-empty
    one (fail closed — an unbound LLM still emits calls). But when there were no
    static tools to begin with, the unbound LLM is the intended prose path AND
    the only usable one: a provider rejects a request carrying ``tools: []``.
    Collapsing the two would turn a working no-tool turn into a provider error.
    """

    SITES = {
        "src/chaos_agent/agent/nodes/execute/agent_loop.py": 1,
        "src/chaos_agent/agent/nodes/execute/execute_loop.py": 1,
        "src/chaos_agent/agent/nodes/verify/verifier.py": 1,
        "src/chaos_agent/agent/nodes/recover/_recover_verifier_loop.py": 2,
    }

    def test_every_node_distinguishes_the_two(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        for rel, expected in self.SITES.items():
            src = (root / rel).read_text()
            found = src.count("if tools and not visible_tools:")
            assert found == expected, (
                f"{rel}: expected {expected} 'tools and not visible_tools' "
                f"guard(s), found {found} — a bare 'not visible_tools' would "
                f"send an empty tools array when no tools were offered at all"
            )
        # The recover Layer 2 site expresses the same rule inline.
        l2 = (root / "src/chaos_agent/agent/nodes/recover/_recover_verifier_loop.py").read_text()
        assert "llm.bind_tools([]) if tools else llm" in l2

    @pytest.mark.asyncio
    async def test_agent_loop_with_no_tools_leaves_the_llm_unbound(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from langchain_core.messages import AIMessage as _AIMessage

        import chaos_agent.agent.nodes.execute.agent_loop as loop_mod
        from chaos_agent.agent.nodes.execute.agent_loop import make_agent_loop

        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_AIMessage(content="nothing to call"))
        node = make_agent_loop(llm=llm, tools=[])
        monkeypatch.setattr(loop_mod, "compute_env_info", AsyncMock(return_value=""))
        monkeypatch.setattr(loop_mod, "sync_to_store", AsyncMock())

        await node({
            "task_id": "task-no-tools", "operation": "inject",
            "agent_loop_count": 0, "messages": [],
            "fault_spec": {"scope": "node", "fault_target": "cpu",
                           "fault_action": "fullload", "names": ["node-1"]},
            "kube_connection_mode": "kubeconfig",
        })

        llm.bind_tools.assert_not_called()
        llm.ainvoke.assert_awaited()


class TestAccidentReplay:
    """Replay task-46317228's REAL call shape through the layers.

    Extracted from the session record: all EIGHT ``host_read`` calls carried
    ``node='cn-shanghai-cloudspe.25.209.68.1'`` on a ``kubewiz_k8s`` session, and
    all eight executed — on the KubeWiz platform executor. Two independent layers
    must now stop each one, so neither is a single point of failure.
    """

    REAL_ARGS = [
        {"node": "cn-shanghai-cloudspe.25.209.68.1", "command": "uptime",
         "task_id": "task-46317228-1f4f-479e-8496-a03489769a2f"},
        {"node": "cn-shanghai-cloudspe.25.209.68.1",
         "command": "ps aux | grep stress-ng | grep -v grep | head -5",
         "task_id": "task-46317228-1f4f-479e-8496-a03489769a2f"},
    ]

    @staticmethod
    def _accident_state(args, call_id):
        return {
            "fault_spec": {
                "scope": "node", "fault_target": "cpu",
                "fault_action": "fullload",
                "names": ["cn-shanghai-cloudspe.25.209.68.1"],
            },
            "kube_connection_mode": "kubewiz_k8s",
            "kubewiz_cluster_uuid": "c62735cce1d61445995c0f1d9e4a1bded",
            "kubewiz_profile": "526255",
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "host_read", "args": args, "id": call_id,
                "type": "tool_call",
            }])],
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REAL_ARGS)
    async def test_screen_stops_the_real_call(self, args):
        FaultProviderRegistry.register_builtins()
        node, _ = _verify_screener()

        out = await node(self._accident_state(args, "c1"))

        assert out["screener_route"] == "retry", "the accident's call passed the screen"
        assert out["messages"][0].content.startswith("Error:")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REAL_ARGS)
    async def test_tool_layer_stops_it_even_if_the_screen_is_bypassed(
        self, args, monkeypatch
    ):
        """Defence in depth: a restored checkpoint can reach the tool directly."""
        from chaos_agent.config.settings import settings
        from chaos_agent.tools.host_cmd import host_read

        monkeypatch.setattr(settings, "kube_connection_mode", "kubewiz_k8s")
        monkeypatch.setattr(
            settings, "kubewiz_cluster_uuid", "c62735cce1d61445995c0f1d9e4a1bded"
        )
        monkeypatch.setattr(settings, "kubewiz_profile", "526255")

        async def _must_not_run(*a, **k):
            raise AssertionError("a command was dispatched")

        monkeypatch.setattr(
            "chaos_agent.tools.host_cmd.execute_via_transport", _must_not_run
        )
        with pytest.raises(Exception):
            await host_read.ainvoke(args)
