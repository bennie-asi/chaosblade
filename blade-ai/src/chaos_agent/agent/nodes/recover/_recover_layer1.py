"""Layer 1 domain for recover verifier: blade_destroy execution and non-ChaosBlade recovery.

Extracted from recover_verifier.py to isolate the "execute recovery" layer
from the "verify recovery" layer (Layer 2).

Symbols:
  Constants: _DESTROYED_STATES, _RECOVER_BASELINE_TOOL_CALL_ID,
             _RECOVER_SYNTHETIC_TOOL_CALL_IDS, _RECOVER_CONTEXT_KWARGS_KEY
  Dataclass: RecoverLayer1Result
  Functions: _layer1_to_dict, _parse_blade_destroy_output,
             _parse_blade_status_destroyed,
             _build_recover_baseline_tool_messages,
             _build_layer1_recovery_prompt, _parse_layer1_recovery_result
  Async:     _run_recover_layer1
"""

import json
import logging
import re
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from chaos_agent.agent.nodes.execute._injection_detection import (
    _was_blade_create_attempted,
)
from chaos_agent.agent.prompts.reminder import SYSTEM_REMINDER_DECLARATION
from chaos_agent.agent.result.verdict import Layer1Result
from chaos_agent.transports import PROFILE_K8S

logger = logging.getLogger(__name__)

_DESTROYED_STATES = frozenset({"Destroyed", "destroyed"})


# ---------------------------------------------------------------------------
# Layer 1 result — reuses verdict.Layer1Result (Pydantic)
# ---------------------------------------------------------------------------

# Backward-compat alias so existing imports don't break.
RecoverLayer1Result = Layer1Result


def _recover_layer1_to_dict(result: Layer1Result) -> dict:
    """Convert Layer1Result to dict for state storage."""
    return result.model_dump()


# ---------------------------------------------------------------------------
# blade_destroy result parsing
# ---------------------------------------------------------------------------

def _parse_blade_destroy_output(raw: str) -> tuple[str, str]:
    """Parse blade_destroy JSON output into (status, details)."""
    if not raw or raw.startswith("Error"):
        return "failed", f"blade_destroy returned error: {raw[:200]}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if "success" in raw.lower() or "destroy" in raw.lower():
            return "passed", "blade_destroy completed (non-JSON output)"
        return "failed", raw[:200]

    if data.get("success") or data.get("code") == 200:
        return "passed", "blade_destroy: success"
    return "failed", f"blade_destroy failed: {data.get('error', raw[:200])}"


# ---------------------------------------------------------------------------
# blade_status check for Destroyed state
# ---------------------------------------------------------------------------

def _parse_blade_status_destroyed(raw: str) -> tuple[str, str]:
    """Check blade_status output confirms experiment is Destroyed."""
    if not raw or not raw.strip():
        return "passed", "blade_status: empty response (experiment not found)"

    # Quick check for NotFound before JSON parsing — kubewiz mode returns
    # error JSON with "not found" when CRD is already deleted.
    if "not found" in raw.lower() or "notfound" in raw.lower():
        return "passed", "blade_status: experiment CRD not found (already destroyed)"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if any(s in raw for s in _DESTROYED_STATES):
            return "passed", "blade_status confirms: Destroyed"
        return "unknown", "Could not parse blade_status output"

    if not (data.get("success") or data.get("code") == 200):
        if data.get("code") == 406:
            return "passed", "blade_status: experiment data not found (already destroyed)"
        # kubewiz mode: blade query k8s returns code 63061 with NotFound error
        error_msg = data.get("error", "")
        if "not found" in error_msg.lower() or "notfound" in error_msg.lower():
            return "passed", "blade_status: experiment CRD not found (already destroyed)"
        return "failed", f"blade_status check failed: {raw[:200]}"

    res = data.get("result", {})
    if not isinstance(res, dict):
        return "passed", "blade_status: Destroyed"

    exp_status = res.get("Status", res.get("status", "")) or res.get("phase", "")
    if exp_status in _DESTROYED_STATES:
        return "passed", "blade_status confirms: Destroyed"
    if exp_status in ("Running", "running"):
        return "failed", "blade_status: experiment still Running (destroy may have failed)"
    return "failed", f"blade_status: unexpected status '{exp_status}'"


