"""Golden scenario runner — roadmap T1.14 / A9, Phase 0 (mock-LLM contract).

Mirrors ``scripts/guard_diff_harness.py``'s SURFACES registry pattern: each
surface adapter builds a minimal AgentState from the YAML ``input``, invokes
the PRODUCTION entry node, and returns a flat ``actual`` dict checked by
``check_expectations`` (subset semantics — only listed fields are asserted).

Surfaces (input shape documented per adapter):
  intent  — ``make_intent_clarification(llm=ScriptedLLM(...))`` driven
            through a ReAct loop: each user turn (``input.user_turns``, a
            list — or the legacy single ``input.user_message``) is followed
            by node↔mock-ToolNode iterations until the turn ends in a
            plain-text reply or the fast path converges the intent.
            Scripted LLM turns live in ``input.llm_script`` (each step is
            ``text`` or ``tool_calls``); mock tool replies come from
            ``input.tool_responses`` (per-tool FIFO queues) —
            ``submit_fault_intent`` defaults to ``input.tool_ack``.
  safety  — ``gates.safety_check(state)`` with a FaultSpec built from
            ``input.fault_spec`` (FaultSpec field names: scope /
            fault_target / fault_action / namespace / names / params).
            Optional ``input.settings_patch`` overrides runtime settings.
  recover — ``recover.recover_handler(state)`` with a mocked task store fed
            by ``input.store_rows`` (query_active). ``store.get(task_id)``
            defaults to looking the id up in store_rows (real-store
            semantics); ``input.store_get`` overrides it wholesale.

Expectation keys (all optional; subset match unless noted):
  operation, confirmed_intent, safety_status, result_status,
  needs_task_selection, dialogue_round, clarification_round,
  fault_spec (recursive subset on the produced spec dict),
  fault_spec_absent (bool), safety_reason_contains (casefolded substring),
  message_contains (casefolded substring over emitted messages),
  absent (result keys that must NOT be present), safety_status_in (one-of),
  llm_calls (exact LLM invocation count), tool_trace_contains
  (substring-ordered mock-tool execution trace), llm_calls_max /
  clarification_round_max / dialogue_round_max (budget ceilings for
  non-deterministic runs)

Modes:
  mock — ScriptedLLM replays input.llm_script; expectations may pin exact
         counts (expected_mock). Deterministic, part of the PR gate.
  real — the PRODUCTION LLM (factory.make_llm, settings/config.json)
         drives the same ReAct loop against the same mocked environment
         (input.tool_responses; unscripted tools answer with
         input.default_tool_response). Expectations should be budget-
         style (expected_real). Per-scenario wall-clock cap:
         input.eval_timeout_s (default 300). Used by
         scripts/llm_eval_harness.py for nightly win-rate matrices.

Phase 0 scope: mock LLM — this locks the YAML schema, the runner, the
assertion semantics and the CI lane. Phase 1 (real-model nightly runs,
win-rate matrix, roadmap 9.1.4 / T1.14) reuses the same YAML corpus
unchanged via ``mode="real"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import yaml
from langchain_core.callbacks import BaseCallbackHandler

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

SURFACES: dict[str, Callable[..., Awaitable[dict]]] = {}


# ── shared helpers ────────────────────────────────────────────────────────


def _base_state() -> dict:
    """Minimal AgentState mirroring tests/conftest.py sample_agent_state."""
    return {
        "task_id": f"task-golden-{uuid4().hex[:12]}",
        "operation": "",
        "skill_name": None,
        "fault_spec": None,
        "safety_status": "pending",
        "safety_reason": None,
        "needs_confirmation": False,
        "plan": None,
        "experiment_uid": None,
        "result": None,
        "error": None,
        "agent_loop_count": 0,
        "execute_loop_count": 0,
        "messages": [],
        "confirmed_intent": None,
        "interaction_mode": "cli",
        "clarification_round": 0,
        "dialogue_round": 0,
        "needs_task_selection": False,
    }


class ScriptedLLM:
    """Minimal LangChain-compatible double for make_intent_clarification.

    ``bind_tools`` returns self (advertisement is ignored by the script);
    ``ainvoke`` pops the next scripted AIMessage and counts invocations
    so scenarios can assert the exact ``llm_calls`` budget.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools: list) -> "ScriptedLLM":  # noqa: ANN101
        return self

    async def ainvoke(self, messages: list, config: dict | None = None, **_: Any):  # noqa: ANN101
        self.calls += 1
        if not self._responses:
            raise AssertionError(
                "ScriptedLLM exhausted — scenario llm_script too short "
                f"(call #{self.calls}, {len(messages or [])} messages in context)"
            )
        return self._responses.pop(0)


