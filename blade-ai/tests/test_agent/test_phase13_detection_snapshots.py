"""Phase-13 detection-domain snapshots (fault-handle-phase13-detection-import-retirement, T1).

快照先行钉扎「改道前」行为：本期 T3/T4 将把通用层四处 chaosblade
detection/verify 直连（``execute_loop.scan_destroyed_uids`` /
``task_snapshot`` 的 verify+detection 双路径）改道为 registry 接缝。
本文件全部直接调改道前的权威函数——它们是改道后的对照组：

* ``TestDestroyedScanSnapshots``（tasks 1.1）——
  ``detection.scan_destroyed_uids`` 对三组消息集的精确集合。
* ``TestSessionUidRecoverySnapshots``（tasks 1.2）——
  ``task_snapshot._extract_experiment_uid_from_session`` 三形态 +
  ``prefer_messages`` 两分支 + 混合 fixture（同 session 含 OS 实验 +
  python 实验证据——Open Question 1 的裁定依据：改道后逐 provider 首
  claim 必须逐值复现这些钉扎值）。
* ``TestReplanSeamDeathFilterSnapshots``（tasks 1.3）—— replan seam 的
  fallback 死亡过滤三场景（已被 destroy / 已 retired / 存活——
  task-349ccf5d 修复路径，keep 语义不得回归）。

改道完成后本文件不变（对照组就是改道前行为）；T3/T5 的「集合相等 /
逐值相等」断言通过与这里的钉扎值对比成立。
"""

import json

from langchain_core.messages import AIMessage, ToolMessage

# Real UUID shapes (8-4-4-4-12 hex) — the extractors' regex contract.
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"
OS_UID = "aaaaaaaa-1111-4222-8333-444444444444"
PY_UID = "bbbbbbbb-1111-4222-8333-444444444444"


def _create_success_content(uid: str) -> str:
    return json.dumps({"code": 200, "success": True, "result": uid})


def _blade_destroy_message(uid: str, tc_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "blade_destroy",
                "args": {"uid": uid},
                "id": tc_id,
                "type": "tool_call",
            }
        ],
    )


class TestDestroyedScanSnapshots:
    """tasks 1.1 —— ``scan_destroyed_uids`` 集合快照（T3 改道对照组）。

    T3 后 ``execute_loop`` 改调 ``FaultProviderRegistry.destroyed_experiment_ids``
    （逐 UID-bearing provider union 聚合）。对本组消息集，接缝返回值必须
    与这里的钉扎集合相等。
    """

    def test_destroy_tool_calls_collected(self):
        from chaos_agent.agent.providers.chaosblade.verify import (
            scan_destroyed_uids,
        )

        messages = [
            ToolMessage(
                content=_create_success_content(UID_A),
                tool_call_id="tc_create",
                name="blade_create",
            ),
            _blade_destroy_message(UID_A, "tc_destroy"),
        ]
        assert scan_destroyed_uids(messages) == {UID_A}

    def test_no_destroy_calls_yields_empty_set(self):
        from chaos_agent.agent.providers.chaosblade.verify import (
            scan_destroyed_uids,
        )

        messages = [
            AIMessage(content="thinking"),
            ToolMessage(
                content=_create_success_content(UID_B),
                tool_call_id="tc_create",
                name="blade_create",
            ),
        ]
        # blade_create / status 等非 destroy 的 tool_calls 不算销毁。
        assert scan_destroyed_uids(messages) == set()

    def test_multiple_uids_deduplicated(self):
        from chaos_agent.agent.providers.chaosblade.verify import (
            scan_destroyed_uids,
        )

        messages = [
            _blade_destroy_message(UID_A, "tc_d1"),
            _blade_destroy_message(UID_B, "tc_d2"),
            _blade_destroy_message(UID_A, "tc_d3"),
        ]
        # 集合语义：重复销毁同一 UID 去重。
        assert scan_destroyed_uids(messages) == {UID_A, UID_B}


