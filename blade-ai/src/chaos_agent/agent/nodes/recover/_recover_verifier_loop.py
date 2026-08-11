"""Recover verifier loop: entry functions for two-layer post-recovery verification."""

import asyncio
import json
import logging

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from chaos_agent.agent.node_names import RECOVER_VERIFIER
from chaos_agent.transports import PROFILE_K8S
from chaos_agent.agent.capabilities import (
    build_capability_context,
    filter_tools_for_context,
)
from chaos_agent.agent.nodes.execute._injection_detection import (
    _was_blade_create_attempted,
    was_kubectl_exec_delivery,
)
from chaos_agent.agent.nodes.execute._kubeconfig_inject import (
    _resolve_kubeconfig,
    inject_kubeconfig_into_tool_calls,
    inject_task_id_into_tool_calls,
    sync_kubewiz_runtime,
)
from chaos_agent.agent.nodes.recover._recover_layer1 import (
    RecoverLayer1Result,
    _RECOVER_BASELINE_TOOL_CALL_ID,
    _RECOVER_CONTEXT_KWARGS_KEY,
    _RECOVER_SYNTHETIC_TOOL_CALL_IDS,
    _build_layer1_recovery_prompt,
    _build_recover_baseline_tool_messages,
    _recover_layer1_to_dict,
    # noqa: F401 — backward-compat re-export for tests
    # noqa: F401 — backward-compat re-export for tests
    _parse_layer1_recovery_result,
    _run_recover_layer1,
)
from chaos_agent.agent.nodes.recover._recover_layer2_parse import (
    _build_recover_verifier_prompt,
    _extract_recovery_verification_section,
    _count_recovery_steps_in_skill_case,
    # noqa: F401 — backward-compat re-export for tests
    # noqa: F401
    # noqa: F401
    # noqa: F401
    # noqa: F401
)
from chaos_agent.agent.nodes.store._store_sync import sync_to_store
from chaos_agent.agent.nodes.verify._verifier_shared import (
    _compute_baseline_confidence,
)
from chaos_agent.agent.spec.fault_registry import is_host_scope

from chaos_agent.agent.nodes.execute.llm_step_helpers import post_invoke_debug
from chaos_agent.agent.nodes.execute.react_helpers import (
    emit_debug_tool_messages,
    extract_persistent_hm,
    extract_synthetic_messages,
    extract_tool_call_fields,
    record_system_prompt,
    summarize_llm_response,
)
from chaos_agent.agent.result.operation_outcome import write_recover_verification
from chaos_agent.agent.nodes.verify._verifier_submit import SUBMIT_RECOVER_VERIFICATION_TOOL_NAME
from chaos_agent.agent.spec.skill_identity import read_active_skill_name
from chaos_agent.agent.state import AgentState
from chaos_agent.config.settings import settings
from chaos_agent.agent.state_mgmt.state_helpers import fail_state
from chaos_agent.agent.result.verdict import FailureCategory
from chaos_agent.memory.session_store import NO_SESSION_MARKER
from chaos_agent.observability.status_tracker import (
    get_tracker,
    StatusCategory,
)
from chaos_agent.utils.time import now_iso

logger = logging.getLogger(__name__)

# Loop budget: settings.max_recover_verifier_loop (default 60, env BLADE_AI_MAX_RECOVER_VERIFIER_LOOP)


def _merge_combo_blade_part(
    layer1: RecoverLayer1Result,
    state: AgentState,
    blade_part_override: dict | None = None,
) -> RecoverLayer1Result:
    """Composite Layer-1 verdict for combo (blade + kubectl-native) recovery.

    The blade experiment was destroyed deterministically BEFORE the LLM flow;
    the LLM verdict covers only the native component. Either part failing
    fails the composite — a partially undone fault is still active.

    ``blade_part_override`` carries the verdict computed in the SAME node
    invocation (iteration 1): it lives only in the pending result_update
    there, not yet in ``state``.
    """
    blade_part = blade_part_override or state.get("combo_blade_part") or {}
    if not blade_part:
        return layer1
    # model_dump keeps enum objects — normalize to the plain string value.
    _bp_raw = blade_part.get("status", "")
    bp_status = str(getattr(_bp_raw, "value", _bp_raw) or "")
    if bp_status == "passed":
        layer1.details = (
            f"[blade experiment destroyed deterministically] {layer1.details}"
        )
        return layer1
    bp_details = str(blade_part.get("details", "") or "")
    _native_status = getattr(layer1.status, "value", layer1.status)
    return RecoverLayer1Result(
        status="failed",
        details=(
            f"Combo recovery failed: blade experiment destroy {bp_status}"
            + (f" ({bp_details[:200]})" if bp_details else "")
            + f"; native part: {_native_status} ({(layer1.details or '')[:200]})"
        ),
        raw_output=layer1.raw_output,
    )


def _provider_for_recover(injection_method, blade_uid):
    """Resolve the no-LLM recovery provider through the registry.

    A ``blade_uid`` always means a ChaosBlade experiment to destroy, regardless
    of the detected method (a UID is the strongest signal). Without a UID, use
    the backend the registry resolves from ``injection_method`` — ``host_native``
    -> :class:`HostShellProvider`, ``kubectl_native`` -> :class:`K8sNativeProvider`
    — defaulting to :class:`K8sNativeProvider` (non-ChaosBlade, no UID to destroy)
    when no method has been detected.
    """
    from chaos_agent.agent.providers import FaultProviderRegistry
    from chaos_agent.agent.providers.chaosblade import ChaosbladeProvider
    from chaos_agent.agent.providers.k8s_native import K8sNativeProvider

    if blade_uid:
        return ChaosbladeProvider()
    return FaultProviderRegistry.resolve_by_method(injection_method) or K8sNativeProvider()


def _assemble_recover_result_dict(state, task_id, skill_name, result) -> dict:
    """Assemble the standard recover-verification ``result_dict`` from a provider
    :class:`RecoverResult`.

    Backend-agnostic: the per-carrier verdict lives in the provider; this node
    helper only stitches the node-side ``baseline_confidence`` in, echoes any
    host carrier write-back (``execution_artifacts``), and merges ``fail_state``
    when the provider reported a failure. Keeping ``sync_to_store`` and tracker
    events out of here preserves the two callers' differing side effects.
    """
    verification = {
        "level": result.level,
        "layer1": result.layer1,
        "layer2": result.layer2,
        "baseline_confidence": _compute_baseline_confidence(state),
        "warnings": list(result.warnings),
    }
    result_out = {
        "task_id": task_id,
        "skill": skill_name,
        "blade_uid": result.blade_uid,
        "recovered": result.recovered,
    }
    result_dict = write_recover_verification(
        result=result_out,
        verification=verification,
        finished_at=now_iso(),
    )
    if result.execution_artifacts is not None:
        result_dict["execution_artifacts"] = list(result.execution_artifacts)
    if not result.recovered and result.failure is not None:
        category, detail = result.failure
        result_dict.update(fail_state(category, detail))
    return result_dict


# ---------------------------------------------------------------------------
# Entry: Simple recover verifier (no LLM, Layer 1 only)
# ---------------------------------------------------------------------------

async def recover_verifier(state: AgentState) -> dict:
    """Simple recover verifier without LLM: Layer 1 only."""
    task_id = state.get("task_id", "")
    blade_uid = state.get("blade_uid", "")
    skill_name = read_active_skill_name(state)
    kubeconfig = _resolve_kubeconfig(state)

    # Defense-in-depth: recover blade_uid from message history if missing in state
    if not blade_uid:
        from chaos_agent.agent.nodes.execute.execute_loop import _extract_blade_uid_from_messages
        messages = state.get("messages", [])
        blade_uid = _extract_blade_uid_from_messages(messages) or ""
        if blade_uid:
            logger.info(f"recover_verifier: recovered blade_uid={blade_uid} from message history")

    tracker = get_tracker(task_id)
    tracker.start(
        StatusCategory.NODE,
        "recover_verifier",
        f"Verifying fault recovery (uid={blade_uid or 'N/A'})",
        {"blade_uid": blade_uid, "skill_name": skill_name},
    )

    # Route to the execution-backend provider and let it produce the rich,
    # carrier-agnostic RecoverResult; the node assembles the standard
    # result_dict and owns tracker events (sync_to_store stays with the LLM
    # caller). The provider scans messages for its own delivery sub-variant
    # (e.g. ChaosBlade's kubectl-exec case), so the node passes only messages.
    messages = state.get("messages", [])
    provider = _provider_for_recover(state.get("injection_method"), blade_uid)
    result = await provider.recover(
        state, None,
        blade_uid=blade_uid,
        kubeconfig=kubeconfig,
        messages=messages,
        task_id=task_id,
    )
    result_dict = _assemble_recover_result_dict(state, task_id, skill_name, result)

    layer1_status = (result.layer1 or {}).get("status", "")
    if result.tracker_message:
        tracker.complete(result.tracker_message)
    elif result.recovered:
        tracker.complete(f"Recovery verification: {layer1_status} (uid={blade_uid or 'N/A'})")
    else:
        tracker.complete(f"Recovery verification: {layer1_status}")
    return result_dict


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers for two-layer recovery decomposition
# ---------------------------------------------------------------------------


