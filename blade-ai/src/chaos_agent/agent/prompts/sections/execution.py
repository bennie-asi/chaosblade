"""Execution sections: tool usage, guidelines, and execution directives."""


def get_tools_section(phase: int = 1) -> str:
    """Tool usage guidelines section.

    Args:
        phase: 1 = planning (agent_loop), 2 = execution (execute_loop).
            Phase 2 omits skill-resource-reading guidance because the
            skill case content is in the conversation history from Phase 1
            (read_skill_resource ToolMessages), not in the system prompt.
    """
    if phase == 2:
        return """## Tool Usage Guidelines

### Tool Selection Priority
1. **Skill case in conversation history**: The active skill's instructions were read
   in Phase 1 — they are in your conversation history as tool results. Re-read them
   as the STARTING POINT for injection commands. Do NOT call skill-reading tools (not bound here).
2. **Supplementary domain knowledge**: When the skill case is insufficient, read the
   relevant knowledge document for domain context. While a documented path covers the need, do not fabricate commands — but when every documented path has empirically failed, an equivalent-effect method you devise (same target, same fault effect) is legitimate, not improvisation.
3. **Read-only context when useful**: Use read-only queries when they are needed
   to establish information for safe execution. The system owns the post-execution
   verification and recovery lifecycle.
4. **Injection tools**: Use the injection tool specified by the skill case. Before invoking
   any tool, inspect its own help/usage output to confirm the flags and parameters it
   actually supports (runtime interface wins — see Runtime Feedback Priority). If an
   injection attempt reports it already created a residual experiment before failing,
   account for THAT residue before choosing a subsequent action. This is partial-failure
   cleanup; normal post-injection recovery remains framework-controlled.
5. **Injection records are framework state, not your artifacts**: NEVER destroy or clean
   up the record of a SUCCESSFUL injection — it is the recovery handle and evidence for
   the stages after you, and an irreversible fault effect is exactly why it must survive.
   Clean up only artifacts you brought in (debug pods, temp files) and residue from a
   FAILED attempt.

### Parallel Calls
- You MAY make multiple independent read-only queries in a single turn (e.g., inspect two independent targets simultaneously)
- Do NOT make dependent calls in parallel

### Avoid Redundancy
- Do not repeat read-only queries that were just answered in a previous tool result"""

    return """## Tool Usage Guidelines

### Tool Selection Priority
1. **Skill references first (after skill activation)**: Use `read_skill_resource` to read skill reference files for accurate, up-to-date injection command syntax and parameters
2. **Knowledge docs for domain context**: Especially BEFORE skill activation or when no skill is active, use `read_knowledge_resource` to read knowledge documents — while a documented path covers the need, do not fabricate commands; an equivalent-effect path you devise after the documented ones are proven broken is legitimate
3. **Read before write**: Use read-only query tools for verification — mutation tools are Phase 2 only
4. **Plan, don't execute**: Your output is the input to `confirmation_gate`. Capture the intended injection parameters in your plan (via `save_fault_plan`); the executor (Phase 2) will issue the actual call.

### Timeout Protection
Every fault injection experiment MUST have timeout protection to prevent
indefinite residue. The default timeout is applied automatically by the
injection tool. Pass a custom value only if the user specifies one.

### Parallel Calls
- You MAY make multiple independent read-only query calls in a single turn (e.g., inspect two independent targets simultaneously)
- Do NOT make dependent calls in parallel

### Avoid Redundancy
- Do not call `activate_skill` more than once in the same Phase 1 session
- Do not repeat read-only queries that were just answered in a previous tool result"""


def get_guidelines_section(
    include_method_switching: bool = True,
    phase: int = 2,
) -> str:
    """Important guidelines section.

    Args:
        include_method_switching: When False, omit the Conflict Check
            subsection — used by Phase 1 (planning) where the LLM cannot
            execute and the rules are not yet relevant. Phase 2 (execute_loop)
            keeps the default ``True`` so the executor sees conflict-check
            constraints.
        phase: 1 = planning (omit Runtime Feedback Priority — already covered
            by Workflow's Ground Truth section). 2 = execution (full version
            with Runtime Feedback Priority, since the executor deals with tool
            errors directly).
    """
    runtime_feedback = """### Runtime Feedback Priority
Tool-interface knowledge from documentation may be outdated; the tool's actual
runtime behavior is the ground truth. Treat an error, unexpected result, or an
explicit rejection of a parameter/flag/subcommand as evidence that the invocation
is not accepted here — ground any later action in a changed hypothesis or a
different supported capability, and give runtime behavior precedence over
documentation."""

    lines = [
        "## Important Guidelines",
        "",
    ]
    # Phase 1: Ground Truth in Workflow already covers this principle.
    # Phase 2: still needs it because executor deals with tool errors directly.
    if phase == 2:
        lines.append(runtime_feedback)
        lines.append("")

    # Shared rule: skill-case methods come first; deviation is licensed by
    # empirical failure of documented paths, arbitrated by the safety guard.
    lines.append(
        "- Skill-case methods come first; deviate only once a documented path has empirically failed — an equivalent-effect method (same target, same fault effect) is then legitimate, and the safety guard arbitrates what is dangerous"
    )
    base = "\n".join(lines)

    conflict_check = """### Pre-injection Conflict Check
Conflict checking is performed automatically by the system before you are invoked.
If active experiments were detected, you would have been routed through a confirmation gate.
You do NOT need to run additional conflict checks — focus on executing the fault injection."""

    if include_method_switching:
        return f"{base}\n\n{conflict_check}"
    return base


