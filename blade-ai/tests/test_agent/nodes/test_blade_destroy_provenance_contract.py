"""Contract tests for blade_destroy provenance across compaction (SC1 fix).

``_screen_blade_destroy`` only allows cleaning up an experiment whose UID
is proven by this task's own ``blade_create`` results. The proof source
used to be message history ONLY — but compaction removes old ToolMessages
BY DESIGN, so mid-task the whitelist went empty and the agent could no
longer destroy its OWN injection: the very recovery step failed the
provenance gate.

The fix unions the message scan with ``state["experiment_uid"]`` — the
framework's durable record of the live experiment, maintained by the
execution loop and preserved by the compressed-history restore path. The
gate's semantics are unchanged: every UID admitted is still proven
created by this task, now via either the visible result or the durable
record. These tests pin:

1. The message scan remains the primary record (including failed-create
   CRD UIDs that still need cleanup).
2. The durable record restores provenance once the ToolMessage is
   compacted away.
3. Foreign UIDs stay REJECT_UNKNOWN even with a durable record set —
   the union must not weaken the provenance gate.
4. Empty/whitespace durable records contribute nothing.
5. The two-argument legacy call (no state) keeps its original behaviour.
"""

from langchain_core.messages import ToolMessage

from chaos_agent.agent.nodes.planning.tool_screener import (
    _experiment_uids_created_by_current_task,
    _screen_blade_destroy,
)
from chaos_agent.agent.target_guard import GuardVerdict

OWN_UID = "a1b2c3d4-e5f6-0718-9abc-def012345678"
FAILED_CREATE_UID = "b2c3d4e5-f607-1829-abcd-ef0123456789"
FOREIGN_UID = "ffffffff-0000-0000-0000-000000000000"


def _create_result(uid: str) -> ToolMessage:
    return ToolMessage(
        content=f'{{"code": 200, "success": true, "result": "{uid}"}}',
        name="blade_create",
        tool_call_id=f"call-{uid[:8]}",
    )


class TestMessageScanRemainsPrimary:
    def test_success_create_proves_uid(self):
        uids = _experiment_uids_created_by_current_task([_create_result(OWN_UID)])
        assert uids == {OWN_UID}

    def test_failed_create_crd_uid_still_counted(self):
        # Terminal create failures don't yield an "active" UID via
        # extract_blade_uid, but their CRDs still need cleanup — the
        # screener keeps admitting them.
        msg = ToolMessage(
            content=f'Error: experiment rejected (UID: {FAILED_CREATE_UID})',
            name="blade_create",
            tool_call_id="call-fail",
        )
        uids = _experiment_uids_created_by_current_task([msg])
        assert FAILED_CREATE_UID in uids

    def test_destroy_allowed_from_message_history(self):
        _, decision = _screen_blade_destroy(
            {"uid": OWN_UID}, [_create_result(OWN_UID)],
        )
        assert decision.verdict == GuardVerdict.ALLOW


class TestDurableRecordRestoresProvenance:
    def test_compacted_history_with_durable_uid_allows(self):
        # The blade_create ToolMessage is gone (compacted by design); the
        # framework still records the live experiment in state.
        _, decision = _screen_blade_destroy(
            {"uid": OWN_UID}, [], {"experiment_uid": OWN_UID},
        )
        assert decision.verdict == GuardVerdict.ALLOW

    def test_union_covers_both_sources(self):
        messages = [_create_result(FAILED_CREATE_UID)]
        state = {"experiment_uid": OWN_UID}
        uids = _experiment_uids_created_by_current_task(messages, state)
        assert uids == {OWN_UID, FAILED_CREATE_UID}

    def test_durable_uid_survives_partial_compaction(self):
        # Some history survives, but not the create result — the durable
        # record keeps proving provenance.
        _, decision = _screen_blade_destroy(
            {"uid": OWN_UID},
            [ToolMessage(content="ok", name="kubectl_get", tool_call_id="c")],
            {"experiment_uid": OWN_UID},
        )
        assert decision.verdict == GuardVerdict.ALLOW


class TestGateNotWeakened:
    def test_foreign_uid_rejected_with_durable_record(self):
        _, decision = _screen_blade_destroy(
            {"uid": FOREIGN_UID}, [], {"experiment_uid": OWN_UID},
        )
        assert decision.verdict == GuardVerdict.REJECT_UNKNOWN

    def test_foreign_uid_rejected_without_durable_record(self):
        _, decision = _screen_blade_destroy({"uid": FOREIGN_UID}, [])
        assert decision.verdict == GuardVerdict.REJECT_UNKNOWN

    def test_empty_uid_rejected(self):
        _, decision = _screen_blade_destroy({"uid": "  "}, [], {"experiment_uid": OWN_UID})
        assert decision.verdict == GuardVerdict.REJECT_UNKNOWN


class TestDurableRecordHygiene:
    def test_blank_durable_uid_contributes_nothing(self):
        assert _experiment_uids_created_by_current_task([], {"experiment_uid": ""}) == set()
        assert _experiment_uids_created_by_current_task([], {"experiment_uid": "   "}) == set()

    def test_non_string_durable_uid_is_coerced_safely(self):
        assert _experiment_uids_created_by_current_task([], {"experiment_uid": None}) == set()

    def test_durable_uid_is_stripped(self):
        uids = _experiment_uids_created_by_current_task([], {"experiment_uid": f" {OWN_UID} "})
        assert uids == {OWN_UID}


class TestLegacyCallShape:
    def test_no_state_keeps_original_behaviour(self):
        # Callers that pass no state see exactly the pre-fix semantics.
        _, allowed = _screen_blade_destroy({"uid": OWN_UID}, [_create_result(OWN_UID)])
        assert allowed.verdict == GuardVerdict.ALLOW
        _, rejected = _screen_blade_destroy({"uid": OWN_UID}, [])
        assert rejected.verdict == GuardVerdict.REJECT_UNKNOWN