class _CallCounter(BaseCallbackHandler):
    """LangChain callback counting chat-model invocations for real runs.

    ScriptedLLM counts its own ``.calls``; the production LLM is counted
    through the standard callback hook instead (subclassing the LLM would
    be fragile — ``bind_tools`` copies the instance and resets any counter
    attribute, while ``callbacks`` survive the copy).
    """

    calls: int = 0

    def on_chat_model_start(self, serialized: dict, messages: list, **_: Any) -> None:
        self.calls += 1


def _build_real_tools() -> list:
    """Mirror factory.py's clarification_tools build (static six + PLAN).

    The production LLM must see the same tool advertisement the real
    graph binds, otherwise it never emits the kubectl/submit tool calls
    the scenarios assert on. Skill tools need a SkillRegistry; provider
    PLAN tools bring the read-only probes (kubectl etc.).
    """
    from chaos_agent.agent.factory import _append_provider_tools, _build_skill_tools
    from chaos_agent.agent.nodes.planning.intent_clarification import (
        query_active_experiments,
        recover_task,
        submit_batch_intent,
        submit_fault_intent,
    )
    from chaos_agent.skills.registry import SkillRegistry

    by_name = {t.name: t for t in _build_skill_tools(SkillRegistry())}
    tools = [
        by_name["activate_skill"],
        by_name["read_skill_resource"],
        submit_fault_intent,
        submit_batch_intent,
        query_active_experiments,
        recover_task,
    ]
    return _append_provider_tools(tools, "PLAN")


def _messages_text(result: dict) -> str:
    parts = []
    for msg in result.get("messages") or []:
        content = getattr(msg, "content", msg)
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


# ── surface: intent ───────────────────────────────────────────────────────


