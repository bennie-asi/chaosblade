"""Silent-node filtering for the L4 progress channel.

Mirrors the TUI's ``streaming._SILENT_TOKEN_NODES``: postmortem content
is delivered via the result envelope (rendered as a dedicated card), so
its raw LLM tokens must not also stream as progress events — the
``terminal_reports`` node was added when postmortem generation moved
out of ``save_memory`` and must stay in the silent set.
"""

from chaos_agent.l4.events import _is_silent_node


def _chat_stream_event(node: str) -> dict:
    return {
        "event": "on_chat_model_stream",
        "tags": [f"langsmith:nodes:{node}"],
        "metadata": {"langgraph_node": node},
    }


def test_terminal_reports_is_silent():
    assert _is_silent_node(_chat_stream_event("terminal_reports")) is True


def test_save_memory_is_silent():
    assert _is_silent_node(_chat_stream_event("save_memory")) is True


def test_agent_loop_still_streams():
    assert _is_silent_node(_chat_stream_event("agent_loop")) is False


def test_metadata_fallback_without_tags():
    # Some events carry the node only in metadata — the check must
    # fall back to ``langgraph_node`` when no langsmith tag exists.
    event = {
        "event": "on_chat_model_stream",
        "tags": [],
        "metadata": {"langgraph_node": "terminal_reports"},
    }
    assert _is_silent_node(event) is True