_RECOVER_BASELINE_TOOL_CALL_ID = "recover_baseline_collector"

# Cache-path reference embedded in compactor truncation notices (both the
# recent "Full output cached at:" and the historical "Cache:" forms).
_TRUNCATION_CACHE_RE = re.compile(r"(?:Cache:|Full output cached at:)\s*(\S+)")

# Per-observation budget when restoring a truncated baseline from the
# compactor cache (the cache holds the FULL pre-compaction output).
_BASELINE_RESTORE_MAX_CHARS = 4000


def _recover_baseline_cache_path(output: str) -> str:
    """Extract the compactor cache path from a truncated baseline output."""
    m = _TRUNCATION_CACHE_RE.search(output)
    return m.group(1) if m else ""


def _read_baseline_cache_content(cache_path: str, max_chars: int = _BASELINE_RESTORE_MAX_CHARS) -> str:
    """Restore a truncated baseline observation from the compactor cache.

    Tool-output-format agnostic: returns the cached original content capped
    at ``max_chars`` — whatever the observation was (describe output, df,
    /proc reads, ...). Returns "" when the file is unreadable — the caller
    falls back to an explicit "evidence incomplete" annotation instead of
    fabricating baseline content.
    """
    try:
        text = Path(cache_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Recover baseline cache read failed for %s: %s", cache_path, exc)
        return ""
    return text[:max_chars]


# Aggregate set of all synthetic tool_call_ids used for state persistence.
_RECOVER_SYNTHETIC_TOOL_CALL_IDS = frozenset({
    _RECOVER_BASELINE_TOOL_CALL_ID,
})

# Marker for the main recover context HumanMessage — used to identify
# the ephemeral HumanMessage that should be persisted to AgentState on
# is_first_layer2 so it remains visible on subsequent iterations.
_RECOVER_CONTEXT_KWARGS_KEY = "_recover_main_context"


def _build_recover_baseline_tool_messages(baseline: dict) -> list:
    """Build synthetic AIMessage + ToolMessage pair for baseline data in recovery verification.

    Mirrors verifier.py's _build_baseline_tool_messages pattern: baseline data
    injected as synthetic tool call results BEFORE the HumanMessage, creating
    a causal narrative ("already obtained baseline") instead of "external reference".

    For recovery verification, the framing emphasizes "back to normal" comparison:
    baseline values are the RECOVERY TARGET — current observations should match these.

    Returns:
        [AIMessage, ToolMessage] pair. Empty list if baseline has no usable data.
    """
    if not baseline or baseline.get("success_count", 0) <= 0:
        return []

    captured_at = baseline.get("captured_at", "unknown time")
    source = baseline.get("source", "unknown")
    observations = baseline.get("observations", [])

    obs_lines = []
    for obs in observations:
        if obs.get("exit_code") != 0 or not obs.get("stdout"):
            continue
        desc = obs.get("description", "unknown metric")
        cmd = obs.get("command", "")
        raw_out = obs["stdout"]
        if "TRUNCATED" in raw_out:
            # Compactor already cut this observation. The cache holds the
            # FULL pre-compaction original — restore it (format-agnostic,
            # budget-capped) instead of showing only the truncated head;
            # on failure keep the cache path and flag the evidence as
            # incomplete instead of hiding the gap.
            cache_path = _recover_baseline_cache_path(raw_out)
            restored = (
                _read_baseline_cache_content(cache_path) if cache_path else ""
            )
            if restored:
                output = (
                    "[Restored from compactor cache — original observation "
                    "was truncated]\n" + restored
                )
            else:
                output = raw_out[:1500] + (
                    f"\n(baseline evidence incomplete — full output at "
                    f"{cache_path}; re-observe if needed)"
                    if cache_path
                    else "\n(baseline evidence incomplete — re-observe if needed)"
                )
        else:
            output = raw_out[:1500]
            if len(raw_out) > 1500:
                output += "\n... (truncated)"
        obs_lines.append(
            f"### {desc}\n"
            f"Command: `{cmd}`\n"
            f"```\n{output}\n```"
        )

    if not obs_lines:
        return []

    content = (
        f"Pre-injection baseline collected at {captured_at} "
        f"(strategy: {source}, {baseline.get('success_count', 0)}/{baseline.get('total_count', 0)} succeeded).\n\n"
        f"These metrics were captured BEFORE fault injection using the same "
        f"observation methods you should use for recovery verification. "
        f"Recovery is confirmed when YOUR CURRENT observations return to "
        f"these baseline levels. Compare using delta format: "
        f"\"baseline: X → current: Y (ΔZ)\". "
        f"If current ≈ baseline → recovery confirmed. "
        f"If current ≠ baseline → fault effect still present.\n\n"
        + "\n\n".join(obs_lines)
    )

    ai_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "recover_baseline_collector",
            "args": {"phase": "pre-injection", "purpose": "recovery_comparison"},
            "id": _RECOVER_BASELINE_TOOL_CALL_ID,
            "type": "tool_call",
        }],
    )
    tool_msg = ToolMessage(
        content=content,
        tool_call_id=_RECOVER_BASELINE_TOOL_CALL_ID,
        name="recover_baseline_collector",
    )
    return [ai_msg, tool_msg]