def _record_layer1_to_session(hook, state, layer1):
    """Record Layer 1 result to session store (programmatic, bypasses hook)."""
    task_id = state.get("task_id", "")
    if hook and getattr(hook, "session_store", None) and task_id:
        hook.session_store.append_raw_message(task_id, {
            "type": "system",
            "content": f"[Recover Layer 1] status={layer1.status}, details={layer1.details}",
            "detail": {
                "layer": 1,
                "status": layer1.status,
                "details": layer1.details,
                "raw_output": (layer1.raw_output or "")[:500],
            },
            "node": RECOVER_VERIFIER,
        })


async def _run_layer1_recovery(
    state, hook, llm, tools, task_id, blade_uid, skill_name, kubeconfig, count, tracker,
):
    """Execute Layer 1 recovery (first iteration, continuation, or cache restore).

    Returns ``(layer1_result, early_return_dict | None)``.  When the second
    element is not ``None``, the caller must return it immediately.
    """
    capability_context = build_capability_context(state, "recover_verify", tools or [])
    visible_tools = filter_tools_for_context(tools or [], capability_context)
    recover_phase = state.get("recover_phase", "layer1_recovery")

    if recover_phase == "layer1_recovery" and count == 1:
        # When injection was done via kubectl exec, the host blade binary
        # cannot destroy the experiment (record not found). Route these
        # cases through the non-ChaosBlade Layer 1 flow (LLM-driven
        # recovery via kubectl tools) instead of blade_destroy.
        # Durable record first: by recovery time compaction may have
        # removed the injection evidence the raw scan needs (see
        # ``was_kubectl_exec_delivery``).
        _kubectl_injection = was_kubectl_exec_delivery(state)

        # COMBO injection (blade experiment + kubectl-native component, either
        # order): destroy the blade experiment deterministically FIRST, then
        # route to the LLM-driven Layer-1 flow for the native undo —
        # deterministic recovery can ONLY destroy the blade experiment and
        # would leak the native mutation, while the LLM route is the superset
        # executor for combo injections.
        #
        # Two durable criteria (no message scans — both survive compaction):
        # 1. the issue-time/UPGRADE marker ``combo_native_issued``;
        # 2. CROSS-CHECK fallback: a native-family attribution alongside a
        #    live blade_uid is combo evidence by itself — a task cannot
        #    legitimately hold both, so if it does, both vehicles acted.
        #    Covers the edge where the marker never landed (detection scan
        #    window missed the UPGRADE).
        from chaos_agent.agent.providers import FaultProviderRegistry

        _method_rv = state.get("injection_method")
        _method_provider_rv = (
            FaultProviderRegistry.resolve_by_method(_method_rv)
            if _method_rv else None
        )
        _combo_native = bool(state.get("combo_native_issued")) or bool(
            blade_uid
            and _method_provider_rv is not None
            and _method_provider_rv.is_multi_step
        )
        _combo_blade_part: dict | None = None

        if blade_uid and not _kubectl_injection and _combo_native:
            _blade_l1 = await _run_recover_layer1(
                blade_uid, kubeconfig,
                messages=state.get("messages", []),
                injection_method=state.get("injection_method"),
            )
            _combo_blade_part = _recover_layer1_to_dict(_blade_l1)
            logger.info(
                f"Combo recovery: blade part handled deterministically "
                f"(status={_blade_l1.status}); native part routed to LLM Layer 1"
            )

        if blade_uid and not _kubectl_injection and not _combo_native:
            # ChaosBlade on host: deterministic blade_destroy + blade_status
            layer1 = await _run_recover_layer1(
                blade_uid, kubeconfig,
                messages=state.get("messages", []),
                injection_method=state.get("injection_method"),
            )
        elif not _combo_native and _was_blade_create_attempted(
            state.get("messages", []),
            injection_method=state.get("injection_method"),
        ):
            # ChaosBlade injection was done but UID unavailable.
            # NEVER steals a combo case: a combo with blade_uid present has
            # already been pre-destroyed above and must reach the LLM flow
            # for the native undo; with blade_uid empty the flag can only
            # survive from a destroyed experiment — the LLM flow (native
            # route) is still the correct vehicle. The terminal branch is
            # meaningful only for the plain "blade attempted, nothing
            # injected, no UID" state.
            layer1 = RecoverLayer1Result(
                status="failed",
                details="blade_create was called during injection but no UID available for recovery",
            )
        else:
            # Non-ChaosBlade OR kubectl exec injection: LLM-driven Layer 1
            # (Layer 1 runs in the main ReAct loop, not a separate sub-loop)
            inject_context = state.get("inject_context", "")

            # For kubectl exec injection, append blade_uid recovery instructions
            if _kubectl_injection and blade_uid:
                original_pod = state.get("kubectl_exec_pod_name")
                pod_hint = ""
                if original_pod:
                    pod_hint = (
                        f"Original injection Pod: `{original_pod}` — prefer this Pod to run the destroy action "
                        f"(its namespace is deployment-specific — locate it across all namespaces if needed).\n"
                        f"If that Pod no longer exists, discover a currently running tool pod across all "
                        f"namespaces by its tool label.\n"
                    )
                inject_context += (
                    f"\n\n## Experiment Recovery (in-cluster injection channel)\n"
                    f"The fault was injected from inside the cluster (the injection tool ran within a tool pod).\n"
                    f"Experiment UID: `{blade_uid}`\n"
                    f"{pod_hint}"
                    f"To recover, you MUST destroy the experiment through the same in-cluster channel:\n"
                    f"run the experiment-destroy command for UID `{blade_uid}` inside a running tool pod, "
                    f"in that pod's own namespace.\n"
                    f"The tool pod namespace is deployment-specific — do NOT assume it; use the "
                    f"namespace you discover.\n"
                )
                logger.info(
                    f"kubectl exec injection detected for uid={blade_uid}, "
                    f"routing to non-ChaosBlade Layer 1 recovery flow"
                )
            elif _combo_native and blade_uid:
                # Combo: blade part already handled deterministically above —
                # tell the LLM the experiment is dealt with and its only job
                # is the native undo (it must not re-destroy anything).
                _bp_raw_status = (_combo_blade_part or {}).get("status", "unknown")
                _bp_status = str(getattr(_bp_raw_status, "value", _bp_raw_status) or "unknown")
                _bp_details = (_combo_blade_part or {}).get("details", "") or ""
                inject_context += (
                    f"\n\n## Experiment Recovery (blade component — handled by the framework)\n"
                    f"The blade experiment (UID `{blade_uid}`) was destroyed deterministically by "
                    f"the framework BEFORE this phase — destroy status: {_bp_status}.\n"
                    + (
                        f"WARNING: the deterministic blade destroy FAILED ({_bp_details[:300]}) — "
                        f"report this in your Details; the experiment component may still be active.\n"
                        if _bp_status != "passed" else ""
                    )
                    + "Do NOT attempt to destroy any experiment yourself.\n"
                    "Your ONLY remaining job: undo the kubectl-native injection component — "
                    "reverse the native mutations recorded in the injection context (e.g. revert "
                    "patches/labels, kill injected processes, remove tc/iptables rules).\n"
                )

            if not inject_context:
                # No inject context — skip Layer 1
                logger.info(f"No inject context for {skill_name}, skipping Layer 1")
                layer1 = RecoverLayer1Result(
                    status="skipped",
                    details="Non-ChaosBlade fault: no inject context available",
                )
                if tracker:
                    tracker.update(
                        "Recover Layer 1 (non-ChaosBlade): skipped - no inject context",
                        {"layer1_status": "skipped", "layer1_type": "non_chaosblade"},
                    )
            else:
                # Build Layer 1 prompt and add to state.messages
                layer1_system_prompt = _build_layer1_recovery_prompt(
                    is_kubectl_blade=bool(_kubectl_injection),
                    profile=capability_context.profile,
                )
                from chaos_agent.agent.spec.fault_spec import read_fault_spec as _rfs_rvl
                _spec_rvl = _rfs_rvl(state)
                layer1_human_content = (
                    f"## Fault Context\n"
                    f"Skill: {skill_name}\n"
                    f"Target names: {list(_spec_rvl.names) if _spec_rvl else []}\n"
                )
                if capability_context.profile == PROFILE_K8S:
                    layer1_human_content += (
                        f"Target namespace: {_spec_rvl.namespace if _spec_rvl else ''}\n"
                        f"Kubeconfig: {kubeconfig or '(default)'}\n"
                    )
                else:
                    layer1_human_content += (
                        f"Target authority: {capability_context.target_authority}\n"
                    )
                # Injection operation context — provides the LLM with what
                # was injected and the original state so it can determine
                # correct recovery actions without guessing (e.g.
                # "from 7 to 3 replicas" → restore to 7).
                _blade_uid_rvl = state.get("blade_uid", "") or ""
                _blast_radius_rvl = state.get("blast_radius_detail", "") or ""
                _blade_parsed = state.get("blade_parsed_flags") or {}
                _spec_params_rvl = (
                    dict(_spec_rvl.params) if _spec_rvl and _spec_rvl.params else {}
                )
                if _blade_uid_rvl or _blast_radius_rvl or _blade_parsed or _spec_params_rvl:
                    layer1_human_content += "\n## Injection Operation\n"
                    if _blade_uid_rvl:
                        layer1_human_content += f"Blade UID: {_blade_uid_rvl}\n"
                    if _blast_radius_rvl:
                        layer1_human_content += f"Impact: {_blast_radius_rvl}\n"
                    if _spec_params_rvl:
                        layer1_human_content += f"Parameters: {_spec_params_rvl}\n"
                    elif _blade_parsed:
                        layer1_human_content += f"Blade key parameters: {_blade_parsed}\n"
                _side_effects_rvl = dict(state.get("side_effects") or {})
                if _side_effects_rvl:
                    layer1_human_content += (
                        "\n## Recorded Side Effects (must be undone or reconciled)\n"
                        "During injection the following collateral changes were recorded "
                        "beyond the primary fault target. Recovery is NOT complete until "
                        "each one is undone, reconciled, or explicitly assessed:\n"
                    )
                    for _se_key, _se_val in _side_effects_rvl.items():
                        _se_text = json.dumps(_se_val, ensure_ascii=False)
                        if len(_se_text) > 500:
                            _se_text = _se_text[:500] + "...(truncated)"
                        layer1_human_content += f"- {_se_key}: {_se_text}\n"
                    layer1_human_content += (
                        "Before touching any resource mentioned above, re-query its CURRENT "
                        "state first — the recorded entries describe the inject-time state.\n"
                    )
                layer1_human_content += (
                    "\n## Recovery Guidance\n"
                    "Use the Injection Operation above to determine the correct recovery actions. "
                    "Use the recovery mechanism supported by the current environment. "
                    "Restore the original state from the Impact description when no experiment UID is available.\n\n"
                )
                if kubeconfig and capability_context.profile == PROFILE_K8S:
                    layer1_human_content += (
                        f"**IMPORTANT**: You MUST pass `kubeconfig='{kubeconfig}'` to EVERY "
                        f"cluster tool call. The default kubeconfig cannot access this cluster. "
                        f"Do NOT omit the kubeconfig parameter.\n"
                    )
                layer1_human_content += (
                    "\nPlease execute the above recovery actions now. "
                    "After completing all actions, output your RECOVERY_EXECUTION_RESULT summary."
                )

                # Add Layer 1 prompt as messages to state (not a separate sub-loop)
                result_update = {
                    "verifier_loop_count": count,
                    "recover_layer1_cache": None,
                    "layer1_iteration_count": 1,
                    # Set unconditionally (mirrors the recover_layer1_cache
                    # clear above): a stale blade-part verdict inherited from
                    # an earlier recover run must not contaminate this run's
                    # composite merge. Non-combo runs write None; combo runs
                    # write the freshly computed pre-destroy verdict.
                    "combo_blade_part": _combo_blade_part,
                }

                # Build the full messages list for LLM: SystemMessage + existing state messages + inject context + new HumanMessage
                inject_msg = None
                if inject_context:
                    inject_msg = HumanMessage(
                        content=(
                            f"## Injection Phase Context (EXPIRED — fault-state data, NOT current)\n"
                            f"The following context was captured during fault injection. "
                            f"It describes what fault was injected and what was observed WHILE THE FAULT WAS ACTIVE.\n"
                            f"This data is STALE — it does NOT represent the current post-recovery state.\n"
                            f"You MUST re-execute currently bound observation tools to obtain CURRENT observations.\n\n"
                            f"{inject_context}\n\n"
                        ),
                        additional_kwargs={NO_SESSION_MARKER: True},
                    )

                messages = list(state.get("messages", []))
                if inject_msg:
                    messages.append(inject_msg)
                messages.append(HumanMessage(content=layer1_human_content))

                # Record system prompt to session store
                record_system_prompt(hook, state, layer1_system_prompt, node_name=RECOVER_VERIFIER)

                # Bind tools and call LLM
                max_l1 = settings.max_recover_layer1_iterations
                is_last_l1 = 1 >= max_l1

                # Deadline/final prompts for edge case where max == 1
                if is_last_l1:
                    messages.append(HumanMessage(content=(
                        "**RECOVERY EXECUTION DEADLINE**: This is the ONLY iteration available.\n"
                        "Tools are unavailable. You MUST provide your recovery execution conclusion "
                        "in this EXACT format:\n\n"
                        "RECOVERY_EXECUTION_RESULT:\n"
                        "- Status: [success/failed]\n"
                        "- Actions: [summary of all actions taken]\n"
                        "- Details: [errors, warnings, or notes — if failed, explain WHY recovery could not be completed]\n\n"
                        "If you cannot determine the result, set Status to \"failed\" and explain why in Details."
                    )))

                # Layer 1 must NOT bind submit_recover_verification — it is a
                # Layer 2 tool for submitting the verification verdict.  If
                # bound during Layer 1, the LLM can call it to bypass the
                # Layer 1 text output path (RECOVERY_EXECUTION_RESULT),
                # leaving the cache stuck at ``in_progress``.
                _layer1_tools = [
                    t for t in visible_tools
                    if getattr(t, "name", "") != SUBMIT_RECOVER_VERIFICATION_TOOL_NAME
                ]
                # ``visible_tools`` empty while ``tools`` was NOT means the
                # capability GATE refused everything — never degrade to an
                # unbound LLM there, or the model's calls reach the static
                # ToolNode anyway. An empty ``_layer1_tools`` (submit was the
                # sole visible tool) or no static tools at all legitimately
                # wants a prose turn — and an unbound LLM is the only way to
                # get one, since a provider rejects an empty ``tools`` array.
                if tools and not visible_tools:
                    llm_to_call = llm.bind_tools([])
                else:
                    llm_to_call = llm if is_last_l1 else (llm.bind_tools(_layer1_tools) if _layer1_tools else llm)

                try:
                    response = await llm_to_call.ainvoke(
                        [SystemMessage(content=layer1_system_prompt)] + messages
                    )
                except Exception as e:
                    logger.error(f"Recover Layer 1 (non-ChaosBlade) LLM call failed: {e}")
                    layer1 = RecoverLayer1Result(status="error", details=f"LLM call failed: {e}", raw_output=str(e))
                    # Fall through to detail_msg update below
                else:
                    # Ensure kubeconfig in tool calls
                    inject_kubeconfig_into_tool_calls(response, kubeconfig)
                    inject_task_id_into_tool_calls(response, state.get("task_id", ""))
                    sync_kubewiz_runtime(state)

                    tool_calls = getattr(response, "tool_calls", None) or []

                    if tool_calls:
                        # LLM wants to call tools — continue Layer 1 ReAct loop
                        msg_list = []
                        if inject_msg:
                            msg_list.append(inject_msg)
                        msg_list.append(HumanMessage(content=layer1_human_content))
                        msg_list.append(response)
                        result_update["messages"] = msg_list

                        if settings.is_debug:
                            debug_info, tool_names = summarize_llm_response(response)
                            tracker.update(
                                f"Recover Layer 1 (non-ChaosBlade) iteration 1 LLM:\n{debug_info}",
                                {"debug": True, "iteration": 1, "tool_calls": tool_names},
                            )
                        else:
                            tool_names = [
                                extract_tool_call_fields(tc)[0]
                                for tc in tool_calls
                            ]
                            tracker.update(
                                "Recover Layer 1 (non-ChaosBlade) iteration 1: calling tools",
                                {"iteration": 1, "tool_calls": tool_names},
                            )

                        # Store system prompt text in cache for subsequent iterations
                        result_update["recover_layer1_cache"] = {
                            "status": "in_progress",
                            "details": "",
                            "raw_output": "",
                            "system_prompt": layer1_system_prompt,
                        }
                        await sync_to_store(state, result_update)
                        return (None, result_update)
                    else:
                        # LLM produced final text — parse Layer 1 result
                        content = getattr(response, "content", "") or ""

                        if settings.is_debug:
                            debug_info, _ = summarize_llm_response(response)
                            tracker.update(
                                f"Recover Layer 1 (non-ChaosBlade) iteration 1 LLM (final):\n{debug_info}",
                                {"debug": True, "iteration": 1, "tool_calls": []},
                            )

                        layer1 = _parse_layer1_recovery_result(content)
                        # Merge ONLY when this invocation pre-destroyed a
                        # blade part: ``state`` may carry a stale verdict
                        # inherited from an earlier recover run, and the
                        # result_update above is already clearing it — the
                        # current invocation's verdict is authoritative.
                        if _combo_blade_part is not None:
                            layer1 = _merge_combo_blade_part(
                                layer1, state, blade_part_override=_combo_blade_part
                            )

                        if tracker:
                            tracker.update(
                                f"Recover Layer 1 (non-ChaosBlade): {layer1.status} - {layer1.details[:100]}",
                                {"layer1_status": layer1.status, "layer1_type": "non_chaosblade"},
                            )

                        # Store Layer 1 output in state.messages for Layer 2 to see
                        msg_list = []
                        if inject_msg:
                            msg_list.append(inject_msg)
                        msg_list.append(HumanMessage(content=layer1_human_content))
                        msg_list.append(response)

                        # Programmatic success guard (mirrors the Layer 2
                        # anti-laziness guard): a success claim with no
                        # post-mutation read-only observation bounces back
                        # into the Layer 1 ReAct loop — fires once, and only
                        # while a further iteration is available.
                        _guard_feedback = _layer1_success_guard_feedback(layer1, state)
                        if _guard_feedback and settings.max_recover_layer1_iterations > 1:
                            result_update["messages"] = msg_list + [
                                HumanMessage(content=_guard_feedback)
                            ]
                            result_update["_layer1_success_guard_fired"] = True
                            result_update["recover_layer1_cache"] = {
                                "status": "in_progress",
                                "details": "",
                                "raw_output": "",
                                "system_prompt": layer1_system_prompt,
                            }
                            if tracker:
                                tracker.update(
                                    "Recover Layer 1: success claim rejected — no "
                                    "post-undo observation (looping back)",
                                    {"layer1_success_guard": True},
                                )
                            await sync_to_store(state, result_update)
                            return (None, result_update)

                        result_update["messages"] = msg_list
                        result_update["recover_layer1_cache"] = _recover_layer1_to_dict(layer1)

                        if layer1.is_terminal():
                            # Layer 1 failed — continue to Layer 2 for state verification
                            # (e.g., kubectl patch 422 may be because the fault was already
                            # auto-recovered by an Operator)
                            logger.info(
                                "Layer 1 (non-ChaosBlade) failed (%s), continuing to Layer 2 "
                                "verification to check actual fault state",
                                layer1.status,
                            )
                            if tracker:
                                tracker.update(
                                    f"Recover Layer 1 failed ({layer1.status}), "
                                    f"continuing to Layer 2 verification",
                                    {"layer1_status": layer1.status, "layer1_failed": True},
                                )

                        # Transition to Layer 2 (regardless of Layer 1 outcome —
                        # Layer 2 verifies actual fault state even if recovery action failed)
                        result_update["recover_phase"] = "layer2_verification"
                        result_update["recover_layer1_type"] = "llm_driven"
                        await sync_to_store(state, result_update)
                        # Return and let the next iteration handle Layer 2
                        # (this iteration already consumed an LLM call for Layer 1)
                        return (None, result_update)

                # If we get here, layer1 was set from the exception case
                # Fall through to the detail_msg update below

        detail_msg = f"Recover Layer 1: {layer1.status}"
        if layer1.details:
            detail_msg += f" - {layer1.details}"
        tracker.update(detail_msg, {"layer1_status": layer1.status})

        _record_layer1_to_session(hook, state, layer1)

    elif recover_phase == "layer1_recovery" and count > 1:
        # Continue Layer 1 ReAct loop (non-ChaosBlade)
        layer1_iteration = state.get("layer1_iteration_count", 0) + 1

        # Check max iterations for Layer 1
        if layer1_iteration > settings.max_recover_layer1_iterations:
            logger.warning(f"Layer 1 (non-ChaosBlade) exceeded max iterations ({settings.max_recover_layer1_iterations})")
            layer1 = RecoverLayer1Result(
                status="error",
                details=f"Layer 1 recovery execution exceeded max iterations ({settings.max_recover_layer1_iterations})",
                raw_output="",
            )
            verification = {
                "level": "unrecovered",
                "layer1": _recover_layer1_to_dict(layer1),
                "layer2": {"status": "skipped", "details": "Layer 1 exceeded max iterations"},
                "baseline_confidence": _compute_baseline_confidence(state),
                "warnings": ["Layer 1 recovery execution exceeded max iterations"],
            }
            result = {
                "task_id": task_id,
                "skill": skill_name,
                "blade_uid": blade_uid,
                "recovered": False,
            }
            tracker.complete("Recovery failed at Layer 1: max iterations exceeded")
            result_dict = write_recover_verification(
                fail_state(FailureCategory.RECOVERY_FAILED, "Layer1=error, Layer2=skipped"),
                result=result,
                verification=verification,
                finished_at=now_iso(),
            )
            await sync_to_store(state, result_dict)
            return (None, result_dict)

        # Get Layer 1 system prompt from cache
        cache = state.get("recover_layer1_cache") or {}
        layer1_system_prompt = cache.get(
            "system_prompt",
            _build_layer1_recovery_prompt(profile=capability_context.profile),
        )

        # Build messages from state
        messages = list(state.get("messages", []))

        max_l1 = settings.max_recover_layer1_iterations

        # Convergence hint: encourage conclusion if already several iterations
        if layer1_iteration >= 3:
            messages.append(HumanMessage(content=(
                "You have already executed several recovery actions. If the actions are complete, "
                "output your RECOVERY_EXECUTION_RESULT summary now rather than taking more actions."
            )))

        # Deadline prompt: tools will be unbound next iteration
        if layer1_iteration >= max_l1 - 1:
            messages.append(HumanMessage(content=(
                f"**RECOVERY EXECUTION DEADLINE**: This is iteration {layer1_iteration} of max {max_l1}.\n"
                f"Based on ALL actions executed so far:\n"
                f"  - If recovery actions are complete, output the RECOVERY_EXECUTION_RESULT format NOW.\n"
                f"  - This is your last chance to use tools — on the next iteration tools will be unavailable.\n\n"
                f"Your Status must be one of:\n"
                f"  - **success**: Recovery actions have been executed successfully\n"
                f"  - **failed**: Recovery actions could not be completed — explain why in Details\n"
            )))

        # Final iteration: no tools, force structured output
        if layer1_iteration >= max_l1:
            messages.append(HumanMessage(content=(
                f"**FINAL RECOVERY EXECUTION ITERATION**: This is iteration {layer1_iteration} of max {max_l1}. "
                f"NO more iterations available. Tools are no longer available.\n"
                f"You MUST provide your final recovery execution conclusion NOW in this EXACT format:\n\n"
                f"RECOVERY_EXECUTION_RESULT:\n"
                f"- Status: [success/failed]\n"
                f"- Actions: [summary of all actions taken]\n"
                f"- Details: [errors, warnings, or notes — if failed, explain WHY recovery could not be completed]\n\n"
                f"If you cannot determine the result, set Status to \"failed\" and explain why in Details."
            )))

        # Per-iteration kubeconfig reminder
        if kubeconfig and capability_context.profile == PROFILE_K8S:
            messages.append(HumanMessage(content=(
                f"**Reminder**: You MUST pass kubeconfig='{kubeconfig}' to every bound tool call."
            )))

        is_last_l1 = layer1_iteration >= max_l1
        # Same filter as first iteration — see comment above.
        _layer1_tools = [
            t for t in visible_tools
            if getattr(t, "name", "") != SUBMIT_RECOVER_VERIFICATION_TOOL_NAME
        ]
        # Same gate/derived distinction as the first iteration: only a GATE that
        # emptied a NON-EMPTY tool set must bind nothing.
        if tools and not visible_tools:
            llm_to_call = llm.bind_tools([])
        else:
            llm_to_call = llm if is_last_l1 else (llm.bind_tools(_layer1_tools) if _layer1_tools else llm)

        try:
            response = await llm_to_call.ainvoke(
                [SystemMessage(content=layer1_system_prompt)] + messages
            )
        except Exception as e:
            logger.error(f"Recover Layer 1 (non-ChaosBlade) LLM call failed at iteration {layer1_iteration}: {e}")
            layer1 = RecoverLayer1Result(status="error", details=f"LLM call failed: {e}", raw_output=str(e))
            # Fall through to terminal check
        else:
            inject_kubeconfig_into_tool_calls(response, kubeconfig)
            inject_task_id_into_tool_calls(response, state.get("task_id", ""))
            sync_kubewiz_runtime(state)
            tool_calls = getattr(response, "tool_calls", None) or []

            result_update = {
                "verifier_loop_count": count,
                "layer1_iteration_count": layer1_iteration,
            }

            if tool_calls:
                # Continue Layer 1 ReAct loop
                result_update["messages"] = [response]

                if settings.is_debug:
                    post_invoke_debug(tracker, response, layer1_iteration, "Recover Layer 1 (non-ChaosBlade) iteration")
                else:
                    tool_names = [
                        extract_tool_call_fields(tc)[0]
                        for tc in tool_calls
                    ]
                    tracker.update(
                        f"Recover Layer 1 (non-ChaosBlade) iteration {layer1_iteration}: calling tools",
                        {"iteration": layer1_iteration, "tool_calls": tool_names},
                    )

                await sync_to_store(state, result_update)
                return (None, result_update)
            else:
                # Layer 1 completed — parse result
                content = getattr(response, "content", "") or ""

                post_invoke_debug(tracker, response, layer1_iteration, "Recover Layer 1 (non-ChaosBlade) iteration")

                layer1 = _parse_layer1_recovery_result(content)
                layer1 = _merge_combo_blade_part(layer1, state)

                if tracker:
                    tracker.update(
                        f"Recover Layer 1 (non-ChaosBlade): {layer1.status} - {layer1.details[:100]}",
                        {"layer1_status": layer1.status, "layer1_type": "non_chaosblade"},
                    )

                # Same programmatic success guard as the first iteration;
                # here a further iteration exists unless this is the last.
                _guard_feedback = _layer1_success_guard_feedback(layer1, state)
                if _guard_feedback and not is_last_l1:
                    result_update["messages"] = [
                        response, HumanMessage(content=_guard_feedback)
                    ]
                    result_update["_layer1_success_guard_fired"] = True
                    result_update["recover_layer1_cache"] = {
                        "status": "in_progress",
                        "details": "",
                        "raw_output": "",
                        "system_prompt": layer1_system_prompt,
                    }
                    if tracker:
                        tracker.update(
                            f"Recover Layer 1 iteration {layer1_iteration}: success "
                            "claim rejected — no post-undo observation (looping back)",
                            {"layer1_success_guard": True},
                        )
                    await sync_to_store(state, result_update)
                    return (None, result_update)

                result_update["messages"] = [response]
                result_update["recover_layer1_cache"] = _recover_layer1_to_dict(layer1)

                if layer1.is_terminal():
                    # Layer 1 failed — continue to Layer 2 for state verification
                    logger.info(
                        "Layer 1 (non-ChaosBlade) failed (%s) at iteration %d, "
                        "continuing to Layer 2 to check actual fault state",
                        layer1.status, layer1_iteration,
                    )
                    if tracker:
                        tracker.update(
                            f"Recover Layer 1 failed ({layer1.status}), "
                            f"continuing to Layer 2 verification",
                            {"layer1_status": layer1.status, "layer1_failed": True},
                        )

                # Transition to Layer 2 (regardless of Layer 1 outcome —
                # Layer 2 verifies actual fault state even if recovery action failed)
                result_update["recover_phase"] = "layer2_verification"
                result_update["recover_layer1_type"] = "llm_driven"
                await sync_to_store(state, result_update)
                return (None, result_update)

        # If we get here, layer1 was set from the exception case above
        detail_msg = f"Recover Layer 1: {layer1.status}"
        if layer1.details:
            detail_msg += f" - {layer1.details}"
        tracker.update(detail_msg, {"layer1_status": layer1.status})

        _record_layer1_to_session(hook, state, layer1)

    else:
        # Restore Layer 1 result from previous iteration's cache
        cache = state.get("recover_layer1_cache") or {}
        layer1 = RecoverLayer1Result(
            status=cache.get("status", "unknown"),
            details=cache.get("details", ""),
            raw_output=cache.get("raw_output", ""),
        )

    return (layer1, None)


