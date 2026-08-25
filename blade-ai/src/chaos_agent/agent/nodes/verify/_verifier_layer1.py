"""Layer 1 state orchestration for verifier.

Extracted from verifier.py — kept in the nodes layer because the
orchestration consumes node-level facilities (provider registry resolution,
AgentState caching). The Layer-1 EXECUTION domain (blade_status /
blade_query_k8s parsing, the kubectl-exec and host-blade runners, and their
private types/constants) physically lives in
``providers/chaosblade/verify.py`` since phase-4 T4; the transitional
aliases this module used to re-export were retired with phase-5.

Symbols (owned here — state orchestration):
  Functions: run_layer1_for_state, _restore_layer1_from_state
"""

from chaos_agent.agent.state import AgentState
from chaos_agent.agent.result.verdict import Layer1Result


async def run_layer1_for_state(
    state: dict, experiment_uid: str, kubeconfig: str, *, task_id: str = "",
) -> Layer1Result:
    """Dispatch Layer-1 verification through the resolved FaultProvider seam.

    Resolves the execution backend through the registry's four-level fault
    identity dispatch — the SAME resolution both verifier entries and the
    recover chain use, so every re-dispatch on the same state agrees on the
    same provider (phase-4 T5). UID-bearing claims route to the experiment
    carrier (its host-blade helper decides poll / warning / skipped from
    ``experiment_uid`` + message history and never polls with an empty UID); an
    evidence-less state routes to the UID-less native carrier, whose Layer-1
    verdict is ``skipped`` (the fault effect is checked in Layer 2) —
    mirroring the recover chain's dispatch semantics.

    ``experiment_uid`` is passed explicitly because the caller resolves it from
    the dispatch identity (which may differ from ``state['experiment_uid']``);
    all other inputs (messages, injection_method, injection_pod_name) come
    from ``state``.
    """
    from chaos_agent.agent.providers import FaultProviderRegistry

    provider, _identity = FaultProviderRegistry.resolve_fault_dispatch(state)
    return await provider.layer1_verify(
        state, experiment_uid=experiment_uid, kubeconfig=kubeconfig, task_id=task_id,
    )


# ---------------------------------------------------------------------------
# Refactor 5: 提取 Layer 1 结果从 state 恢复的逻辑
# 原因: 后续迭代需要复用第一轮的 Layer 1 结果，但之前的实现用
#        f-string 拼凑丢失了 raw_output，LLM 看不到完整上下文
# 做法: 将 raw_output 也存入 verification dict，恢复时完整重建
# ---------------------------------------------------------------------------

def _restore_layer1_from_state(state: AgentState) -> Layer1Result:
    """Restore Layer 1 result from a previous iteration's cache."""
    cache = state.get("inject_layer1_cache") or {}
    return Layer1Result.model_validate(cache) if cache else Layer1Result()
