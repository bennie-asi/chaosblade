"""Contract tests for the task identity single source of truth.

The prefix split (inject- / recover-) must never break the whitelist:
legacy ``task-`` records keep resolving, new prefixes are minted per
pipeline, and non-task ids stay rejected everywhere persistence guards
run.
"""


class TestIsRealTaskId:
    def test_legacy_prefix_still_accepted(self):
        from chaos_agent.persistence.task_identity import is_real_task_id

        assert is_real_task_id("task-0f1e2d3c")

    def test_new_prefixes_accepted(self):
        from chaos_agent.persistence.task_identity import is_real_task_id

        assert is_real_task_id("inject-0f1e2d3c")
        assert is_real_task_id("recover-0f1e2d3c")

    def test_non_task_ids_rejected(self):
        from chaos_agent.persistence.task_identity import is_real_task_id

        for bad in (None, "", "unknown", "turn-abc123", "chaos-session1", 42):
            assert not is_real_task_id(bad), bad


class TestMinting:
    def test_inject_mint_prefix_and_uniqueness(self):
        from chaos_agent.persistence.task_identity import (
            is_real_task_id,
            new_inject_task_id,
        )

        first, second = new_inject_task_id(), new_inject_task_id()
        assert first.startswith("inject-")
        assert first != second
        assert is_real_task_id(first)

    def test_recover_mint_prefix_and_uniqueness(self):
        from chaos_agent.persistence.task_identity import (
            is_real_task_id,
            new_recover_task_id,
        )

        first, second = new_recover_task_id(), new_recover_task_id()
        assert first.startswith("recover-")
        assert first != second
        assert is_real_task_id(first)

    def test_legacy_alias_mints_inject_flavour(self):
        from chaos_agent.persistence.task_identity import new_task_id

        assert new_task_id().startswith("inject-")


class TestAllocateOperationTaskId:
    def test_operation_selects_prefix(self):
        from chaos_agent.agent.nodes.planning.intent_clarification import (
            _allocate_operation_task_id,
        )

        assert _allocate_operation_task_id("", operation="inject").startswith("inject-")
        assert _allocate_operation_task_id("", operation="recover").startswith("recover-")

    def test_existing_real_id_reused_regardless_of_prefix(self):
        from chaos_agent.agent.nodes.planning.intent_clarification import (
            _allocate_operation_task_id,
        )

        # Legacy ids minted before the split must be reused, never reminted.
        assert _allocate_operation_task_id("task-abc", operation="recover") == "task-abc"
        assert _allocate_operation_task_id("inject-abc", operation="inject") == "inject-abc"