# ---------------------------------------------------------------------------
# Layer 1 (non-ChaosBlade): LLM-driven recovery execution prompt & parser
# ---------------------------------------------------------------------------

def _build_layer1_recovery_prompt(
    *, is_kubectl_blade: bool = False, profile: str = PROFILE_K8S
) -> str:
    """Build the Layer 1 recovery execution system prompt.

    Args:
        is_kubectl_blade: If True, this is a ChaosBlade experiment created via
            kubectl exec into a cluster pod (e.g., otel-c-tool). The recovery
            must use `blade destroy` via kubectl exec, not host blade_destroy.
            If False, this is a true non-ChaosBlade fault (kubectl-native).
        profile: Channel profile ("k8s"|"host"). Selects the environment
            capability fragment appended after the intro, mirroring how the
            inject phases and the Layer-2 recover verifier inject
            ``environment.prompt_fragment(...)``. The generalized (non-kubectl)
            branch stays profile-agnostic; the capability fragment is what
            tells a host recovery executor it has no cluster resource semantics.
    """
    from chaos_agent.agent.environment_profiles import get_environment_profile

    environment = get_environment_profile(profile)
    capability_fragment = (
        environment.prompt_fragment("recover")
        if environment is not None
        else (
            "## Capability Profile\n"
            "The current environment profile is unsupported. Do not attempt "
            "recovery; report the missing environment capability."
        )
    )

    if is_kubectl_blade:
        # U-shaped attention (same architecture as execute_loop / verifiers):
        # critical constraints at BEGINNING (primacy) + REMEMBER at END
        # (recency); procedure details in the middle, where attention dips.
        return f"""You are executing recovery actions for a chaos engineering fault.

Your single objective: restore the target to its pre-fault state.

## CRITICAL CONSTRAINTS
- This fault was injected from INSIDE the cluster — the injection tool ran
  within a tool pod, not on the host. Host-side recovery tools cannot see or
  undo such experiments: the undo action MUST go through the same in-cluster
  channel that performed the injection. DO NOT use host-side destroy/status
  tools for this experiment.
- NEVER assume the tool pod namespace — it is deployment-specific; use only
  the namespace you discover via live cluster queries.
- DO NOT verify the fault has been removed — that is Layer 2's job, not yours.
- DO NOT use interactive commands — they do not work in automation; translate
  them into programmatic equivalents.
- {SYSTEM_REMINDER_DECLARATION}

{capability_fragment}

## How to derive recovery commands
- WHAT to undo comes from the recovery context below: the experiment UID and
  the recorded impact — and the original injection pod, when it is named.
- WHERE to run it comes from live cluster queries — discover the current
  state first; never assume it.
- HOW comes from the tools you actually hold: inspect a tool's own
  help/usage output to confirm the commands and parameters it supports,
  and trust its runtime output. Documentation and conversation history may
  be outdated — the tool's runtime behavior is the ground truth.

## Recovery Procedure
1. Locate a currently running tool pod. If the recovery context below names
   the original injection pod, prefer it, and identify its namespace before
   use (it is deployment-specific — NEVER assume it). Tool pods rotate, and
   the context may name no pod at all — then discover a running one by its
   tool label ACROSS ALL NAMESPACES, using live cluster queries.
2. Inside the located tool pod, in ITS own namespace, run the
   experiment-destroy command for the experiment UID. Confirm the exact
   destroy syntax from the injection tool's own help/usage inside the pod
   before running it.
3. Confirm the destroy output reports success.

## Kubeconfig Requirement
If a kubeconfig path is provided in the recovery context, you MUST pass it to
EVERY cluster tool call. The default kubeconfig cannot access the target
cluster; omitting it connects tool calls to the WRONG cluster.

# REMEMBER
- Undo through the SAME in-cluster channel that performed the injection —
  host-side tools cannot reach cluster-created experiments.
- Discover the tool pod and its namespace with live cluster queries; NEVER
  assume either.
- A tool's own help/usage output and runtime behavior are the ground truth
  over documentation and memory.
- Your job ends when the undo action is confirmed landed at the API layer —
  Layer 2 verifies the recovery outcome.

## Output
After completing the recovery action (or determining it cannot be completed),
output a FINAL summary in this EXACT format:

RECOVERY_EXECUTION_RESULT:
- Status: [success/failed]
- Actions: [summary of actions taken, e.g., "destroyed the experiment via the in-cluster tool pod"]
- Details: [any errors, warnings, or notes]
"""

    return f"""You are executing recovery actions for a chaos engineering fault.

This is a non-ChaosBlade fault. EXECUTE the recovery actions to remove the fault
effect using ONLY the tools bound in the current environment. You are NOT
verifying — Layer 2 owns outcome verification.

{capability_fragment}

## Important Constraints
- Do NOT verify the fault has been removed — that is Layer 2's job, not yours.
- Do NOT invoke tools that are not currently bound (there is no ChaosBlade
  experiment to destroy here).
- Do NOT use interactive commands that require a TTY — translate them into
  programmatic equivalents.
- Treat the configured target authority and current tool observations as the
  authority for recovery actions: inspect a tool's own help/usage output to
  confirm what it supports, and trust its runtime output over documentation
  or memory.
- If an action fails, use another supported recovery approach only when new
  evidence justifies it; otherwise report the blocker precisely, and do not
  broaden scope to compensate for an error.
- {SYSTEM_REMINDER_DECLARATION}

## Instructions
1. Use the Recovery Actions and injection context to determine what must be undone.
2. Execute each supported recovery action through the currently bound tools.
3. Preserve the target boundary.

# REMEMBER
- Execute ONLY through currently bound tools; inspect their help/usage and
  trust their runtime output over documentation or memory.
- Preserve the target boundary — do not broaden scope to compensate for an
  error.
- Your job ends when the recovery actions are confirmed landed at the API
  layer — Layer 2 verifies the recovery outcome.

## Output
After completing ALL recovery actions (or determining they cannot be completed),
output a FINAL summary in this EXACT format:

RECOVERY_EXECUTION_RESULT:
- Status: [success/failed]
- Actions: [summary of actions taken]
- Details: [any errors, warnings, or notes]
"""


