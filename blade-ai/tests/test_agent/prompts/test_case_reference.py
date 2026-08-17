"""Tests for the canonical case-handoff note (single source of truth).

The note is the ONE authoritative phrasing for handing the case settled in
the intent dialogue over to planning. These tests freeze its wording
discipline so no consumer can drift back into the historical failure modes:

* promising a case file when no path was settled;
* assuming a skill directory layout (e.g. ``references/catalogue/``);
* using positional words that break when the note moves between prompts.
"""

from chaos_agent.agent.prompts.sections.case_reference import (
    get_case_reference_note,
)


class TestGetCaseReferenceNote:
    def test_empty_path_yields_empty_note(self):
        # No case settled in the dialogue → no note at all: telling planning
        # to "start from it" with no path would be self-contradictory.
        assert get_case_reference_note("") == ""
        assert get_case_reference_note("   ") == ""

    def test_note_carries_reference_semantics(self):
        note = get_case_reference_note("a/b/c.md")
        assert "a reference, not a directive" in note
        assert "the final case selection is yours" in note
        assert "note the reason in your plan" in note

    def test_note_states_how_to_read_the_path(self):
        note = get_case_reference_note("a/b/c.md")
        assert "`read_skill_resource` consumes" in note
        assert "browse the active skill with `read_skill_resource`" in note

    def test_note_makes_no_directory_layout_assumptions(self):
        note = get_case_reference_note("a/b/c.md")
        lowered = note.lower()
        # No built-in skill tree leaked into the canonical wording — skills
        # may organise their resources however their SKILL.md describes.
        assert "catalogue" not in lowered
        assert "references/" not in lowered

    def test_note_uses_no_positional_words(self):
        note = get_case_reference_note("a/b/c.md")
        lowered = note.lower()
        # Must read correctly wherever it is inserted (system prompt section
        # or a stand-alone reminder) — never "the ... above/below".
        assert "above" not in lowered
        assert "below" not in lowered
