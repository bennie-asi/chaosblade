"""Model-family knowledge: which wire behaviour a model name implies.

Endpoint (URL) detection and model-family detection are orthogonal
dimensions (qwen-code's dual gate): a DashScope endpoint may serve
non-qwen models (e.g. glm via marketplace), and vendor-specific
fields must never leak across the family boundary. The family facts
live here in one place so the thinking-payload builder and any future
per-model gating share the same predicates.
"""

from __future__ import annotations


def normalize_model(model: str | None) -> str:
    """Lowercased model id; empty-safe."""
    return (model or "").strip().lower()


def is_qwen_family(model: str | None) -> bool:
    """True for qwen-family wire model ids (``qwen*``).

    Only qwen-family models read the DashScope thinking knobs
    (``enable_thinking`` / ``reasoning_effort``); shipping them for a
    non-qwen model on the same endpoint is at best ignored and at
    worst rejected.
    """
    return normalize_model(model).startswith("qwen")


def is_tiered_effort_model(model: str | None) -> bool:
    """True for the qwen3.8-max family — the only qwen family that
    reads the tiered ``reasoning_effort`` field directly (``none``
    disables thinking). Older qwen hybrids expose only the on/off
    ``enable_thinking`` switch. Prefix-matched so dated snapshots and
    ``-latest`` aliases are covered.
    """
    return normalize_model(model).startswith("qwen3.8-max")