class TestSessionUidRecoverySnapshots:
    """tasks 1.2 —— session UID 恢复三形态快照（T4 改道对照组）。

    T4 后 ``task_snapshot._extract_experiment_uid_from_session`` 的
    ``uid_from_messages`` 内部改为一行 registry 接缝调用
    （``recover_experiment_uid_from_session``）。对本组 session fixture，
    改道后返回值必须与这里的钉扎值逐值相等。
    """

    def test_langchain_convertible_messages_hit(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        session = {
            "messages": [
                {
                    "type": "tool",
                    "name": "blade_create",
                    "content": _create_success_content(UID_A),
                    "tool_call_id": "tc_create",
                }
            ]
        }
        # langchain 转换路径命中（ToolMessage name=blade_create，
        # verify 全家桶 Priority 1）。
        assert _extract_experiment_uid_from_session(session) == UID_A

    def test_dict_only_messages_fallback_hit(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        session = {
            "messages": [
                {
                    "type": "tool_execution",
                    "detail": {
                        "command": "blade create k8s pod-cpu fullload --names demo-pod",
                        "stdout_preview": _create_success_content(UID_B),
                    },
                }
            ]
        }
        # langchain 转换后 name 为空（detail.source 缺失），全家桶不认；
        # dict 原文 fallback 命中（command 含 blade+create 词汇判定 +
        # stdout_preview 文本提取）——正是要归位载体侧的 L267 词汇路径。
        assert _extract_experiment_uid_from_session(session) == UID_B

    def test_no_messages_returns_empty(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        assert _extract_experiment_uid_from_session({}) == ""
        assert _extract_experiment_uid_from_session(None) == ""

    def test_result_summary_priority_over_messages(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        session = {
            "result_summary": json.dumps({"data": {"experiment_uid": UID_A}}),
            "messages": [
                {
                    "type": "tool",
                    "name": "blade_create",
                    "content": _create_success_content(UID_B),
                    "tool_call_id": "tc_create",
                }
            ],
        }
        # 默认（prefer_messages=False）：durable result_summary 优先。
        assert _extract_experiment_uid_from_session(session) == UID_A
        # prefer_messages=True（increment-log 会话）：消息证据优先，
        # result_summary 只作兜底。
        assert (
            _extract_experiment_uid_from_session(session, prefer_messages=True)
            == UID_B
        )

    def test_mixed_os_and_python_evidence_python_last(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        session = {
            "messages": [
                {
                    "type": "tool",
                    "name": "blade_create",
                    "content": _create_success_content(OS_UID),
                    "tool_call_id": "tc_os",
                },
                {
                    "type": "tool",
                    "name": "blade_python_create",
                    "content": _create_success_content(PY_UID),
                    "tool_call_id": "tc_py",
                },
            ]
        }
        # Open Question 1 的裁定 fixture：改前全家桶（verify.
        # extract_experiment_uid_from_messages）reversed 扫描，Priority 1
        # 对 blade_create / blade_python_create 一视同仁——最靠后的证据
        # 胜出。此处 python 证据靠后 → PY_UID。
        assert _extract_experiment_uid_from_session(session) == PY_UID

    def test_mixed_os_and_python_evidence_os_last(self):
        from chaos_agent.agent.result.task_snapshot import (
            _extract_experiment_uid_from_session,
        )

        session = {
            "messages": [
                {
                    "type": "tool",
                    "name": "blade_python_create",
                    "content": _create_success_content(PY_UID),
                    "tool_call_id": "tc_py",
                },
                {
                    "type": "tool",
                    "name": "blade_create",
                    "content": _create_success_content(OS_UID),
                    "tool_call_id": "tc_os",
                },
            ]
        }
        # 反向混合：OS 证据靠后 → OS_UID。「最靠后者胜出」语义对两个
        # 方向都必须成立（T4 改道后逐 provider 首 claim 的等价性裁定）。
        assert _extract_experiment_uid_from_session(session) == OS_UID


class TestReplanSeamDeathFilterSnapshots:
    """tasks 1.3 —— replan seam 死亡过滤三场景（T3 改道对照组）。

    task-349ccf5d 修复路径：compression-boundary fallback uid 必须通过与
    live 提取相同的死亡过滤（destroy / retired）。T3 改道
    （``scan_destroyed_uids`` → registry 接缝）后，``experiment_uid_at_seam``
    的 keep 语义必须与本组钉扎一致。
    """

    @staticmethod
    def _fire(state_messages: list, **state_extra) -> tuple[dict, dict]:
        from chaos_agent.agent.nodes.execute.execute_loop import _fire_replan_seam
        from chaos_agent.agent.replan import ReplanRequest

        state = {"messages": list(state_messages), "replan_count": 0}
        state.update(state_extra)
        result: dict = {}
        replan_context: dict = {}
        _fire_replan_seam(
            state,
            result,
            ReplanRequest(
                kind="feasibility",
                decision="plan_invalid",
                invalidated_assumption="assumption under test",
                affected_step="step-1",
            ),
            replan_context,
        )
        return result, replan_context

    def test_fallback_uid_destroyed_is_dropped(self):
        result, replan_context = self._fire(
            [_blade_destroy_message(UID_A, "tc_destroy")],
            experiment_uid=UID_A,
        )
        # 持久化 uid 已被 blade_destroy → fallback 清空，seam 无存活 uid。
        assert replan_context["existing_experiment_uids"] == []
        assert result["replan_history"][-1]["experiment_uid_at_seam"] is None

    def test_fallback_uid_retired_is_dropped(self):
        result, replan_context = self._fire(
            [],
            experiment_uid=UID_A,
            retired_experiment_uids=[UID_A],
        )
        # 持久化 uid 已被框架侧 retired（无 blade_destroy ToolMessage）
        # → 死亡过滤的第二来源。
        assert replan_context["existing_experiment_uids"] == []
        assert result["replan_history"][-1]["experiment_uid_at_seam"] is None

    def test_fallback_uid_alive_is_kept(self):
        # compression-boundary 场景：create ToolMessage 已被摘要掉，仅剩
        # 持久化 uid，且未 destroy / 未 retired → keep。
        result, replan_context = self._fire(
            [],
            experiment_uid=UID_A,
        )
        assert replan_context["existing_experiment_uids"] == [UID_A]
        assert result["replan_history"][-1]["experiment_uid_at_seam"] == UID_A

    def test_live_evidence_uid_is_kept(self):
        # live 提取路径：create ToolMessage 在场且未销毁 → seam 直取
        # live uid（持久化字段为空）。
        result, replan_context = self._fire(
            [
                ToolMessage(
                    content=_create_success_content(UID_B),
                    tool_call_id="tc_create",
                    name="blade_create",
                )
            ],
        )
        assert replan_context["existing_experiment_uids"] == [UID_B]
        assert result["replan_history"][-1]["experiment_uid_at_seam"] == UID_B

    def test_live_uid_destroyed_falls_back_to_nothing(self):
        # live 证据存在但已被销毁（destroy 在 create 之后），持久化字段
        # 为空 → seam 无 uid。
        result, replan_context = self._fire(
            [
                ToolMessage(
                    content=_create_success_content(UID_B),
                    tool_call_id="tc_create",
                    name="blade_create",
                ),
                _blade_destroy_message(UID_B, "tc_destroy"),
            ],
        )
        assert replan_context["existing_experiment_uids"] == []
        assert result["replan_history"][-1]["experiment_uid_at_seam"] is None