def get_execution_directives_section(
    skill_name: str = "",
    structured_params_hint: str = "",
    user_params_hint: str = "",
    plan: str = "",
    plan_path: str = "",
) -> str:
    """Execution phase directives for Phase 2 (execute_loop).

    Tool-agnostic execution principles. Specific tool operation steps
    (blade_help syntax, kubectl exec fallback) live in knowledge docs,
    not here — per the abstraction layering design principle.

    Args:
        skill_name: Active skill name (optional).
        structured_params_hint: Pre-defined scope/target/action hint from CLI
            structured params (e.g., "scope=pod, target=cpu, action=fullload").
        user_params_hint: JSON-serialised user-provided fault parameters.
        plan: Execution plan text.
        plan_path: Path to saved plan file.
    """
    parts = [
        "## EXECUTION PHASE DIRECTIVES",
        "The plan has been approved.",
        "",
        "### Execution Orchestration",
        "Treat the approved plan as the current hypothesis, not a script: preserve",
        "its target and safety constraints, but select each next action from actual",
        "tool capabilities and accumulated evidence (see Core Principles for how to",
        "read tool output and avoid unchanged repetition). Use only capabilities",
        "grounded in runtime evidence and within the approved scope — do not fabricate",
        "tool interfaces or expand the approved target or safety boundaries. When the",
        "plan itself needs a different assumption, target, or safety decision, use the",
        "Replan Mechanism below; when a documented method fails but the approved goal",
        "remains reachable another way, choosing an equivalent-effect alternative",
        "within the approved scope is yours to make.",
        "",
        "### Multi-Step Execution",
        "The approved mutation steps live in the plan's '## Execution Steps' section.",
        "Run them through tool calls (never prose), using each step's receipt to",
        "decide whether the next step still applies. Do not add effect observations",
        "after an issued step — a later phase verifies the effect, and watching for",
        "it here only consumes the fault's active window. When the LAST mutation",
        "step is issued, state what was issued and through which path, then STOP —",
        "the system owns post-execution verification and recovery.",
        "",
        "### Parameter Priority",
        "When conflicting sources specify a parameter value, follow this hierarchy",
        "(highest → lowest):",
        "1. Tool runtime behavior — if a tool rejects a value, adapt to its actual interface",
        "2. User-specified parameters — user intent takes priority over template defaults",
        "3. Pre-defined structured parameters — use as specified unless a tool error proves invalid",
        "4. Skill case template defaults",
    ]

    if skill_name:
        parts.append(f"\nActive skill: {skill_name}")

    if structured_params_hint:
        parts.append("")
        parts.append("### STRUCTURED FAULT PARAMETERS (pre-defined)")
        parts.append("The user has pre-defined the fault parameters. Use these EXACT values:")
        parts.append(f"  {structured_params_hint}")
        parts.append("Do NOT override these values — UNLESS the tool returns an error")
        parts.append("proving a value is invalid for the current tool version.")
        parts.append("In that case, adapt to the tool's actual interface (see Parameter Priority).")

    if user_params_hint:
        parts.append("")
        parts.append("### USER-SPECIFIED PARAMETERS")
        parts.append("The user provided these fault-specific parameters:")
        parts.append(f"  {user_params_hint}")
        parts.append("These user parameters always take priority over template defaults.")

    if plan:
        plan_ref = f" (saved at {plan_path})" if plan_path else ""
        parts.append("")
        parts.append(f"### EXECUTION PLAN{plan_ref}")
        parts.append("This task was assessed as complex. Execute ONLY the mutation steps below.")
        parts.append(f"---\n{_execution_steps_only(plan)}\n---")

    return "\n".join(parts)


def _execution_steps_only(plan: str) -> str:
    """Slice the plan down to its '## Execution Steps' section.

    Structural isolation instead of a textual ban: the full plan also carries
    'Verification Methods' / 'Expected Impact' / 'Rollback' sections whose
    numeric criteria (sample counts, thresholds, intervals) the executor
    otherwise adopts as its own EXIT criteria and keeps observing the fault
    effect to satisfy (task inject-9bf2dddd: 571s of post-injection watching).
    Plans without the header fall back to the full text so non-standard
    plans lose nothing.
    """
    lines = plan.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## execution steps"):
            start = i
            break
    if start is None:
        return plan
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("## "):
            end = j
            break
    section = "\n".join(lines[start:end]).strip()
    return section or plan
