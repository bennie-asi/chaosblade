"""ResultCard duration scope: operation time only, no intent clarification.

The ResultCard's ``Duration`` row must measure ONLY the pipeline / recover
run. Intent clarification (potentially many dialogue rounds) precedes the
dispatch and is conversation time — a turn that spent 6 minutes clarifying
followed by a 10-minute injection must report 10 minutes, not 16.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from chaos_agent.server.routes import turn_event_stream as stream_mod


class RecordingIntentGraph:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def aupdate_state(self, config, values, as_node=None):
        self.updates.append(
            {"config": config, "values": values, "as_node": as_node}
        )


class EmptyPipelineGraph:
    def astream_events(self, *_args, **_kwargs):
        async def _events():
            if False:
                yield {}

        return _events()

    async def aget_state(self, _config):
        return SimpleNamespace(values={}, next=())


class RecordingStore:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, str]] = []

    def add_task(self, sid: str, task_id: str) -> None:
        self.tasks.append((sid, task_id))


def _ctx(**overrides) -> SimpleNamespace:
    base = dict(
        sid="sid-1",
        turn_id="turn-1",
        thread_id="thread-1",
        intent_graph=RecordingIntentGraph(),
        pipeline_graph=EmptyPipelineGraph(),
        graph_config={
            "configurable": {"thread_id": "thread-1"},
            "recursion_limit": 10,
        },
        tracker_queue=asyncio.Queue(),
        req=SimpleNamespace(),
        store=RecordingStore(),
        dry_run=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _single_iv() -> dict:
    return {
        "task_id": "task-single",
        "tui_session_id": "sid-1",
        "handoff_summary": "[Intent Clarification Summary]",
        "fault_spec": {
            "scope": "pod",
            "target": "cpu",
            "action": "fullload",
            "namespace": "default",
            "names": ["pod-a"],
        },
    }


def _batch_iv() -> dict:
    return {
        "tui_session_id": "sid-1",
        "handoff_summary": "[Intent Clarification Summary]",
        "batch_submit_args": {
            "faults": [
                {
                    "scope": "pod",
                    "target": "pod",
                    "action": "terminate",
                    "namespace": "arms-prom",
                    "names": ["pod-a"],
                },
            ],
            "execution_order": "serial",
            "interval_seconds": 0,
        },
        "fault_spec": {
            "scope": "pod",
            "target": "pod",
            "action": "terminate",
            "namespace": "arms-prom",
            "names": ["pod-a"],
        },
    }


def _cancelled_drain_merged():
    async def fake_drain_merged(*_args, **_kwargs):
        raise asyncio.CancelledError()
        if False:
            yield ""

    return fake_drain_merged


@pytest.mark.asyncio
async def test_inject_pipeline_stamps_duration_origin_at_dispatch(monkeypatch):
    """The duration origin is stamped at pipeline dispatch — even a turn
    aborted right after dispatch must have it (the fallback to the turn
    start is for turns where NO pipeline ever ran)."""

    async def fake_drain_merged(*_args, **_kwargs):
        raise asyncio.CancelledError()
        if False:
            yield ""

    from chaos_agent.agent.nodes.planning import intent_clarification
    monkeypatch.setattr(
        intent_clarification,
        "bootstrap_task_session",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(stream_mod, "_merged_stream", lambda *_a, **_k: object())
    monkeypatch.setattr(stream_mod, "_drain_merged", fake_drain_merged)

    ctx = _ctx()
    t_before = time.monotonic()

    with pytest.raises(asyncio.CancelledError):
        async for _ in stream_mod._run_inject_pipeline(
            ctx,
            _single_iv(),
            batcher=None,
            sidewrite=lambda _evt, **_kw: None,
            converters={},
        ):
            pass

    t_after = time.monotonic()
    assert t_before <= ctx.pipeline_started_monotonic <= t_after


@pytest.mark.asyncio
async def test_batch_pipeline_stamps_duration_origin_at_dispatch(monkeypatch):
    monkeypatch.setattr(stream_mod, "_merged_stream", lambda *_a, **_k: object())
    monkeypatch.setattr(stream_mod, "_drain_merged", _cancelled_drain_merged())

    ctx = _ctx()
    t_before = time.monotonic()

    with pytest.raises(asyncio.CancelledError):
        async for _ in stream_mod._run_batch_pipeline(
            ctx,
            _batch_iv(),
            batcher=None,
            sidewrite=lambda _evt, **_kw: None,
            converters={},
        ):
            pass

    t_after = time.monotonic()
    assert t_before <= ctx.pipeline_started_monotonic <= t_after


@pytest.mark.asyncio
async def test_recover_duration_origin_is_recover_start(monkeypatch):
    """The recover ResultCard's duration origin is the recover graph's own
    start — the dialogue that identified WHICH task to recover must not
    count as recovery time."""

    intent_graph = EmptyPipelineGraph()
    # Intent graph snapshot: confirmed recover intent, no pending interrupt.
    async def _intent_get_state(_config):
        return SimpleNamespace(
            values={
                "confirmed_intent": "recover",
                "recover_task_id": "task-inj-1",
                "task_id": "task-rec-1",
            },
            next=(),
        )

    intent_graph.aget_state = _intent_get_state

    recover_graph = EmptyPipelineGraph()

    async def fake_drain_merged(*_args, **_kwargs):
        if False:
            yield ""

    monkeypatch.setattr(stream_mod, "_merged_stream", lambda *_a, **_k: object())
    monkeypatch.setattr(stream_mod, "_drain_merged", fake_drain_merged)
    monkeypatch.setattr(
        stream_mod,
        "resolve_recover_initial_state",
        _async_return(SimpleNamespace(
            initial_state={"tui_session_id": "sid-1"}, source_values={},
        )),
    )
    monkeypatch.setattr(
        stream_mod,
        "build_recover_result_payload",
        _async_return({"status": "success", "data": {"task_id": "task-rec-1"}}),
    )
    monkeypatch.setattr(
        stream_mod, "build_recover_summary_text", lambda *_a, **_k: "[Recover Summary]",
    )
    monkeypatch.setattr(
        stream_mod, "write_operation_summary", _async_return(None),
    )
    from chaos_agent.agent.nodes.planning import intent_clarification
    from chaos_agent.agent.result import operation_summary as _op_summary_mod
    from chaos_agent.memory import session_store as _session_store_mod
    from chaos_agent.memory import tui_session_store as _tui_store_mod

    monkeypatch.setattr(
        intent_clarification,
        "bootstrap_task_session",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        _op_summary_mod,
        "append_ledger_process_detail",
        lambda text, _sv: text,
    )
    monkeypatch.setattr(
        _session_store_mod, "get_global_session_store", lambda: None,
    )
    monkeypatch.setattr(
        _tui_store_mod, "get_global_tui_session_store", lambda: None,
    )

    ctx = _ctx(
        agents={"recover": recover_graph, "pipeline": EmptyPipelineGraph()},
        intent_graph=intent_graph,
    )
    t_before = time.monotonic()

    events = []
    async for sse in stream_mod._run_recover(
        ctx, intent_graph, ctx.graph_config,
        batcher=None,
        sidewrite=lambda _evt, **_kw: None,
        converters={},
    ):
        events.append(sse)

    t_after = time.monotonic()
    # The recover result event was emitted.
    assert events

    # build_recover_result_payload(recover_graph, recover_config,
    #     recover_task_id, inject_task_id, inject_state_values,
    #     started_monotonic) — the 6th positional arg is the duration origin.
    payload_calls = stream_mod.build_recover_result_payload.await_args_list
    assert payload_calls, "build_recover_result_payload must have been awaited"
    started = payload_calls[0].args[5]
    assert t_before <= started <= t_after


def _async_return(value):
    async def _fake(*_args, **_kwargs):
        return value

    return _fake_with_args(_fake)


class _fake_with_args:
    """Minimal AsyncMock stand-in that records await_args_list."""

    def __init__(self, coro_fn):
        self._coro_fn = coro_fn
        self.await_args_list = []
        self.await_args = None

    async def __call__(self, *args, **kwargs):
        call = SimpleNamespace(args=args, kwargs=kwargs)
        self.await_args_list.append(call)
        self.await_args = call
        return await self._coro_fn(*args, **kwargs)