def _layer1_success_lacks_landed_evidence(messages) -> bool:
    """True when a Layer 1 success claim has no post-mutation observation.

    Programmatic acceptance criterion (mirrors the Layer 2 anti-laziness
    guard in ``finalize_recover_verification``): the LAST mutating tool call
    in the conversation must be followed by at least one read-only
    observation call — command issuance alone is not evidence that the undo
    landed at the API layer. Mutating/readonly verdicts come from the
    target_guard classifier (no hand-rolled verb lists); classifier failures
    fail open per call so a classification bug cannot wedge recovery.
    A blade-experiment destroy via ``kubectl exec ... blade destroy``
    classifies READONLY, and a Layer 1 with no mutating call at all has
    nothing to confirm — both pass.
    """
    from chaos_agent.agent.target_guard import SCOPE_READONLY, infer_effective_target

    last_mutating_idx = -1
    last_readonly_idx = -1
    for idx, msg in enumerate(messages):
        for tc in getattr(msg, "tool_calls", None) or []:
            name, args = extract_tool_call_fields(tc)
            try:
                effective = infer_effective_target(name, args)
            except Exception as exc:
                logger.warning(
                    "Layer 1 success guard: classifier failed for %s: %s",
                    name or "<unknown>", exc,
                )
                continue
            if effective.scope == SCOPE_READONLY:
                last_readonly_idx = idx
            else:
                last_mutating_idx = idx
    if last_mutating_idx < 0:
        return False
    return last_readonly_idx <= last_mutating_idx