async def _run_intent(inp: dict, mode: str = "mock") -> dict:
    """Drive the intent node through a ReAct loop.

    The production node is single-step by design (LangGraph routes tool
    calls to a real ToolNode and re-enters). This adapter supplies that
    harness half: it appends the scripted HumanMessage turn, invokes the
    node, executes every emitted tool_call against ``input.tool_responses``
    queues, appends the ToolMessage and re-enters — until the node replies
    in plain text or the fast path converges ``confirmed_intent``.

    ``mode="real"`` swaps ScriptedLLM for the production LLM
    (factory.make_llm + the factory's clarification tool advertisement);
    the environment stays mocked so runs are reproducible and need no
    cluster. Unscripted tools answer ``input.default_tool_response``.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from chaos_agent.agent.nodes.planning.intent_clarification import (
        make_intent_clarification,
    )

    tool_queues: dict[str, list[str]] = {
        name: list(resps) for name, resps in (inp.get("tool_responses") or {}).items()
    }
    default_ack = inp.get("tool_ack", "Submitted fault intent.")
    default_tool_response = inp.get(
        "default_tool_response",
        "(golden mock env) tool executed; no real side effects.",
    )

    if mode == "real":
        from chaos_agent.agent.factory import make_llm

        counter = _CallCounter()
        llm = make_llm(callbacks=[counter])
        tools = _build_real_tools()
    else:
        script = []
        for step in inp.get("llm_script", []):
            if step.get("tool_calls"):
                script.append(
                    AIMessage(
                        content=step.get("text", ""),
                        tool_calls=[
                            {
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                                "id": f"golden_{tc['name']}_{i}",
                                "type": "tool_call",
                            }
                            for i, tc in enumerate(step["tool_calls"])
                        ],
                    )
                )
            else:
                script.append(AIMessage(content=step["text"]))
        llm = ScriptedLLM(script)
        tools = None

    node = make_intent_clarification(llm=llm, tools=tools)

    state = _base_state()
    msgs: list = []
    tool_trace: list[str] = []
    turns = inp.get("user_turns") or (
        [inp["user_message"]] if inp.get("user_message") else []
    )

    for turn_text in turns:
        msgs.append(HumanMessage(content=turn_text))
        for _ in range(inp.get("max_iterations", 8)):
            state["messages"] = msgs
            result = await node(state)
            if not result:
                break
            new_msgs = result.get("messages") or []
            msgs.extend(new_msgs)
            for key, value in result.items():
                if key != "messages":
                    state[key] = value
            if state.get("confirmed_intent"):
                break
            calls = [
                tc for m in new_msgs for tc in (getattr(m, "tool_calls", None) or [])
            ]
            if not calls:
                break  # plain-text reply ends the turn
            for tc in calls:
                name = tc["name"]
                tool_trace.append(name)
                if name == "submit_fault_intent":
                    content = default_ack
                elif tool_queues.get(name):
                    content = tool_queues[name].pop(0)
                elif mode == "real":
                    # Real models may probe tools the scenario did not
                    # script (activate_skill, read_skill_resource…):
                    # answer neutrally instead of failing the scenario.
                    content = default_tool_response
                else:
                    raise AssertionError(
                        f"golden script has no tool_responses entry for {name!r}"
                    )
                msgs.append(
                    ToolMessage(
                        content=content,
                        name=name,
                        tool_call_id=tc["id"],
                    )
                )
        if state.get("confirmed_intent"):
            break

    return {
        "result": {k: v for k, v in state.items() if k != "messages"},
        "operation": state.get("operation"),
        "confirmed_intent": state.get("confirmed_intent"),
        "fault_spec": state.get("fault_spec"),
        "dialogue_round": state.get("dialogue_round"),
        "clarification_round": state.get("clarification_round"),
        "needs_task_selection": state.get("needs_task_selection"),
        "llm_calls": counter.calls if mode == "real" else llm.calls,
        "tool_trace": tool_trace,
        "messages_text": _messages_text({"messages": msgs}),
    }


# ── surface: safety ───────────────────────────────────────────────────────


async def _run_safety(inp: dict) -> dict:
    """Run gates.safety_check over a spec built from input.fault_spec.

    ``input.settings_patch`` is applied by direct setattr on the settings
    proxy (whose ``__setattr__`` forwards to the active Settings). The
    unittest.mock ``patch.object`` restore path delattrs the PROXY instance
    — which never owns the attribute — so a hand-rolled save/restore is
    the only safe mechanism here.
    """
    from chaos_agent.agent.nodes.gates.safety_check import safety_check
    from chaos_agent.config.settings import settings
    from tests._helpers import replace_fault_spec

    state = _base_state()
    state["skill_name"] = inp.get("skill_name", "pod-cpu-fullload")
    replace_fault_spec(state, **inp["fault_spec"])

    saved_settings = {
        key: getattr(settings, key) for key in (inp.get("settings_patch") or {})
    }
    for key, value in (inp.get("settings_patch") or {}).items():
        if isinstance(value, list):
            value = tuple(value)
        setattr(settings, key, value)
    try:
        result = await safety_check(state)
    finally:
        for key, value in saved_settings.items():
            setattr(settings, key, value)

    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    return {
        "result": result,
        "operation": result.get("operation"),
        "safety_status": result.get("safety_status"),
        "safety_reason": result.get("safety_reason") or "",
        "result_status": inner.get("status"),
        "messages_text": _messages_text(result),
    }


# ── surface: recover ──────────────────────────────────────────────────────


async def _run_recover(inp: dict) -> dict:
    from langchain_core.messages import HumanMessage

    from chaos_agent.agent.nodes.recover.recover_handler import recover_handler

    state = _base_state()
    state["confirmed_intent"] = "recover"
    if inp.get("user_message"):
        state["messages"] = [HumanMessage(content=inp["user_message"])]

    rows = inp.get("store_rows", [])

    def _lookup(tid: str):
        # Real stores return the task record for its id; an unmocked
        # AsyncMock.get would hand back a bare AsyncMock whose chained
        # .get() calls explode inside format_experiment_line.
        if "store_get" in inp:
            return inp["store_get"]
        for row in rows:
            if isinstance(row, dict) and row.get("task_id") == tid:
                return row
        return None

    mock_store = AsyncMock()
    mock_store.query_active = AsyncMock(return_value=rows)
    mock_store.get = AsyncMock(side_effect=_lookup)

    with patch(
        "chaos_agent.agent.nodes.recover.recover_handler.get_task_store",
        return_value=mock_store,
    ):
        result = await recover_handler(state)

    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    return {
        "result": result,
        "operation": result.get("operation"),
        "result_status": inner.get("status"),
        "recover_task_id": result.get("recover_task_id"),
        "needs_task_selection": result.get("needs_task_selection"),
        "messages_text": _messages_text(result),
    }


SURFACES["intent"] = _run_intent
SURFACES["safety"] = _run_safety
SURFACES["recover"] = _run_recover


# ── assertion engine ──────────────────────────────────────────────────────


def _subset_matches(expected: Any, actual: Any) -> bool:
    """Recursive subset equality: only keys listed in expected are checked."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            k in actual and _subset_matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            return False
        exp_list = [list(e) if isinstance(e, (list, tuple)) else e for e in expected]
        act_list = [list(a) if isinstance(a, (list, tuple)) else a for a in actual]
        return exp_list == act_list
    return expected == actual


def _flat_diff(expected: dict, actual: Any) -> list[str]:
    diffs: list[str] = []
    for key, want in expected.items():
        got = actual.get(key) if isinstance(actual, dict) else None
        if not _subset_matches(want, got):
            diffs.append(f"  {key}: expected {want!r}, got {got!r}")
    return diffs


