"""Tests for _verifier_finalize.py — finalize verification pure functions."""

from unittest.mock import AsyncMock, patch

import pytest

from chaos_agent.agent.nodes.verify._verifier_finalize import (
    _overall_to_level,
    _verification_from_submit_args,
    _format_verification_detail,
    _build_verify_replan_context,
    _cleanup_residuals,
    _retired_uids_from_residuals,
    _verify_replan_eligible,
    _apply_step_coverage,
    _enforce_disk_burn_facts,
)
from chaos_agent.agent.result.verdict import Layer1Result


class TestVerifyReplanEligible:
    """The verify-replan verdict gate, incl. the direct-mode exclusion.

    Direct runs carry the user-specified injection verbatim and skip
    Phase 1, so re-planning would substitute a different injection for
    the one ordered — the verdict must finalize as-is instead.
    """

    @staticmethod
    def _verification(level="unverified", l2="failed"):
        return {"level": level, "layer2": {"status": l2}}

    def test_unverified_l2_failed_is_eligible(self):
        assert _verify_replan_eligible({}, self._verification()) is True

    def test_direct_mode_is_excluded(self):
        assert _verify_replan_eligible(
            {"direct": True}, self._verification()
        ) is False

    def test_verified_level_is_not_eligible(self):
        assert _verify_replan_eligible(
            {}, self._verification(level="verified", l2="passed")
        ) is False

    def test_l2_not_failed_is_not_eligible(self):
        assert _verify_replan_eligible(
            {}, self._verification(l2="partial")
        ) is False


class TestOverallToLevel:
    @pytest.mark.parametrize("overall, expected", [
        ("verified", "verified"),
        ("partial", "partial"),
        ("unverified", "unverified"),
        ("garbage", "unverified"),
        ("", "unverified"),
    ])
    def test_mapping(self, overall, expected):
        assert _overall_to_level(overall) == expected


class TestVerificationFromSubmitArgs:
    def test_basic_verified(self):
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "layer2_details": "CPU confirmed at 95%",
            "primary_evidence_observed": True,
            "baseline_used": True,
        }
        result = _verification_from_submit_args(args)
        assert result["level"] == "verified"
        assert result["layer2"]["status"] == "passed"
        assert result["layer2"]["details"] == "CPU confirmed at 95%"
        assert result["primary_evidence_observed"] is True
        assert result["baseline_used"] is True

    def test_primary_evidence_false_downgrades(self):
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "primary_evidence_observed": False,
        }
        result = _verification_from_submit_args(args)
        assert result["level"] == "partial"
        assert any("PrimaryEvidenceObserved" in w for w in result["warnings"])

    def test_layer2_failed_blocks_verified(self):
        args = {
            "overall": "verified",
            "layer2_status": "failed",
            "primary_evidence_observed": True,
        }
        result = _verification_from_submit_args(args)
        assert result["level"] == "unverified"
        assert any("Layer2='failed'" in w for w in result["warnings"])

    def test_layer2_partial_forces_partial(self):
        args = {
            "overall": "verified",
            "layer2_status": "partial",
        }
        result = _verification_from_submit_args(args)
        assert result["level"] == "partial"

    def test_checklist_with_inconsistency(self):
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "primary_evidence_observed": True,
            "checklist": [
                {"step": 1, "status": "passed"},
                {"step": 2, "status": "failed", "evidence": "no change, at 2%"},
            ],
        }
        result = _verification_from_submit_args(args)
        assert result["layer2"]["status"] == "partial"
        assert result["level"] == "partial"

    def test_invalid_overall_defaults_unverified(self):
        args = {"overall": "maybe", "layer2_status": "passed"}
        result = _verification_from_submit_args(args)
        assert result["level"] == "unverified"

    def test_non_list_checklist_ignored(self):
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "primary_evidence_observed": True,
            "checklist": "not a list",
        }
        result = _verification_from_submit_args(args)
        assert "checklist" not in result

    def test_non_dict_checklist_items_filtered(self):
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "primary_evidence_observed": True,
            "checklist": ["string item", {"step": 1, "status": "passed"}],
        }
        result = _verification_from_submit_args(args)
        assert result["checklist"]["total_count"] == 1