def _layer1_success_guard_feedback(layer1, state) -> str | None:
    """Reject a Layer 1 success claim lacking landed evidence (fires once).

    Returns the guidance text when the claim must bounce back into the
    Layer 1 ReAct loop; ``None`` accepts it. One-shot flag mirrors
    ``recover_layer2_first`` so a model that still cannot produce the
    observation is not looped forever — the claim then proceeds to Layer 2,
    which verifies the actual fault state.
    """
    if str(getattr(layer1.status, "value", layer1.status) or "") != "passed":
        return None
    if state.get("_layer1_success_guard_fired"):
        return None
    if not _layer1_success_lacks_landed_evidence(state.get("messages", [])):
        return None
    return (
        "⚠️ RECOVERY EXECUTION GUARD: Your success claim was rejected — after "
        "your last mutating command there is no read-only observation "
        "confirming the undo LANDED. Command issuance is not evidence. Run "
        "ONE targeted read-only query on the target (e.g., the experiment "
        "CRD is gone, or the workload reflects the change), then output "
        "RECOVERY_EXECUTION_RESULT again."
    )


async def _run_layer2_verification(
    state, hook, llm, tools, task_id, blade_uid, skill_name, kubeconfig, count, tracker, layer1,
):
    """Layer 2: LLM-based fault-specific recovery verification (ReAct step)."""
    capability_context = build_capability_context(state, "recover_verify", tools or [])
    visible_tools = filter_tools_for_context(tools or [], capability_context)
    # Call pre_reason_hook (memory compaction + session recording)
    hook_updates = {}
    if hook:
        hook_updates = await hook(state)

    # Emit ToolMessage results from previous iteration (debug only)
    emit_debug_tool_messages(tracker, state, seed_existing=True)

    # Only resolve recovery instructions on first Layer 2 iteration
    is_first_layer2 = not state.get("layer2_context_added", False)
    inject_context = state.get("inject_context", "")

    # Determine Layer 1 type: "deterministic" (blade_destroy on host) or "llm_driven"
    # (non-ChaosBlade or kubectl exec injection). Used by Layer 2 prompt and context.
    # recover_layer1_type may be None (field default for fresh runs where
    # the deterministic path didn't explicitly set it). Fall back to the
    # blade_uid heuristic in that case.
    _rl1_type = state.get("recover_layer1_type")
    if _rl1_type is None:
        _rl1_type = "deterministic" if blade_uid else "llm_driven"
    _layer1_is_deterministic = _rl1_type == "deterministic"

    # Build messages for LLM
    # inject_ctx_msg: for ChaosBlade faults, Layer 1 didn't add inject context to state.messages
    inject_ctx_msg = None
    messages = list(state.get("messages", []))

    # ── Position-optimized baseline ToolMessage injection ──
    # Baseline ToolMessage before any HumanMessage (early placement
    # gets higher attention per Lost in the Middle).
    # Inject on EVERY iteration, not just count==1, because they are
    # ephemeral (not in AgentState.messages by default).  On count>1,
    # check if they're already in state history (persisted from
    # count==1 via result_update) to avoid duplication.
    _baseline = state.get("baseline_data")
    if _baseline and _baseline.get("success_count", 0) > 0:
        _baseline_in_state = any(
            getattr(m, "tool_call_id", "") == _RECOVER_BASELINE_TOOL_CALL_ID
            for m in messages if isinstance(m, ToolMessage)
        )
        if not _baseline_in_state:
            messages.extend(_build_recover_baseline_tool_messages(_baseline))

    if is_first_layer2:
        from chaos_agent.agent.spec.fault_spec import read_fault_spec as _rfs_rvl2
        _spec_rvl2 = _rfs_rvl2(state)
        target = {
            "namespace": _spec_rvl2.namespace if _spec_rvl2 else "",
            "names": list(_spec_rvl2.names) if _spec_rvl2 else [],
            "labels": dict(_spec_rvl2.labels) if _spec_rvl2 else {},
            "resource_type": _spec_rvl2.scope if _spec_rvl2 else "",
        }
        # Host scope (bare-metal / VM) has no cluster / kubectl / tool pod — the
        # verifier only has read-only host diagnostics bound. Drive the wording
        # off this flag so a host recovery-verification prompt never tells the
        # LLM to "use kubectl tools" (there are none to call).
        _is_host_scope_rv = is_host_scope(_spec_rvl2.scope if _spec_rvl2 else "")
        _is_k8s_profile_rv = capability_context.profile == PROFILE_K8S

        # For ChaosBlade faults, Layer 1 didn't use LLM so inject_context
        # wasn't added to state.messages. Add it now with _no_session marker.
        # For non-ChaosBlade faults, inject_context was added in Layer 1
        # and is already in state.messages — the any() guard skips duplicates.
        if inject_context and not any(
            isinstance(m, HumanMessage) and
            getattr(m, "additional_kwargs", {}).get(NO_SESSION_MARKER)
            for m in messages
        ):
            inject_ctx_msg = HumanMessage(
                content=(
                    f"## Injection Phase Context (EXPIRED — fault-state data, NOT current)\n"
                    f"The following context was captured during fault injection. "
                    f"Use this ONLY to understand what fault was injected.\n"
                    f"⚠️ This data is STALE — it does NOT represent the current post-recovery state.\n"
                    f"DO NOT use injection-phase outputs as 'current' evidence.\n"
                    f"You MUST re-execute currently bound observation tools to obtain CURRENT observations.\n\n"
                    f"{inject_context}\n\n"
                ),
                additional_kwargs={NO_SESSION_MARKER: True},
            )
            messages.append(inject_ctx_msg)

        # Build instructions section (skill-first strategy)
        # P1-4: Only inject recovery verification section + cross-referenced
        # 注入验证 steps, not the entire skill case file. Reduces HumanMessage
        # size by 70-75% while preserving all actionable content.
        skill_case = state.get("skill_case_content", "")
        if skill_case:
            # Extract only the 恢复验证 section + cross-references
            recovery_section = _extract_recovery_verification_section(skill_case)
            # Count expected steps from the extracted section
            expected_steps = _count_recovery_steps_in_skill_case(skill_case)
            step_hint = ""
            if expected_steps > 0:
                step_hint = (
                    f"\n**Expected Verification Steps**: {expected_steps} "
                    f"recovery verification step(s). Your RECOVERY_VERIFICATION_CHECKLIST MUST have "
                    f"at least {expected_steps} items.\n"
                )
            if recovery_section:
                instructions_section = (
                    f"\n## Recovery Verification Instructions\n"
                    f"Follow the recovery verification approach below as the primary reference.\n\n"
                    f"<recovery-verification>\n{recovery_section}\n</recovery-verification>\n\n"
                    f"1. Follow the **恢复验证** section above exactly. "
                    f"Execute every verification step it specifies.\n"
                    f"2. If a step cannot be executed, note it and design an equivalent check.\n"
                    f"3. If ALL steps pass, conclude Layer2 as 'passed'.\n"
                    f"4. If ANY step fails, conclude accordingly.\n"
                    f"5. You MUST produce a RECOVERY_VERIFICATION_CHECKLIST with one item per step.\n"
                    f"{step_hint}\n"
                )
            else:
                # Fallback: inject full content if extraction failed
                instructions_section = (
                    f"\n## Recovery Verification Instructions\n"
                    f"Follow the **恢复验证** section in the skill case as the primary reference.\n\n"
                    f"<skill-case>\n{skill_case}\n</skill-case>\n\n"
                    f"1. Follow the **恢复验证** section exactly.\n"
                    f"2. If a step cannot be executed, note it and design an equivalent check.\n"
                    f"3. You MUST produce a RECOVERY_VERIFICATION_CHECKLIST with one item per step.\n"
                    f"{step_hint}\n"
                )
        else:
            knowledge_example = (
                "recovery verification, kubectl field reference"
                if _is_k8s_profile_rv
                else "recovery verification and host diagnostic references"
            )
            instructions_section = (
                "\n## Recovery Verification Instructions\n"
                "No skill use-case content is available. You MUST use `read_skill_resource` "
                "to try to load the recovery verification instructions, OR design verification based on "
                "the fault type and your experience.\n"
                "**WARNING**: Without skill guidance, verification may be incomplete. "
                "At minimum, you MUST verify the fault effect has been removed from the target.\n"
                "**Knowledge docs**: Check the Domain Knowledge Index for documents whose "
                f"\"When to read\" field covers your current scenario (e.g., {knowledge_example}). "
                "Use `read_knowledge_resource` to "
                "load them before designing your verification plan.\n"
                "**CHECKLIST REQUIRED**: Even without skill guidance, you MUST produce a "
                "RECOVERY_VERIFICATION_CHECKLIST covering each aspect you verify. "
                "This ensures recovery completeness is tracked.\n\n"
            )

        # Build Layer 1 context section — the ChaosBlade-vs-non-ChaosBlade
        # framing is owned by the resolved provider (no carrier branching here).
        _recover_provider = _provider_for_recover(state.get("injection_method"), blade_uid)
        layer1_context, layer2_instruction = _recover_provider.recover_layer2_context(
            state, layer1,
            is_deterministic=_layer1_is_deterministic,
            blade_uid=blade_uid,
            is_host_scope=_is_host_scope_rv,
        )

        context = (
            f"{layer1_context}"
            f"## Fault Context\n"
            f"Skill: {skill_name}\n"
            f"Target names: {target.get('names', [])}\n"
        )
        if _is_k8s_profile_rv:
            context += (
                f"Target namespace: {target.get('namespace', '')}\n"
                f"Kubeconfig: {kubeconfig or '(default)'}\n"
            )
        else:
            context += f"Target authority: {capability_context.target_authority}\n"
        # Structured key parameters from parsed flags (e.g. path, percent, size)
        _blade_parsed = state.get("blade_parsed_flags") or {}
        if _blade_parsed:
            context += f"Blade key parameters: {_blade_parsed}\n"
        # Disk partition/overlay semantics (imagefs vs nodefs, /host, df -h) are
        # NOT hardcoded here: they live in the skill case's 恢复验证 section
        # (embedded above as PRIMARY AUTHORITY) and the knowledge docs
        # (fault-verification-strategies.md), loaded on demand.
        # Timeout info: simplified informational note
        _timeout_val_rv = _blade_parsed.get("timeout")
        if _timeout_val_rv:
            try:
                _timeout_sec_rv = int(str(_timeout_val_rv).strip())
                if _timeout_sec_rv < 600:
                    context += (
                        f"ℹ Duration note: Original --timeout was {_timeout_sec_rv}s. "
                        f"The fault may have already auto-expired before manual recovery. "
                        f"If recovery verification shows no residual fault effects, this is expected.\n"
                    )
            except (ValueError, TypeError):
                pass
        if kubeconfig and _is_k8s_profile_rv:
            context += (
                f"**IMPORTANT**: You MUST pass `kubeconfig='{kubeconfig}'` to EVERY "
                f"cluster tool call. The default kubeconfig cannot access this cluster. "
                f"Do NOT omit the kubeconfig parameter.\n"
            )
        # Tool pod context: provide accurate information about tool pod capabilities
        _blade_scope = _spec_rvl2.scope if _spec_rvl2 else ""
        _blade_target = _spec_rvl2.blade_target if _spec_rvl2 else ""
        _blade_action = _spec_rvl2.blade_action if _spec_rvl2 else ""
        _tool_pod_name = state.get("kubectl_exec_pod_name")
        if _is_k8s_profile_rv and _blade_scope == "node" and _tool_pod_name:
            # Namespace is deployment-specific and never recorded in state —
            # instruct discovery instead of asserting one (task-e9bae269: the
            # pods lived in `default`, not `chaosblade`).
            _ns_line = (
                "- Namespace: unknown — identify it across all namespaces before exec\n"
            )
            context += (
                f"\n## Available Tool Pod\n"
                f"A tool pod is available for cluster-level operations:\n"
                f"- Pod name: `{_tool_pod_name}`\n"
                f"{_ns_line}"
                f"- Capabilities: injection-tool commands, cluster API checks\n"
                f"- LIMITATION: This pod does NOT mount /host. `df -h` shows overlay, NOT host disk.\n"
                f"  For host filesystem verification, use a node debug pod instead.\n"
            )
            if blade_uid:
                context += (
                    f"- **UID Dual Mapping**: The experiment UID ({blade_uid}) is the CRD resource name. "
                    f"Inside a tool pod, the injection tool's local status subcommand searches the LOCAL "
                    f"experiment database and typically returns 'record not found' for an experiment "
                    f"created through the cluster API — NEVER use it for this check (it causes false "
                    f"conclusions). Query the experiment CRD through the cluster API instead "
                    f"(discover the experiment resource kind via the cluster query tool "
                    f"itself; knowledge docs provide reference forms).\n"
                )
        # Injection verification baseline (from inject phase Layer 2 observations)
        inject_summary = state.get("inject_verification_summary", "")
        if inject_summary:
            context += (
                f"\n## Injection Verification Baseline (for comparison — NOT current state)\n"
                f"During the injection phase, the following was observed when the fault was active:\n"
                f"{inject_summary}\n\n"
                f"Compare your CURRENT observations against this. "
                f"If the current state matches what was observed during injection, the fault "
                f"has NOT been recovered.\n"
                f"**Baseline integrity**: Ensure you compare metrics from the SAME resource "
                f"(same partition, same node, same pod). See BASELINE INTEGRITY rules below.\n"
            )
        # Pre-injection state reference from the injection plan
        _blast_radius_l2 = state.get("blast_radius_detail", "") or ""
        if _blast_radius_l2:
            context += (
                f"\n## Pre-Injection State Reference\n"
                f"The injection plan recorded this impact: {_blast_radius_l2}\n"
                f"Recovery is confirmed when CURRENT state matches the "
                f"PRE-injection state described above.\n"
            )
        # Side effects recorded during injection — Layer 2 must verify each
        # one is undone/reconciled; any residual means recovery is incomplete.
        _side_effects_l2 = dict(state.get("side_effects") or {})
        if _side_effects_l2:
            context += (
                "\n## Side-Effect Reconciliation (verification duty)\n"
                "The injection recorded these collateral changes. For EACH entry, "
                "query its CURRENT state and confirm it is undone or reconciled:\n"
            )
            for _se_key_l2, _se_val_l2 in _side_effects_l2.items():
                _se_text_l2 = json.dumps(_se_val_l2, ensure_ascii=False)
                if len(_se_text_l2) > 500:
                    _se_text_l2 = _se_text_l2[:500] + "...(truncated)"
                context += f"- {_se_key_l2}: {_se_text_l2}\n"
            context += (
                "Report each entry's post-recovery status in your checklist. If ANY entry "
                "still shows inject-time state, the overall verdict must be unrecovered. "
                "List residual side effects as warnings.\n"
            )
        # Baseline data is now injected as synthetic AIMessage+ToolMessage pairs
        # (via _build_recover_baseline_tool_messages) BEFORE the main
        # HumanMessage, instead of as plain-text inside HumanMessage.
        # Same pattern as verifier.py inject verifier — see academic
        # basis there (Lost in the Middle, TIM-PRM, VERITAS).
        # Instructions are injected UNCONDITIONALLY (regardless of
        # baseline availability) — they contain polling strategy and
        # verification method guidance that the LLM always needs.
        context += (
            f"{instructions_section}\n"
            f"{layer2_instruction}"
            "**POLLING STRATEGY (CRITICAL)**: Recovery effects may take seconds "
            "to minutes to clear — the convergence tempo depends on the mechanism. "
            "Check at least twice before concluding. If first check shows recovery, "
            "do ONE confirmation check. If fault persists, re-check at spaced intervals.\n\n"
            "**STEP CONCLUSION RULE**: "
            "You may conclude any step early if continued attempts are unlikely to yield new information.\n"
            "When concluding early, you MUST provide:\n"
            "1. What you tried (commands/methods)\n"
            "2. What you observed (actual output)\n"
            "3. Why further attempts would not change the outcome\n\n"
            "**Observation fallback**: If a bound observation method is unavailable, "
            "use another currently bound method and record why it is equivalent.\n"
        )
        messages.append(HumanMessage(
            content=context,
            additional_kwargs={_RECOVER_CONTEXT_KWARGS_KEY: True},
        ))

    # Convergence hint preserves model discretion while making remaining evidence explicit.
    if count >= 4:
        messages.append(HumanMessage(content=(
            "You have gathered sufficient CURRENT (post-recovery) evidence across multiple iterations. "
            "If your observations clearly show recovery, call submit_recover_verification now. "
            "Do NOT repeat the same check — conclude based on evidence already collected in THIS Layer 2 iteration."
        )))
    if count >= settings.max_recover_verifier_loop - 1:
        messages.append(HumanMessage(content=(
            f"**RECOVERY VERIFICATION DEADLINE**: This is iteration {count} of max {settings.max_recover_verifier_loop}. "
            f"Tools are already unavailable — conclude from the evidence gathered.\n"
            f"Based on ALL evidence gathered so far, output the RECOVERY_VERIFICATION_RESULT format NOW.\n\n"
            f"Your Overall conclusion must be one of:\n"
            f"  - **recovered**: The fault's cause is undone and every residual deviation is "
            f"attributable to recovery propagation (trajectory improving toward baseline)\n"
            f"  - **unrecovered**: Fault effect is STILL present despite recovery attempt\n"
        )))

    # Per-iteration kubeconfig reminder (first Layer 2 iteration already has it in the main context)
    if (
        state.get("layer2_context_added", False)
        and kubeconfig
        and capability_context.profile == PROFILE_K8S
    ):
        messages.append(HumanMessage(content=(
            f"**Reminder**: You MUST pass kubeconfig='{kubeconfig}' to every bound tool call."
        )))

    # Final-iteration conclusion prompt (tools already unbound at max-1)
    if count >= settings.max_recover_verifier_loop:
        # Single source with the system-prompt label (see the builder call
        # below): the raw blade_uid heuristic mislabels kubectl-blade/combo
        # recoveries, whose Layer 1 is LLM-driven despite a live UID.
        layer1_label = "blade_destroy" if _layer1_is_deterministic else "recovery execution"
        messages.append(HumanMessage(content=(
            f"**FINAL RECOVERY VERIFICATION ITERATION**: This is iteration {count} of max {settings.max_recover_verifier_loop}. "
            f"NO more iterations available. Tools are no longer available.\n"
            f"You MUST provide your final recovery verification conclusion NOW in this EXACT format:\n\n"
            f"RECOVERY_VERIFICATION_RESULT:\n"
            f"- Layer1 ({layer1_label}): passed\n"
            f"- Layer2 (fault-specific): [passed/failed/skipped] - [details with evidence summary]\n"
            f"- BaselineUsed: [true/false]\n"
            f"- Overall: [recovered/unrecovered]\n"
            f"- Warnings: [any warnings, or \"none\"]\n\n"
            f"If you cannot determine the result, set Overall to \"unrecovered\" and explain why in Layer2 details."
        )))

    # Bind tools for LLM (unbind one iteration early to force summary)
    if count >= settings.max_recover_verifier_loop - 1:
        llm_to_call = llm
    elif is_first_layer2 and tools:
        # P0-4: Force at least one tool call on the first Layer 2 iteration.
        # Prevents "lazy verification" (LLM outputs conclusion without
        # executing any kubectl commands). This complements the prompt-level
        # CRITICAL RULE 1 ("Execute kubectl to observe CURRENT state").
        #
        # NOTE: DashScope's OpenAI-compatible endpoint only supports
        # tool_choice "none" and "auto" — it rejects "required"/"any".
        # We use "auto" here, relying on the prompt-level rule to ensure
        # the LLM calls at least one tool. The programmatic guarantee is
        # weakened compared to OpenAI's "required", but this is an API
        # constraint we cannot bypass.
        llm_to_call = llm.bind_tools(visible_tools)  # tool_choice defaults to "auto"
    else:
        # Empty ``visible_tools`` from a NON-EMPTY ``tools`` is the capability
        # gate refusing everything — bind an empty tool set rather than an
        # unbound LLM (fail-closed). With no static tools at all there is
        # nothing to refuse and the unbound LLM is the intended prose path.
        if visible_tools:
            llm_to_call = llm.bind_tools(visible_tools)
        else:
            llm_to_call = llm.bind_tools([]) if tools else llm

    # Extract synthetic AIMessage+ToolMessage pairs from the local messages
    # list for state persistence. On count==1, prepend them to
    # result_update["messages"] BEFORE the response so that
    # state["messages"][-1] remains the real AIMessage (routing-safe).
    _synthetic_for_state = extract_synthetic_messages(messages, _RECOVER_SYNTHETIC_TOOL_CALL_IDS)

    # Extract the main recover context HumanMessage for state persistence.
    _main_hm_for_state = extract_persistent_hm(messages, state, _RECOVER_CONTEXT_KWARGS_KEY)

    from chaos_agent.agent.progress_ledger import build_ledger_prompt_section
    system_prompt = _build_recover_verifier_prompt(
        layer1_label="blade_destroy" if _layer1_is_deterministic else "recovery execution",
        profile=capability_context.profile,
        ledger_section=build_ledger_prompt_section(state.get("progress_ledger")),
    )

    # Record system prompt to session store (dedup handles repeated prompts)
    record_system_prompt(hook, state, system_prompt, node_name=RECOVER_VERIFIER)

    try:
        response = await asyncio.wait_for(
            llm_to_call.ainvoke(
                [SystemMessage(content=system_prompt)] + messages
            ),
            timeout=settings.llm_read_timeout,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Recover Layer 2 LLM call timed out after %ds",
            settings.llm_read_timeout,
        )
        tracker.complete("Recover Layer 2 LLM call timed out")
        result_dict = write_recover_verification(
            {
                "verifier_loop_count": count,
                "recover_layer1_cache": _recover_layer1_to_dict(layer1),
                "layer2_context_added": True,
                **fail_state(
                    FailureCategory.RECOVERY_VERIFICATION_TIMEOUT,
                    f"Layer 2 LLM call timed out after {settings.llm_read_timeout}s",
                ),
            },
            result={"task_id": task_id, "skill": skill_name, "blade_uid": blade_uid, "recovered": False},
            verification={
                "level": "unrecovered",
                "layer1": _recover_layer1_to_dict(layer1),
                "layer2": {"status": "error", "details": f"LLM call timed out after {settings.llm_read_timeout}s"},
                "baseline_confidence": _compute_baseline_confidence(state),
            },
            finished_at=now_iso(),
        )
        from chaos_agent.memory.hook import merge_hook_updates
        merge_hook_updates(result_dict, hook_updates)
        await sync_to_store(state, result_dict)
        return result_dict
    except Exception as e:
        logger.error(f"Recover Layer 2 LLM call failed: {e}")
        tracker.complete(f"Recover Layer 2 LLM call failed: {e}")
        result_dict = write_recover_verification(
            {
                "verifier_loop_count": count,
                "recover_layer1_cache": _recover_layer1_to_dict(layer1),
                "layer2_context_added": True,
                **fail_state(
                    FailureCategory.RECOVERY_FAILED,
                    f"Layer 2 LLM call failed: {e}",
                ),
            },
            result={"task_id": task_id, "skill": skill_name, "blade_uid": blade_uid, "recovered": False},
            verification={
                "level": "unrecovered",
                "layer1": _recover_layer1_to_dict(layer1),
                "layer2": {"status": "error", "details": f"LLM call failed: {e}"},
                "baseline_confidence": _compute_baseline_confidence(state),
            },
            finished_at=now_iso(),
        )
        from chaos_agent.memory.hook import merge_hook_updates
        merge_hook_updates(result_dict, hook_updates)
        await sync_to_store(state, result_dict)
        return result_dict

    # Programmatic kubeconfig injection: ensure every kubectl/blade tool call
    # has the correct kubeconfig, even if the LLM forgot to include it.
    inject_kubeconfig_into_tool_calls(response, kubeconfig)
    inject_task_id_into_tool_calls(response, state.get("task_id", ""))
    sync_kubewiz_runtime(state)

    # Problem B guard: Layer 2 read-only discipline is enforced by the
    # recover_verifier_screener graph-edge node (graph.py) between this
    # node and recover_verifier_tools, mirroring phase1_screener /
    # tool_screener — mutating calls are refused with unrecovered
    # guidance instead of verifier-side repairs.

    # Build result
    result_update = {
        "verifier_loop_count": count,
        "recover_layer1_cache": _recover_layer1_to_dict(layer1),  # persist for subsequent iterations
        "layer2_context_added": True,  # mark Layer 2 context as built
    }

    tool_calls = getattr(response, "tool_calls", None) or []
    # Scheme B: recover_verifier_loop Layer 2 is a pure ReAct step.
    # Persist the response (+ synthetic/context messages); routing decides
    # next — should_continue_recover_verifier sends tool_calls -> tools, or
    # a Layer 2 verdict text -> finalize_recover_verification. All Layer 2
    # finalization (guard + parse + baseline + retry + cleanup) now lives
    # in the finalize_recover_verification node.
    result_update["messages"] = (
        _main_hm_for_state + _synthetic_for_state
        + ([inject_ctx_msg] if inject_ctx_msg else []) + [response]
    )
    # Pass the first-Layer2 signal to finalize_recover_verification so its
    # anti-laziness guard fires exactly once (a conclusion produced before
    # any kubectl verification ran). Mirrors the original is_first_layer2 guard.
    result_update["recover_layer2_first"] = is_first_layer2

    if settings.is_debug:
        post_invoke_debug(tracker, response, count, "Recover Layer 2 iteration")
    else:
        _tc_names = [extract_tool_call_fields(tc)[0] for tc in tool_calls]
        tracker.update(
            f"Recover Layer 2 iteration {count}: "
            + ("calling tools" if tool_calls else "emitting verdict text"),
            {"iteration": count, "tool_calls": _tc_names},
        )

    from chaos_agent.memory.hook import merge_hook_updates
    merge_hook_updates(result_update, hook_updates)
    await sync_to_store(state, result_update)
    return result_update


