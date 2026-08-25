"""Session finalization and auto-rollback utilities for CLI / TUI."""

from __future__ import annotations

import logging

from chaos_agent.memory.session_finalizer import (
    finalize_inject_session as _finalize_inject_session,  # noqa: F401  (re-exported: cli.runner imports it from here)
)

logger = logging.getLogger(__name__)


def _format_error(e: Exception) -> tuple[int, str]:
    """Format an exception into (error_code, message) with type info.

    - ChaosAgentError subclasses: use their built-in error_code
    - Other exceptions: code 4001 with type name prefix for debuggability
    """
    from chaos_agent.errors import ChaosAgentError

    if isinstance(e, ChaosAgentError):
        return e.error_code, f"{type(e).__name__}: {e}"
    return 4001, f"{type(e).__name__}: {e}"


async def auto_rollback(graph, config) -> str:
    """Attempt to roll back an orphaned fault handle after inject failure.

    Dispatches by handle kind through the provider registry (blade UID
    destroy, native reverse ops, ...). Returns a human-readable status
    suffix (e.g. " (auto-rolled back experiment_uid=...)"); empty string when
    no rollback was needed.
    """
    try:
        current_state = await graph.aget_state(config)
        if current_state and current_state.values:
            values = current_state.values
            from chaos_agent.agent.state import materialize_fault_handle
            handle = materialize_fault_handle(values)
            if handle:
                logger.warning(
                    "Auto-rollback: dispatching fault handle %s after inject failure",
                    handle,
                )
                from chaos_agent.agent.providers import FaultProviderRegistry
                return await FaultProviderRegistry.rollback_handle(
                    handle, kubeconfig=values.get("kubeconfig", ""),
                )
    except Exception as rb_err:
        logger.error("Auto-rollback failed: %s", rb_err)
        return f" (rollback FAILED: {rb_err})"
    return ""