class TestFormatVerificationDetail:
    def test_basic_format(self):
        verification = {
            "level": "verified",
            "layer2": {"status": "passed", "details": "CPU at 95%"},
            "checklist": {"items": [
                {"step": 1, "status": "passed", "evidence": "CPU confirmed"},
            ]},
            "warnings": [],
        }
        layer1 = Layer1Result(status="passed", details="blade_status: Running")
        text = _format_verification_detail(verification, layer1)
        assert "verified" in text.lower()
        assert "Layer1:" in text
        assert "Layer2: passed" in text

    def test_with_warnings(self):
        verification = {
            "level": "partial",
            "layer2": {"status": "partial", "details": ""},
            "warnings": ["Some important warning"],
        }
        layer1 = Layer1Result(status="passed", details="")
        text = _format_verification_detail(verification, layer1)
        assert "Some important warning" in text

    def test_no_checklist(self):
        verification = {
            "level": "unverified",
            "layer2": {"status": "failed", "details": "no effect"},
            "warnings": [],
        }
        layer1 = Layer1Result(status="passed", details="")
        text = _format_verification_detail(verification, layer1)
        assert "unverified" in text.lower()


class TestBuildVerifyReplanContext:
    """Tests for _build_verify_replan_context."""

    def test_basic_context(self):
        verification = {
            "level": "unverified",
            "layer1": {"status": "passed", "details": "blade returned success"},
            "layer2": {"status": "failed", "details": "disk usage unchanged"},
            "warnings": ["test warning"],
        }
        ctx = _build_verify_replan_context(verification, [], 0, "k8s-disk-fill")
        assert ctx["affected_step"] == "post-injection verification"
        assert ctx["decision"] == "plan_invalid"
        assert ctx["unresolved_questions"]
        assert ctx["trigger"] == "verify_replan"
        assert ctx["skill_name"] == "k8s-disk-fill"
        assert ctx["iteration_at_failure"] == 1
        assert ctx["failed_tool_calls"] == []
        assert ctx["failed_tool_names"] == []
        assert "Injection executed successfully" in ctx["error_summary"]
        assert "NOT observed" in ctx["error_summary"]
        assert ctx["verifier_findings"]["level"] == "unverified"
        assert ctx["verifier_findings"]["layer1_status"] == "passed"
        assert ctx["verifier_findings"]["layer2_status"] == "failed"
        assert ctx["verifier_findings"]["layer2_details"] == "disk usage unchanged"
        assert ctx["verifier_findings"]["warnings"] == ["test warning"]
        assert ctx["residuals_cleaned"] == []
        assert ctx["residuals_description"] == "None"

    def test_with_failed_evidence(self):
        verification = {
            "level": "unverified",
            "layer1": {"status": "passed", "details": ""},
            "layer2": {"status": "failed", "details": "no effect"},
            "checklist": {
                "items": [
                    {"step": 1, "status": "passed", "evidence": "ok"},
                    {"step": 2, "status": "failed", "evidence": "disk still at 39%"},
                ],
            },
        }
        ctx = _build_verify_replan_context(verification, [], 1, "test-skill")
        assert len(ctx["verifier_findings"]["failed_evidence"]) == 1
        assert "Step 2" in ctx["verifier_findings"]["failed_evidence"][0]
        assert "disk still at 39%" in ctx["verifier_findings"]["failed_evidence"][0]
        assert ctx["iteration_at_failure"] == 2

    def test_with_residuals(self):
        verification = {
            "level": "unverified",
            "layer1": {"status": "passed", "details": ""},
            "layer2": {"status": "failed", "details": ""},
        }
        residuals = [
            {"type": "running_experiment", "id": "abc123", "cleanup_result": "success"},
        ]
        ctx = _build_verify_replan_context(verification, residuals, 0, "test-skill")
        assert ctx["residuals_cleaned"] == residuals
        assert "running_experiment" in ctx["residuals_description"]
        assert "abc123" in ctx["residuals_description"]

    def test_suggestion_mentions_alternative(self):
        verification = {
            "level": "unverified",
            "layer1": {"status": "passed", "details": ""},
            "layer2": {"status": "failed", "details": ""},
        }
        ctx = _build_verify_replan_context(verification, [], 0, "test-skill")
        assert "alternative" in ctx["suggestion"].lower()


