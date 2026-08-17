"""Canonical wording for the settled-case handoff (``case_resource_path``).

One authoritative phrasing, shared by every LLM-facing surface that hands the
case settled in the intent dialogue over to planning (the Reviewed FaultSpec
contract section). Wording discipline:

- Empty path → empty note. The note must never promise a file that does not
  exist — no "start from it" without a path.
- No directory-layout assumptions: skills are not required to organise their
  resources in any particular tree, so the note never names a path. How to
  browse is the skill's own SKILL.md discovery flow and the
  ``read_skill_resource`` docstring's job.
- Reference semantics: the settled case is a starting point, not a binding.
  Planning keeps final selection authority and must note any deviation.
- No positional words ("above"/"below"), so the note reads correctly wherever
  it is inserted.
"""


def get_case_reference_note(case_resource_path: str) -> str:
    """Return the canonical case-handoff note, or ``""`` when no path was
    settled in the intent dialogue."""
    path = (case_resource_path or "").strip()
    if not path:
        return ""
    return (
        "`case_resource_path` is the case file settled in the intent "
        "dialogue — a path relative to the skill directory, exactly what "
        "`read_skill_resource` consumes. It is a reference, not a "
        "directive: start from it, but the final case selection is yours. "
        "If runtime evidence shows it unviable here or another case fits "
        "better, browse the active skill with `read_skill_resource`, choose "
        "accordingly, and note the reason in your plan."
    )
