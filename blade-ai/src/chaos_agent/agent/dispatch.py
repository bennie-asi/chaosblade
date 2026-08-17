"""Structured domain event dispatching for blade-ai graph nodes.

Uses ``langchain_core.callbacks.adispatch_custom_event`` to emit events
that flow through ``astream_events(v2)`` as ``on_custom_event`` type.
These replace the fragile ``on_chain_start/on_chain_end`` parsing that
depends on non-public LangGraph internal event format (``langsmith:nodes:``
tags and ``metadata["langgraph_node"]``).

Event names:
- ``phase_started``   — node entry, carries ``{node, phase}``
- ``phase_completed`` — node exit,  carries ``{node, phase}``

Consumed by ``parse_stream_event()`` in ``streaming.py`` which maps them
to ``StreamEvent(type="node_start"|"node_end")``.  Downstream EventBridge
and PhaseTimelineRenderer are unchanged — they already handle these types.
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from langchain_core.callbacks import adispatch_custom_event
from langgraph.errors import GraphInterrupt

from chaos_agent.agent.state import AgentState

logger = logging.getLogger(__name__)


async def dispatch_phase_started(node: str, phase: str) -> None:
    """Emit a phase_started domain event (node entry)."""
    await adispatch_custom_event("phase_started", {"node": node, "phase": phase})


async def dispatch_phase_completed(node: str, phase: str) -> None:
    """Emit a phase_completed domain event (node exit)."""
    await adispatch_custom_event("phase_completed", {"node": node, "phase": phase})


async def dispatch_node_message(node: str, content: str) -> None:
    """Emit a node_message event for programmatic text not produced by an LLM call.

    parse_stream_event converts this to a token StreamEvent so the TUI displays it.

    Custom-event dispatch is cosmetic (TUI streaming) and requires a parent run
    context. When a node is invoked directly (e.g. unit tests) there is no run,
    and ``adispatch_custom_event`` raises ``RuntimeError``. Swallow it so node
    business logic is never broken by a missing streaming context.
    """
    try:
        await adispatch_custom_event("node_message", {"node": node, "content": content})
    except RuntimeError:
        logger.debug("dispatch_node_message skipped for %s (no run context)", node)


def with_phase_events(
    node_name: str,
    phase: str,
    node_fn: Callable[[AgentState], Awaitable[dict]],
) -> Callable[[AgentState], Awaitable[dict]]:
    """Wrap an async node function to emit phase_started/phase_completed events.

    Applied in ``graph.py`` at node registration — zero changes to node code.
    Handles ``GraphInterrupt`` (from ``interrupt()``) by NOT dispatching
    ``phase_completed`` on interrupt: the node hasn't finished, it's paused.
    On resume the wrapper fires ``phase_started`` again (LangGraph re-invokes
    the node), which is a harmless duplicate the TUI already handles.

    Tracer span recording lives here too: graph nodes call
    ``get_tracker()``/``tracker.start()`` directly (never the
    ``track_status`` context manager), so this wrapper — applied to every
    major node at registration — is the single point where per-node spans
    and the token summary are persisted to the TaskStore. Without it the
    ``task_spans`` table stays empty and ``task_details`` summaries stay
    at zero.

    Parameters
    ----------
    node_name : str
        Must match the name used in ``graph.add_node(node_name, ...)``.
    phase : str
        The 5-stage stepper phase: ``"intent"`` | ``"safety"`` | ``"inject"``
        | ``"verify"`` | ``"recovery"``.
    node_fn : async callable
        The original node function ``(state: AgentState) -> dict``.
    """
    async def wrapped(state: AgentState) -> dict:
        # Phase events are cosmetic (TUI stepper), not functional.
        # If dispatch fails (e.g., no runnable context in tests), the node
        # must still execute its business logic.
        try:
            await dispatch_phase_started(node_name, phase)
        except Exception:
            logger.debug("dispatch_phase_started failed for %s (non-critical)", node_name)

        # --- tracer span setup (never blocks the node) ---
        # Only real task identities get spans (see persistence.task_identity):
        # dialogue turns reuse these nodes without owning a task, and
        # recording them would leak in-memory traces and fabricate DB rows.
        span_ctx = None
        task_id = state.get("task_id", "") if isinstance(state, dict) else ""
        try:
            from chaos_agent.persistence.task_identity import is_real_task_id
            if is_real_task_id(task_id):
                from chaos_agent.observability import status_tracker as _st_mod
                # Attribute LLM token usage to this task: graph nodes never
                # go through track_status (the original set_task_id call
                # site), so the shared tracing callbacks would otherwise
                # route usage to a throwaway trace.
                if _st_mod._tracing_callback is not None:
                    _st_mod._tracing_callback.set_task_id(task_id)
                if _st_mod._otel_callback is not None:
                    _st_mod._otel_callback.set_task_id(task_id)
                from chaos_agent.observability.tracer import get_trace
                trace = await get_trace(task_id)
                span = trace.start_span(node_name)
                from chaos_agent.observability.status_tracker import get_tracker
                history_start = len(get_tracker(task_id)._history)
                span_ctx = (trace, span, history_start)
        except Exception:
            logger.debug("span start failed for %s (non-critical)", node_name)
            span_ctx = None

        async def _end_span(error: str | None = None) -> None:
            """Collect tool calls, close the span and flush the summary."""
            if span_ctx is None:
                return
            trace, span, history_start = span_ctx
            try:
                from chaos_agent.observability.status_tracker import (
                    StatusPhase, get_tracker,
                )
                tool_names: list[str] = []
                events = list(get_tracker(task_id)._history)
                for ev in events[history_start:]:
                    if ev.phase == StatusPhase.RUNNING and ev.detail.get("tool_calls"):
                        tool_names.extend(ev.detail["tool_calls"])
                span.tool_calls = tool_names
                # trace.total_tool_calls is never incremented anywhere else
                # (TracingCallback only counts LLM calls/tokens), so it must
                # be fed here — otherwise flush_trace overwrites the
                # incrementally accumulated count with zero.
                trace.total_tool_calls += len(tool_names)
                await trace.end_span(span, error=error)
                # Persist the summary rollup now: CLI invocations exit right
                # after the last node, so there is no later flush chance.
                from chaos_agent.observability.tracer import flush_trace
                await flush_trace(task_id)
            except Exception:
                logger.debug("span end failed for %s (non-critical)", node_name)

        try:
            result = await node_fn(state)
        except GraphInterrupt:
            # interrupt() pauses the node — don't mark it as completed.
            # On resume, LangGraph re-invokes this wrapper from scratch,
            # firing phase_started again (harmless duplicate). The paused
            # segment is still recorded as a span so the trace timeline
            # shows the node ran until the interrupt.
            await _end_span(error="interrupted (awaiting resume)")
            raise
        except Exception as e:
            await _end_span(error=str(e))
            raise
        else:
            await _end_span()
            try:
                await dispatch_phase_completed(node_name, phase)
            except Exception:
                logger.debug(
                    "dispatch_phase_completed failed for %s (non-critical)",
                    node_name,
                )
            return result
    return wrapped