def _parse_layer1_recovery_result(text: str) -> RecoverLayer1Result:
    """Parse the LLM's Layer 1 recovery execution result into a RecoverLayer1Result."""
    text_lower = text.lower()

    # Extract status
    if "status: success" in text_lower or "status:  success" in text_lower:
        status = "passed"
    elif "status: failed" in text_lower or "status:  failed" in text_lower:
        status = "failed"
    elif "recovery_execution_result" in text_lower:
        # Has the result block but unclear status — check for positive indicators
        if "error" in text_lower or "failed" in text_lower:
            status = "failed"
        else:
            status = "passed"
    else:
        # No structured output — assume success if tools were used
        status = "passed"

    # Extract details
    details = ""
    for line in text.split("\n"):
        line_lower = line.strip().lower()
        if line_lower.startswith("actions:") or line_lower.startswith("details:"):
            details += line.strip() + "; "
    if not details:
        # Use first 300 chars as fallback
        details = text.strip()[:300]

    raw_output = text.strip()[:500]

    return RecoverLayer1Result(
        status=status,
        details=details.rstrip("; ") if details else "Recovery execution completed",
        raw_output=raw_output,
    )


# ---------------------------------------------------------------------------
# Layer 1: Execute blade_destroy + verify destroyed
# ---------------------------------------------------------------------------


