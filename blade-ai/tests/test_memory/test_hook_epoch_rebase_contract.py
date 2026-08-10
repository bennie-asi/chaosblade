"""Contract tests for epoch-boundary re-basing across compaction (G1 fix).

``attribution_epoch_index`` is an ABSOLUTE index into ``messages``
(state_lifecycle contract: "must stay aligned with the message list it
indexes"). The memory hook's compaction return is the only in-task
mutation of that list, and it used to leave the boundary untouched —
every removed message shifted the epoch window left:

  * the RESUME/UPGRADE injection re-scan (``_detect_injection_method``)
    and the replan attempt audit (``_injection_attempted_this_contract``)
    would read the wrong slice and under-attribute the current epoch's
    attempts;
  * when compaction removed MORE messages than the boundary value, the
    window silently fell back to full history and pre-epoch attempts
    leaked back in (the task-5193538b failure shape).

The fix makes the mutator own the re-base: ``_rebase_epoch_index`` counts
how many pre-boundary messages SURVIVE (``to_keep`` is not a clean suffix
— ``check_context`` retains the newest [Compressed History] summaries
wherever they sit), and the compaction return carries the new boundary.

These tests pin:

1. The re-base value is "survivors before the boundary", not "subtract a
   prefix length" (non-clean-suffix shapes included).
2. No-op cases: no boundary / zero / malformed; compaction entirely after
   the boundary; id-less messages (which never get a RemoveMessage).
3. Full pre-boundary compaction collapses the boundary to 0, which the
   consumer treats as "no boundary → full history" — correct, because no
   pre-boundary message survives.
4. End-to-end window equivalence: the epoch window after compaction +
   re-base covers exactly the surviving current-epoch messages.
"""

from langchain_core.messages import HumanMessage

from chaos_agent.agent.nodes.execute.execute_loop import _epoch_bounded_messages
from chaos_agent.memory.hook import _rebase_epoch_index


def _msgs(n: int, offset: int = 0) -> list:
    return [HumanMessage(content=f"m{i + offset}", id=f"msg-{i + offset}") for i in range(n)]


def _window(messages: list, boundary) -> list[str]:
    return [m.content for m in _epoch_bounded_messages(
        messages, {"attribution_epoch_index": boundary},
    )]


class TestRebaseValue:
    def test_prefix_compaction_shifts_boundary(self):
        msgs = _msgs(10)
        # boundary=5: epoch = m5..m9. Compaction removes m0..m3 → m4 survives
        # before the boundary, so the new boundary is 1.
        assert _rebase_epoch_index(msgs, msgs[0:4], 5) == 1

    def test_non_clean_suffix_with_retained_summary_in_compact(self):
        # check_context keeps the newest summaries verbatim and drops older
        # ones wherever they sit, so to_compact is NOT a prefix. Interleave
        # a kept message inside the compacted region: m0 m1 [kept-summary]
        # m3 m4 | m5..m9, with to_compact = {m0, m1, m3}.
        msgs = _msgs(10)
        to_compact = [msgs[0], msgs[1], msgs[3]]
        # Survivors before boundary=5: m2, m4 → 2.
        assert _rebase_epoch_index(msgs, to_compact, 5) == 2

    def test_boundary_inside_compacted_region(self):
        msgs = _msgs(10)
        # Compaction removes m0..m6; boundary=3 sits inside the removed
        # region → zero survivors before it.
        assert _rebase_epoch_index(msgs, msgs[0:7], 3) == 0

    def test_all_pre_boundary_messages_compacted(self):
        msgs = _msgs(10)
        assert _rebase_epoch_index(msgs, msgs[0:5], 5) == 0


class TestNoOpCases:
    def test_no_boundary(self):
        msgs = _msgs(10)
        assert _rebase_epoch_index(msgs, msgs[0:4], None) is None
        assert _rebase_epoch_index(msgs, msgs[0:4], 0) is None

    def test_malformed_boundary(self):
        msgs = _msgs(10)
        assert _rebase_epoch_index(msgs, msgs[0:4], "not-a-number") is None

    def test_compaction_entirely_after_boundary(self):
        msgs = _msgs(10)
        # Removing only post-boundary messages leaves the boundary valid.
        assert _rebase_epoch_index(msgs, msgs[6:], 5) is None

    def test_empty_compaction(self):
        msgs = _msgs(10)
        assert _rebase_epoch_index(msgs, [], 5) is None

    def test_id_less_messages_always_survive(self):
        # The hook emits RemoveMessage only for messages WITH an id, so an
        # id-less message in the compacted set is never actually removed
        # and must keep counting toward the boundary.
        no_id = [HumanMessage(content=f"x{i}") for i in range(6)]
        assert all(m.id is None for m in no_id)
        assert _rebase_epoch_index(no_id, no_id[:3], 5) is None
        # Mixed: only the id-carrying prefix actually disappears.
        mixed = [
            HumanMessage(content="a", id="has-id-1"),
            HumanMessage(content="b"),  # id-less → survives
            HumanMessage(content="c", id="has-id-2"),
            HumanMessage(content="d", id="has-id-3"),
        ]
        # Compaction claims all four, but only three actually go; survivors
        # before boundary=4: the id-less "b" → 1.
        assert _rebase_epoch_index(mixed, mixed, 4) == 1


class TestWindowEquivalence:
    def test_window_stable_across_compaction(self):
        """The epoch window after compaction + re-base must cover exactly
        the surviving current-epoch messages — the invariant the RESUME
        re-scan and the attempt audit depend on."""
        msgs = _msgs(10)
        boundary = 5
        window_before = _window(msgs, boundary)
        assert window_before == ["m5", "m6", "m7", "m8", "m9"]

        to_compact = msgs[0:4]
        survivors = msgs[4:]  # LangGraph add_messages order preserved
        summary = HumanMessage(content="[Compressed History] ...", id="summary")
        post = survivors + [summary]

        new_boundary = _rebase_epoch_index(msgs, to_compact, boundary)
        assert new_boundary == 1
        window_after = _window(post, new_boundary)
        assert window_after == ["m5", "m6", "m7", "m8", "m9",
                                "[Compressed History] ..."]

    def test_full_pre_boundary_compaction_falls_back_to_history(self):
        """Boundary collapses to 0 → consumer reads full history. Correct:
        no pre-boundary message survives, so everything visible belongs to
        (or postdates) the current epoch."""
        msgs = _msgs(10)
        to_compact = msgs[0:5]
        post = msgs[5:]
        new_boundary = _rebase_epoch_index(msgs, to_compact, 5)
        assert new_boundary == 0
        assert _window(post, new_boundary) == [f"m{i}" for i in range(5, 10)]