# ---------------------------------------------------------------------------
# Entry: Full recover verifier with LLM (two-layer)
# ---------------------------------------------------------------------------

def make_recover_verifier(hook=None, llm=None, tools=None, registry=None):
    """Create a recover verifier node with two-layer verification.

    Layer 1 (Execute recovery):
      - ChaosBlade faults: blade_destroy + blade_status (deterministic)
      - Non-ChaosBlade faults: LLM executes recovery actions via kubectl tools
    Layer 2 (Verify recovery):
      - LLM verifies fault effect is removed (ReAct loop)
        - Priority 1: Use skill's "恢复验证" section
        - Priority 2: LLM designs verification based on fault context
        - Priority 3: LLM outputs skipped → auto-warning
    When llm is None, falls back to Layer 1 only.
    """
    if llm is None:
        return recover_verifier

    async def _recover_verifier_with_llm(state: AgentState) -> dict:
        task_id = state.get("task_id", "")
        blade_uid = state.get("blade_uid", "")
        skill_name = read_active_skill_name(state)
        kubeconfig = _resolve_kubeconfig(state)
        count = state.get("verifier_loop_count", 0) + 1

        # Reset time_wait consecutive-call guard (mirrors execute_loop).
        # Without this, once time_wait runs in the recover verifier, the
        # global _last_tool_was_wait flag is never cleared and every
        # subsequent time_wait call gets a false-positive rejection.
        from chaos_agent.tools.wait import check_and_reset_wait_guard
        check_and_reset_wait_guard(state.get("messages", []))

        # Defense-in-depth: recover blade_uid from message history if missing in state
        if not blade_uid:
            from chaos_agent.agent.nodes.execute.execute_loop import _extract_blade_uid_from_messages
            messages = state.get("messages", [])
            blade_uid = _extract_blade_uid_from_messages(messages) or ""
            if blade_uid:
                logger.info(f"recover_verifier_with_llm: recovered blade_uid={blade_uid} from message history")

        tracker = get_tracker(task_id)
        tracker.start(
            StatusCategory.NODE,
            "recover_verifier",
            f"Verifying fault recovery (uid={blade_uid or 'N/A'}, iteration={count})",
            {"blade_uid": blade_uid, "skill_name": skill_name, "iteration": count},
        )

        # ---- Guard: max iterations exceeded ----
        if count > settings.max_recover_verifier_loop:
            logger.warning(f"Recover verifier loop exceeded max iterations ({settings.max_recover_verifier_loop})")
            verification = {
                "level": "partial",
                "layer1": {"status": "passed", "details": "Confirmed in earlier iterations"},
                "layer2": {"status": "skipped", "details": "Max iterations reached, could not confirm recovery"},
                "baseline_confidence": _compute_baseline_confidence(state),
                "warnings": ["Recover verifier loop exceeded max iterations — fault may still be active"],
            }
            result = {
                "task_id": task_id,
                "skill": skill_name,
                "blade_uid": blade_uid,
                "recovered": False,  # Cannot confirm recovered
            }
            result_dict = write_recover_verification(
                fail_state(
                    FailureCategory.RECOVERY_VERIFICATION_TIMEOUT,
                    f"max_iterations={settings.max_recover_verifier_loop}",
                ),
                result=result,
                verification=verification,
                finished_at=now_iso(),
            )
            await sync_to_store(state, result_dict)
            return result_dict

        # ---- Layer 1: Execute recovery ----
        layer1, early = await _run_layer1_recovery(
            state, hook, llm, tools, task_id, blade_uid, skill_name, kubeconfig, count, tracker,
        )
        if early is not None:
            return early

        # ---- Terminal check: Layer 1 failed ----
        if layer1.is_terminal():
            if blade_uid:
                # ChaosBlade fault: blade_destroy failed → skip Layer 2
                # (preserve existing ChaosBlade recovery behavior)
                verification = {
                    "level": "unrecovered",
                    "layer1": _recover_layer1_to_dict(layer1),
                    "layer2": {"status": "skipped", "details": "Layer 1 failed, skipping Layer 2"},
                    "warnings": [f"Layer 1 recovery verification failed: {layer1.details}"],
                    "baseline_confidence": _compute_baseline_confidence(state),
                }
                result = {
                    "task_id": task_id,
                    "skill": skill_name,
                    "blade_uid": blade_uid,
                    "recovered": False,
                }
                tracker.complete(f"Recovery failed at Layer 1: {layer1.status}")
                result_dict = write_recover_verification(
                    fail_state(
                        FailureCategory.RECOVERY_FAILED,
                        f"Layer1={layer1.status}, Layer2=skipped, details={layer1.details[:200]}",
                    ),
                    result=result,
                    verification=verification,
                    finished_at=now_iso(),
                )
                await sync_to_store(state, result_dict)
                return result_dict
            else:
                # Non-ChaosBlade fault: Layer 1 failed but continue to Layer 2
                # Layer 2 verifies current fault state (fault may have self-recovered,
                # e.g. kubectl patch 422 because Pod was already restored by Operator)
                logger.info(
                    "Layer 1 failed (%s) for non-ChaosBlade fault, continuing to "
                    "Layer 2 to verify actual fault state",
                    layer1.status,
                )
                tracker.update(
                    f"Layer 1 failed ({layer1.status}), continuing to Layer 2 verification",
                    {"layer1_status": layer1.status, "layer1_failed": True},
                )

        # ---- Layer 2: LLM-based fault-specific recovery verification ----
        return await _run_layer2_verification(
            state, hook, llm, tools, task_id, blade_uid, skill_name, kubeconfig, count, tracker, layer1,
        )

    return _recover_verifier_with_llm