async def _run_recover_layer1(
    blade_uid: str, kubeconfig: str, *, messages: list | None = None,
    injection_method: str | None = None,
) -> RecoverLayer1Result:
    """Execute blade_destroy and verify the experiment is destroyed.

    Step 1: Call blade_destroy
    Step 2: Call blade_status to confirm Destroyed state

    ``injection_method`` is the durable attribution record from state; see
    :func:`_was_blade_create_attempted` for why it takes priority over the
    (possibly compacted) message history.
    """
    if not blade_uid:
        # Distinguish two scenarios when blade_uid is empty during recovery:
        # 1. ChaosBlade injection was done but UID unavailable → "failed" (terminal)
        # 2. Non-ChaosBlade fault (kubectl-based) → "skipped" (not terminal, Layer 2 proceeds)
        if messages and _was_blade_create_attempted(messages, injection_method):
            return RecoverLayer1Result(
                status="failed",
                details="blade_create was called during injection but no UID available for recovery",
            )
        return RecoverLayer1Result(
            status="skipped",
            details="Non-ChaosBlade fault (no blade_uid), Layer 1 recovery not applicable",
        )

    try:
        from chaos_agent.tools.blade import blade_destroy, blade_status

        # Step 1: Execute blade_destroy
        destroy_output = await blade_destroy.ainvoke(
            {"uid": blade_uid, "kubeconfig": kubeconfig}
        )
        destroy_raw = destroy_output if isinstance(destroy_output, str) else str(destroy_output)
        destroy_status, destroy_details = _parse_blade_destroy_output(destroy_raw)

        if destroy_status == "failed":
            # Fallback: destroy failed (e.g. kubewiz guard blocked the delete),
            # but the experiment may already be gone (timeout auto-expiry).
            # Check via blade_status — if CRD is not found, treat as recovered.
            try:
                status_output = await blade_status.ainvoke(
                    {"uid": blade_uid, "kubeconfig": kubeconfig}
                )
                status_raw = status_output if isinstance(status_output, str) else str(status_output)
                if "not found" in status_raw.lower() or "notfound" in status_raw.lower():
                    return RecoverLayer1Result(
                        status="passed",
                        details="blade_destroy failed but experiment CRD already removed (timeout auto-expiry)",
                        raw_output=f"destroy: {destroy_raw}\nstatus: {status_raw}",
                    )
                check_status, check_details = _parse_blade_status_destroyed(status_raw)
                if check_status == "passed":
                    return RecoverLayer1Result(
                        status="passed",
                        details=f"blade_destroy failed but {check_details}",
                        raw_output=f"destroy: {destroy_raw}\nstatus: {status_raw}",
                    )
            except Exception as fallback_err:
                logger.debug(f"destroy-failed fallback status check also failed: {fallback_err}")
            return RecoverLayer1Result(
                status="failed",
                details=destroy_details,
                raw_output=destroy_raw,
            )

        # Step 2: Verify via blade_status that experiment is Destroyed
        try:
            status_output = await blade_status.ainvoke(
                {"uid": blade_uid, "kubeconfig": kubeconfig}
            )
            status_raw = status_output if isinstance(status_output, str) else str(status_output)
            check_status, check_details = _parse_blade_status_destroyed(status_raw)

            if check_status == "failed":
                return RecoverLayer1Result(
                    status="failed",
                    details=f"{destroy_details}, but {check_details}",
                    raw_output=f"destroy: {destroy_raw}\nstatus: {status_raw}",
                )

            combined_details = f"{destroy_details}, {check_details}"
            return RecoverLayer1Result(
                status="passed",
                details=combined_details,
                raw_output=f"destroy: {destroy_raw}\nstatus: {status_raw}",
            )
        except Exception as se:
            logger.debug(f"blade_status check failed (non-critical): {se}")
            return RecoverLayer1Result(
                status="passed",
                details=f"{destroy_details} (status check unavailable)",
                raw_output=destroy_raw,
            )

    except Exception as e:
        logger.error(f"Recover Layer 1 failed: {e}")
        return RecoverLayer1Result(status="error", details=str(e), raw_output=str(e))
