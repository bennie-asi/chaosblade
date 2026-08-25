"""Recover graph initial-state builders.

Recover can be launched from CLI, TS TUI, HTTP streaming, and L4.  All of
those entry points need the same rule: copy only durable inject facts, then
reset recover/runtime fields so stale verification, messages, and loop state
cannot leak into the new recover graph.
"""

from __future__ import annotations

from chaos_agent.agent.spec.fault_spec import fault_spec_from_legacy_state
from chaos_agent.agent.spec.skill_identity import read_active_skill_name
from chaos_agent.agent.state import materialize_fault_handle
from chaos_agent.agent.state_mgmt.state_lifecycle import (
    ensure_recover_runtime_defaults,
    recover_reset_state,
)


def build_recover_initial_from_checkpoint(
    inject_values: dict,
    inject_task_id: str,
    *,
    record_task_id: str | None = None,
    inject_context: str | None = None,
    kubeconfig_override: str | None = None,
    tui_session_id_override: str | None = None,
) -> dict:
    """Build recover initial state from an inject graph checkpoint/state dict."""
    record_task_id = record_task_id or f"recover-{inject_task_id}"
    if inject_context is None:
        from chaos_agent.utils.inject_context import build_inject_context

        inject_context = build_inject_context(inject_values.get("messages", []))

    initial = recover_reset_state()
    initial.update({
        "task_id": record_task_id,
        "tui_session_id": (
            tui_session_id_override
            if tui_session_id_override is not None
            else inject_values.get("tui_session_id", "")
        ) or "",
        "parent_task_id": inject_task_id,
        "recover_task_id": inject_task_id,
        "operation": "recover",
        "experiment_uid": inject_values.get("experiment_uid", ""),
        # Carrier-agnostic fault handle for the recover graph. Inject graphs
        # written before the handle existed (legacy checkpoints, persisted
        # task snapshots) lack the field, so materialize it from whatever
        # attribution facts survived — the registry hydration seam keeps the
        # legacy knowledge provider-side instead of in this builder.
        "fault_handle": materialize_fault_handle(inject_values),
        "skill_name": read_active_skill_name(inject_values),
        "fault_type": inject_values.get("fault_type", "") or "",
        "skill_case_content": inject_values.get("skill_case_content", "") or "",
        "blast_radius_detail": inject_values.get("blast_radius_detail", "") or "",
        # Side effects recorded at injection time (collateral impact beyond
        # the primary target). Carried into recover so Layer 1 can undo /
        # reconcile them and Layer 2 must verify each one.
        "side_effects": dict(inject_values.get("side_effects") or {}),
        "injection_parsed_params": inject_values.get("injection_parsed_params") or {},
        "inject_verification_summary": (
            inject_values.get("inject_verification_summary", "") or ""
        ),
        "inject_context": inject_context or "",
        "baseline_data": inject_values.get("baseline_data"),
        "fault_spec": _recover_fault_spec(inject_values),
        "kubeconfig": (
            kubeconfig_override
            if kubeconfig_override is not None
            else inject_values.get("kubeconfig", "")
        ) or "",
        "kube_context": inject_values.get("kube_context", "") or "",
        "kubewiz_cluster_uuid": inject_values.get("kubewiz_cluster_uuid", "") or "",
        "kubewiz_profile": inject_values.get("kubewiz_profile", "") or "",
        "injection_method": inject_values.get("injection_method"),
        "execution_artifacts": list(inject_values.get("execution_artifacts") or []),
        "kubectl_exec_pod_name": inject_values.get("kubectl_exec_pod_name"),
        "created_at": str(inject_values.get("created_at") or inject_values.get("gmt_create") or ""),
        "tenant_id": inject_values.get("tenant_id", "") or "",
    })
    return initial


def _recover_fault_spec(values: dict) -> dict | None:
    raw = values.get("fault_spec")
    if isinstance(raw, dict) and raw:
        return dict(raw)
    spec = fault_spec_from_legacy_state(values, source="recover_checkpoint")
    return spec.to_dict() if spec else None


__all__ = [
    "build_recover_initial_from_checkpoint",
    "ensure_recover_runtime_defaults",
    "recover_reset_state",
]