class TestCleanupResiduals:
    """Tests for _cleanup_residuals."""

    @pytest.mark.asyncio
    async def test_no_blade_uid_returns_empty(self):
        state = {"blade_uid": ""}
        cleaned = await _cleanup_residuals(state, "/fake/kubeconfig")
        assert cleaned == []

    @pytest.mark.asyncio
    async def test_no_blade_uid_key_returns_empty(self):
        state = {}
        cleaned = await _cleanup_residuals(state, "/fake/kubeconfig")
        assert cleaned == []

    @pytest.mark.asyncio
    async def test_with_blade_uid_cleans_up(self):
        state = {"blade_uid": "test-uid-123"}
        with patch(
            "chaos_agent.tools.blade.blade_destroy"
        ) as mock_destroy:
            mock_destroy.ainvoke = AsyncMock(
                return_value='{"status": "success"}'
            )
            cleaned = await _cleanup_residuals(state, "/fake/kubeconfig")
            assert len(cleaned) == 1
            assert cleaned[0]["type"] == "running_experiment"
            assert cleaned[0]["id"] == "test-uid-123"
            assert "success" in cleaned[0]["cleanup_result"]
            mock_destroy.ainvoke.assert_awaited_once_with(
                {"uid": "test-uid-123", "kubeconfig": "/fake/kubeconfig"}
            )

    @pytest.mark.asyncio
    async def test_blade_destroy_failure_recorded(self):
        state = {"blade_uid": "failing-uid"}
        with patch(
            "chaos_agent.tools.blade.blade_destroy"
        ) as mock_destroy:
            mock_destroy.ainvoke = AsyncMock(
                side_effect=RuntimeError("connection refused")
            )
            cleaned = await _cleanup_residuals(state, "/fake/kubeconfig")
            assert len(cleaned) == 1
            assert cleaned[0]["type"] == "running_experiment"
            assert "failed" in cleaned[0]["cleanup_result"]
            assert "connection refused" in cleaned[0]["cleanup_result"]


class TestRetiredUidsFromResiduals:
    """Tests for _retired_uids_from_residuals (verify-replan UID retirement).

    Only UIDs whose destroy genuinely succeeded may be retired; a failed
    destroy may leave a live experiment that must stay visible to recovery.
    """

    def test_successful_destroy_retired(self):
        residuals = [
            {"type": "running_experiment", "id": "uid-1",
             "cleanup_result": '{"code":200,"success":true}'},
        ]
        assert _retired_uids_from_residuals(residuals) == ["uid-1"]

    def test_exception_failure_not_retired(self):
        residuals = [
            {"type": "running_experiment", "id": "uid-1",
             "cleanup_result": "failed: connection refused"},
        ]
        assert _retired_uids_from_residuals(residuals) == []

    def test_soft_error_not_retired(self):
        """blade_destroy returns 'Error: ...' on exit-code failure without
        raising — such a UID may still be live and must NOT be retired."""
        residuals = [
            {"type": "running_experiment", "id": "uid-1",
             "cleanup_result": "Error: blade destroy failed (exit 1): not found"},
        ]
        assert _retired_uids_from_residuals(residuals) == []

    def test_non_experiment_types_ignored(self):
        residuals = [
            {"type": "debug_pod", "id": "pod-1", "cleanup_result": "deleted"},
            {"type": "running_experiment", "id": "", "cleanup_result": "ok"},
            {"type": "running_experiment", "cleanup_result": "ok"},
        ]
        assert _retired_uids_from_residuals(residuals) == []

    def test_empty_residuals(self):
        assert _retired_uids_from_residuals([]) == []

    def test_mixed_residuals(self):
        residuals = [
            {"type": "running_experiment", "id": "uid-ok",
             "cleanup_result": '{"code":200,"success":true}'},
            {"type": "running_experiment", "id": "uid-err",
             "cleanup_result": "Error: blade destroy failed"},
        ]
        assert _retired_uids_from_residuals(residuals) == ["uid-ok"]


