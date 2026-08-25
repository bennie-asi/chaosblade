"""Contract tests for kubectl-exec delivery detection across compaction (P1 fix).

Recovery routing must know whether the LIVE experiment was created via
``kubectl exec``: a CRD-created experiment is unreachable by the host
``blade`` binary ("record not found"), so the deterministic host
``blade_destroy`` path is wrong for it and the LLM-driven kubectl-exec
recovery flow is the only path that works.

The decision used to be derived from a message scan ONLY
(``scan_kubectl_blade_success``) — but recovery runs LATE in the task and
compaction removes the injection evidence pair (the kubectl-exec
AIMessage + its ChaosBlade-success ToolMessage are among the oldest
messages) BY DESIGN. After compaction the scan went False and a
CRD-created experiment was routed into the deterministic host
blade_destroy, which can never succeed, while the kubectl-exec recovery
instructions were withheld from the LLM flow.

Meanwhile the framework ALREADY keeps a durable record —
``state["injection_method"]`` is committed in the same iteration the
``blade_uid`` appears, is ``durable=True`` and inherited by the recover
graph; the VERIFY side routes on it (``ChaosbladeProvider.layer1_verify``).
The fix (``was_kubectl_exec_delivery``) unions the durable record with
the scan — the same pattern as the blade_destroy provenance fix (SC1) —
so recovery agrees with verification. These tests pin:

1. The durable record survives compaction (the bug's core).
2. The message scan remains the state-less fallback (legacy sessions).
3. Absence of both proofs → False (no false kubectl-exec routing).
4. Other methods never leak into the decision.
5. The scan primitive itself loses its evidence under compaction (the
   root cause this union exists to absorb).
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chaos_agent.agent.providers.chaosblade.verify import (
    was_kubectl_exec_delivery,
)
from chaos_agent.agent.providers.chaosblade.verify import scan_kubectl_blade_success

UID = "a1b2c3d4e5f60718"


def _kubectl_exec_evidence_pair() -> tuple[AIMessage, ToolMessage]:
    """The injection evidence pair the scan keys on."""
    ai = AIMessage(
        content="",
        tool_calls=[{
            "name": "kubectl",
            "args": {
                "subcommand": "exec",
                "v_args": "otel-c-tool-x1 -n chaosblade -- blade create cpu fullload",
                "kubeconfig": "/tmp/kc",
            },
            "id": "tc-1",
            "type": "tool_call",
        }],
    )
    tool = ToolMessage(
        content=f'{{"code":200,"success":true,"result":"{UID}"}}',
        tool_call_id="tc-1",
        name="kubectl",
    )
    return ai, tool


def _post_compaction_tail() -> list:
    """What check_context keeps: recent messages + the newest summary."""
    return [
        HumanMessage(content="continue verification"),
        AIMessage(content="cpu load observed at 82%"),
        HumanMessage(content="[Compressed History] injection happened ..."),
    ]


class TestDurableRecordSurvivesCompaction:
    def test_method_record_true_after_evidence_compacted(self):
        """The bug's core: durable record present, scan evidence gone."""
        state = {
            "injection_method": "kubectl_exec",
            "messages": _post_compaction_tail(),
        }
        assert was_kubectl_exec_delivery(state) is True

    def test_method_record_true_with_empty_messages(self):
        state = {"injection_method": "kubectl_exec", "messages": []}
        assert was_kubectl_exec_delivery(state) is True


class TestMessageScanFallback:
    def test_stateless_full_history(self):
        """Legacy / restored sessions without the method recorded."""
        ai, tool = _kubectl_exec_evidence_pair()
        state = {"messages": [ai, tool, *_post_compaction_tail()]}
        assert was_kubectl_exec_delivery(state) is True

    def test_stateless_missing_messages_key(self):
        assert was_kubectl_exec_delivery({}) is False

    def test_stateless_none_messages(self):
        assert was_kubectl_exec_delivery({"messages": None}) is False

    def test_explicit_messages_override(self):
        """Provider recover() passes the history through kwargs, not state."""
        ai, tool = _kubectl_exec_evidence_pair()
        assert was_kubectl_exec_delivery({}, [ai, tool]) is True
        # The override REPLACES state messages: empty override with a
        # state list that has no evidence still scans the override.
        assert was_kubectl_exec_delivery(
            {"messages": _post_compaction_tail()}, [],
        ) is False


class TestNoFalsePositive:
    def test_no_evidence_anywhere(self):
        state = {"messages": _post_compaction_tail()}
        assert was_kubectl_exec_delivery(state) is False

    def test_other_method_does_not_leak(self):
        """host_blade + compacted history must NOT route kubectl-exec."""
        state = {
            "injection_method": "host_blade",
            "messages": _post_compaction_tail(),
        }
        assert was_kubectl_exec_delivery(state) is False

    def test_native_method_does_not_leak(self):
        ai, tool = _kubectl_exec_evidence_pair()
        state = {"injection_method": "kubectl_native", "messages": [ai, tool]}
        # The scan still sees the evidence — native methods never carry a
        # blade experiment, but the union's second branch is the scan, so
        # the contract pins WHAT that means: if the history genuinely holds
        # a kubectl-exec blade success, the delivery WAS kubectl exec
        # regardless of a provisional native label (UPGRADE semantics).
        assert was_kubectl_exec_delivery(state) is True


class TestScanPrimitiveRootCause:
    def test_scan_true_before_compaction(self):
        ai, tool = _kubectl_exec_evidence_pair()
        assert scan_kubectl_blade_success([ai, tool, *_post_compaction_tail()]) is True

    def test_scan_false_after_compaction(self):
        """Pins why the union exists: the scan is not durable."""
        assert scan_kubectl_blade_success(_post_compaction_tail()) is False