def check_expectations(expected: dict, actual: dict) -> list[str]:
    """Return human-readable failure strings; empty list means pass."""
    failures: list[str] = []
    result = actual.get("result", {})

    for simple_key in (
        "operation",
        "confirmed_intent",
        "safety_status",
        "result_status",
        "needs_task_selection",
        "dialogue_round",
        "clarification_round",
        "recover_task_id",
    ):
        if simple_key in expected:
            want, got = expected[simple_key], actual.get(simple_key)
            if want != got:
                failures.append(f"  {simple_key}: expected {want!r}, got {got!r}")

    if "safety_status_in" in expected:
        if actual.get("safety_status") not in expected["safety_status_in"]:
            failures.append(
                f"  safety_status: expected one of {expected['safety_status_in']!r}, "
                f"got {actual.get('safety_status')!r}"
            )

    if "llm_calls" in expected:
        want, got = expected["llm_calls"], actual.get("llm_calls")
        if want != got:
            failures.append(f"  llm_calls: expected {want!r}, got {got!r}")

    if "llm_calls_max" in expected:
        got = actual.get("llm_calls")
        if got is None or got > expected["llm_calls_max"]:
            failures.append(
                f"  llm_calls: expected <= {expected['llm_calls_max']!r}, got {got!r}"
            )

    if "clarification_round_max" in expected:
        got = actual.get("clarification_round")
        if got is None or got > expected["clarification_round_max"]:
            failures.append(
                f"  clarification_round: expected <= "
                f"{expected['clarification_round_max']!r}, got {got!r}"
            )

    if "dialogue_round_max" in expected:
        got = actual.get("dialogue_round")
        if got is None or got > expected["dialogue_round_max"]:
            failures.append(
                f"  dialogue_round: expected <= "
                f"{expected['dialogue_round_max']!r}, got {got!r}"
            )

    trace = actual.get("tool_trace") or []
    for name in expected.get("tool_trace_contains", []):
        if name not in trace:
            failures.append(f"  tool_trace: {name!r} not executed (trace: {trace!r})")

    if "fault_spec" in expected:
        failures.extend(
            _flat_diff(expected["fault_spec"], actual.get("fault_spec") or {})
        )

    if expected.get("fault_spec_absent"):
        if result.get("fault_spec") is not None:
            failures.append("  fault_spec: expected absent, but the node produced one")

    reason = (actual.get("safety_reason") or "").casefold()
    for needle in expected.get("safety_reason_contains", []):
        if needle.casefold() not in reason:
            failures.append(f"  safety_reason: missing {needle!r} in {reason!r}")

    text = (actual.get("messages_text") or "").casefold()
    for needle in expected.get("message_contains", []):
        if needle.casefold() not in text:
            failures.append(f"  messages: missing {needle!r}")

    for key in expected.get("absent", []):
        if result.get(key) is not None:
            failures.append(
                f"  {key}: expected NOT in result, but present as {result.get(key)!r}"
            )

    return failures


# ── scenario loading ──────────────────────────────────────────────────────


def load_scenarios() -> list[dict]:
    """Load and validate every YAML in tests/golden/scenarios/."""
    required = ("name", "surface", "input", "expected")
    out: list[dict] = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"{path.name}: missing required keys {missing}")
        if data["surface"] not in SURFACES:
            raise ValueError(f"{path.name}: unknown surface {data['surface']!r}")
        out.append(data)
    if not out:
        raise ValueError("no golden scenarios found — corpus is empty")
    return out


async def run_scenario(
    scenario: dict, mode: str = "mock", *, return_actual: bool = False,
) -> list[str] | tuple[list[str], dict]:
    """Execute one scenario through its surface; return failure strings.

    Mode-specific expectations overlay the shared ``expected`` block:
    ``{**expected, **expected_mock}`` or ``{**expected, **expected_real}`` —
    so a scenario can pin exact counts for mock runs while keeping budget
    ceilings for non-deterministic real-model runs.

    ``return_actual=True`` (used by the eval harness) additionally returns
    the raw actual dict — tool_trace + messages tail go into the report so
    failures can be adjudicated WITHOUT re-running the scenario.
    """
    surface = scenario["surface"]
    mode_expected = scenario.get(f"expected_{mode}") or {}
    merged = {**scenario["expected"], **mode_expected}

    import asyncio

    timeout = scenario.get("input", {}).get("eval_timeout_s", 300)
    try:
        if surface == "intent":
            actual = await asyncio.wait_for(
                _run_intent(scenario["input"], mode=mode), timeout=timeout,
            )
        else:
            actual = await asyncio.wait_for(
                SURFACES[surface](scenario["input"]), timeout=timeout,
            )
    except TimeoutError:
        failures = [f"  scenario timed out after {timeout}s (mode={mode})"]
        if return_actual:
            return failures, {}
        return failures
    if return_actual:
        return check_expectations(merged, actual), actual
    return check_expectations(merged, actual)
