"""submit_verification control-signal tool (Scheme B).

Mirrors the agent_loop ``finish_planning`` pattern: the verifier LLM calls
``submit_verification`` to end the verification ReAct loop and hand a
STRUCTURED verdict to the ``finalize_verification`` node. Going through a
real tool (ToolNode) keeps the message history well-formed (every tool_call
gets a ToolMessage) and decouples "I'm done" from "no tool_calls" — so a
verdict bundled with a cleanup tool_call no longer forces the LLM to repeat
its verdict on a second turn.

The tool body is a no-op confirmation; the verdict lives in the call's
ARGS, read by ``finalize_verification`` from the AIMessage.
"""

from langchain_core.tools import tool

SUBMIT_VERIFICATION_TOOL_NAME = "submit_verification"


@tool
def submit_verification(
    overall: str,
    layer2_status: str,
    layer2_details: str = "",
    primary_evidence_observed: bool = False,
    baseline_used: bool = False,
    checklist: list = None,
    warnings: list = None,
    chosen_candidate: int = 0,
) -> str:
    """Verifier ONLY. Submit the FINAL verification verdict and end verification.

    Call as your LAST action once evidence is sufficient.
    Do NOT also emit a free-text VERIFICATION_RESULT — this structured call
    IS the verdict. Debug-pod cleanup is automatic.

    Inputs:
      - overall: "verified" (effect directly confirmed against baseline) |
          "partial" (injected, effect only partially/indirectly confirmed) |
          "unverified" (could not confirm the fault effect).
      - layer2_status: "passed|failed|partial|skipped|recovered_before_observation"
          (fault-specific effect observable?).
      - layer2_details: one-line evidence summary.
      - primary_evidence_observed: true ONLY if you directly observed the
          fault's PRIMARY effect (not just a side effect); "verified"
          requires this true.
      - baseline_used: compared against the pre-injection baseline.
      - checklist: list of {"step": int, "status":
          "passed|failed|skipped|recovered_before_observation|expected|not_applicable",
          "evidence": str}, one per skill-case step.
      - warnings: optional warning strings.
      - chosen_candidate: chosen candidate index (multi-candidate); 0 otherwise.

    Output: confirmation string (verdict taken from these args).
    """
    return "Verification verdict recorded."


SUBMIT_RECOVER_VERIFICATION_TOOL_NAME = "submit_recover_verification"


@tool
def submit_recover_verification(
    overall: str,
    layer2_status: str,
    layer2_details: str = "",
    baseline_used: bool = False,
    residual_attribution: str = "none",
    checklist: list = None,
    warnings: list = None,
) -> str:
    """Recover verifier ONLY. Submit the FINAL recovery verdict and end
    verification. Call as your LAST action after observing the CURRENT
    post-recovery state — this call IS the verdict (no free-text
    RECOVERY_VERIFICATION_RESULT).

    Inputs:
      - overall: "recovered" | "partial" | "unrecovered"
      - layer2_status: "passed" | "failed" | "partial" | "skipped"
          (passed = fault effect absent; a converging tail does not block it)
      - layer2_details: one-line evidence summary.
      - baseline_used: compared against the pre-injection baseline.
      - residual_attribution: "none" | "recovery_process" | "fault_residual"
          | "mixed" ("recovered" + fault-attributed residuals: downgraded).
      - checklist: [{"step": int, "status": "passed|failed|skipped|partial",
          "evidence": str}], one per verification step.
      - warnings: optional warning strings.

    Output: confirmation string (the verdict is taken from these args).
    """
    return "Recovery verdict recorded."