class TestApplyStepCoverageAnswerBased:
    """Answer-based coverage: every step answered; discretion needs a reason."""

    _SKILL = (
        "## 注入验证\n"
        "1. 检查目标内存占用是否升高\n"
        "2. 检查 Pod 是否出现 OOMKilled 事件\n"
        "3. 检查应用 A 的访问延迟\n"
    )

    def _verification(self, items):
        return {
            "level": "verified",
            "layer2": {"status": "passed", "details": ""},
            "warnings": [],
            "checklist": {
                "items": items,
                "total_executed": len(items),
                "total_count": len(items),
            },
        }

    def test_justified_discretionary_answers_do_not_downgrade(self):
        items = [
            {"step": 1, "status": "passed", "category": "core",
             "evidence": "memory 81% via kubectl top"},
            {"step": 2, "status": "expected", "category": "impact",
             "evidence": "no OOMKilled in events; mem-percent=80 below "
                         "eviction threshold"},
            {"step": 3, "status": "not_applicable", "category": "impact",
             "evidence": "'应用 A' matches no workload in this cluster"},
        ]
        v = self._verification(items)
        missing, expected_steps, executed = _apply_step_coverage(
            v, {"skill_case_content": self._SKILL}, None, False,
        )
        assert not missing
        assert expected_steps == 3
        assert executed == 3
        assert v["layer2"]["status"] == "passed"
        assert v["level"] == "verified"

    def test_expected_without_evidence_downgrades_to_partial(self):
        items = [
            {"step": 1, "status": "passed", "evidence": "memory 81%"},
            {"step": 2, "status": "expected"},
            {"step": 3, "status": "not_applicable",
             "evidence": "no workload matches"},
        ]
        v = self._verification(items)
        _apply_step_coverage(v, {"skill_case_content": self._SKILL}, None, False)
        assert v["layer2"]["status"] == "partial"
        assert v["level"] == "partial"
        assert any("without evidence" in w for w in v["warnings"])

    def test_silent_omission_still_flags_gap(self):
        items = [{"step": 1, "status": "passed", "evidence": "memory 81%"}]
        v = self._verification(items)
        missing, _, _ = _apply_step_coverage(
            v, {"skill_case_content": self._SKILL}, None, False,
        )
        assert missing == [2, 3]
        assert v["layer2"]["status"] == "partial"


class TestEnforceDiskBurnFactsImpactExclusion:
    """Burn override must not flip IMPACT findings or let them gate level."""

    def test_impact_items_survive_override_and_do_not_gate_level(self):
        verification = {
            "level": "partial",
            "layer2": {"status": "failed", "details": ""},
            "warnings": [],
            "checklist": {"items": [
                {"step": 1, "status": "failed", "category": "core",
                 "evidence": "no I/O delta seen"},
                {"step": 2, "status": "failed", "category": "impact",
                 "evidence": "no latency increase observed"},
            ]},
        }
        state = {"disk_burn_post_check": {
            "burn_io_detected": True,
            "active_partitions": [
                {"name": "/dev/vdb", "write_throughput_mb_s": 120}],
        }}
        applied = _enforce_disk_burn_facts(verification, state)
        assert applied
        core_item, impact_item = verification["checklist"]["items"]
        # CORE step overridden by the programmatic I/O evidence.
        assert core_item["status"] == "passed"
        assert "OVERRIDE" in core_item["evidence"]
        # IMPACT finding preserved verbatim — never a fake pass.
        assert impact_item["status"] == "failed"
        assert "OVERRIDE" not in impact_item["evidence"]
        assert verification["layer2"]["status"] == "passed"
        # IMPACT failure does not gate the level.
        assert verification["level"] == "verified"


class TestSubmitArgsImpactEvidenceExclusion:
    """IMPACT absence-phrasing evidence must not force auto-downgrade."""

    def test_impact_absence_evidence_does_not_force_downgrade(self):
        # CORE step failed (benign, timing lag) + IMPACT finding whose
        # evidence carries an absence phrase. Only CORE evidence may
        # trigger the objective-measurement auto-downgrade.
        args = {
            "overall": "verified",
            "layer2_status": "passed",
            "primary_evidence_observed": True,
            "checklist": [
                {"step": 1, "status": "failed", "category": "core",
                 "evidence": "timing lag; retry confirmed effect later"},
                {"step": 2, "status": "failed", "category": "impact",
                 "evidence": "no observable business impact"},
            ],
        }
        result = _verification_from_submit_args(args)
        assert result["layer2"]["status"] == "passed"
        assert any("inconsistency" in w for w in result["warnings"])


class TestMode2CoverageDisabledContract:
    """When the extractor cannot enumerate steps, the count fallback is off."""

    _SKILL = "# 场景\n## 注入验证\n1.\n"  # counter counts 1, extractor yields []

    def test_count_fallback_does_not_fire_without_parseable_steps(self):
        # Checklist answers nothing numerically, yet no count-based gap or
        # downgrade may fire — coverage validation is DISABLED per prompt.
        v = {
            "level": "verified",
            "layer2": {"status": "passed", "details": ""},
            "warnings": [],
            "checklist": {"items": [
                {"step": 1, "status": "passed", "evidence": "mem elevated"},
            ], "total_executed": 1},
        }
        missing, expected_steps, executed = _apply_step_coverage(
            v, {"skill_case_content": self._SKILL}, None, False,
        )
        assert expected_steps == 0
        assert not missing
        assert v["layer2"]["status"] == "passed"
        assert not any("never attempted" in w for w in v["warnings"])
