"""Tests for _conflict_check module: _extract_param_from_flag, _analyze_overlap, check_blade_conflicts."""

import json

import pytest

from chaos_agent.agent.nodes.side_effect._conflict_check import (
    ConflictInfo,
    _extract_param_from_flag,
    _analyze_overlap,
    check_blade_conflicts,
)


class TestExtractParamFromFlag:
    """Tests for _extract_param_from_flag()."""

    def test_equals_format(self):
        flag = "--namespace=cms-demo --labels=app=accounting"
        assert _extract_param_from_flag(flag, "--namespace") == "cms-demo"
        assert _extract_param_from_flag(flag, "--labels") == "app=accounting"

    def test_space_format(self):
        flag = "--namespace cms-demo --labels app=accounting"
        assert _extract_param_from_flag(flag, "--namespace") == "cms-demo"
        assert _extract_param_from_flag(flag, "--labels") == "app=accounting"

    def test_mixed_formats(self):
        flag = "--namespace=cms-demo --labels app=accounting --timeout 180s"
        assert _extract_param_from_flag(flag, "--namespace") == "cms-demo"
        assert _extract_param_from_flag(flag, "--labels") == "app=accounting"
        assert _extract_param_from_flag(flag, "--timeout") == "180s"

    def test_param_not_present(self):
        flag = "--namespace cms-demo --cpu-percent 80"
        assert _extract_param_from_flag(flag, "--labels") == ""

    def test_empty_flag(self):
        assert _extract_param_from_flag("", "--namespace") == ""

    def test_param_name_with_dashes(self):
        flag = "--cpu-percent=80 --mem-percent=90"
        assert _extract_param_from_flag(flag, "--cpu-percent") == "80"
        assert _extract_param_from_flag(flag, "--mem-percent") == "90"

    def test_value_with_equals_sign(self):
        # Labels value may contain = in key=value format
        flag = "--labels app=accounting,version=v1"
        assert _extract_param_from_flag(flag, "--labels") == "app=accounting,version=v1"

    def test_param_at_end_of_flag(self):
        flag = "--namespace cms-demo --timeout 180s --names pod1"
        assert _extract_param_from_flag(flag, "--names") == "pod1"

    def test_comma_separated_names(self):
        flag = "--namespace cms-demo --names pod1,pod2,pod3"
        assert _extract_param_from_flag(flag, "--names") == "pod1,pod2,pod3"

    def test_single_dash_param_name(self):
        # Function strips leading dashes, so "-" prefix works too
        flag = "--namespace cms-demo"
        assert _extract_param_from_flag(flag, "namespace") == "cms-demo"


class TestAnalyzeOverlap:
    """Tests for _analyze_overlap()."""

    def _make_conflict(self, **kwargs):
        defaults = {
            "uid": "abc123def4567890",
            "flag": "",
            "namespace": "",
            "names": "",
            "labels": "",
        }
        defaults.update(kwargs)
        return ConflictInfo(**defaults)

    def test_same_namespace_same_name_overlaps(self):
        ci = self._make_conflict(namespace="cms-demo", names="pod-1")
        _analyze_overlap(ci, "cms-demo", "pod-1", "")
        assert ci.overlaps_target is True
        assert "same target" in ci.overlap_reason
        assert "pod-1" in ci.overlap_reason

    def test_same_namespace_different_name_no_overlap(self):
        ci = self._make_conflict(namespace="cms-demo", names="pod-1")
        _analyze_overlap(ci, "cms-demo", "pod-2", "")
        assert ci.overlaps_target is False
        assert ci.overlap_reason == ""

    def test_different_namespace_same_name_no_overlap(self):
        ci = self._make_conflict(namespace="other-ns", names="pod-1")
        _analyze_overlap(ci, "cms-demo", "pod-1", "")
        assert ci.overlaps_target is False

    def test_comma_separated_names_partial_overlap(self):
        ci = self._make_conflict(namespace="cms-demo", names="pod-1,pod-2")
        _analyze_overlap(ci, "cms-demo", "pod-2,pod-3", "")
        assert ci.overlaps_target is True
        assert "pod-2" in ci.overlap_reason

    def test_same_namespace_same_labels_overlaps(self):
        ci = self._make_conflict(namespace="cms-demo", labels="app=accounting")
        _analyze_overlap(ci, "cms-demo", "", "app=accounting")
        assert ci.overlaps_target is True
        assert "same labels" in ci.overlap_reason

    def test_same_namespace_partial_labels_overlap(self):
        ci = self._make_conflict(
            namespace="cms-demo", labels="app=accounting,version=v1"
        )
        _analyze_overlap(ci, "cms-demo", "", "app=accounting,version=v2")
        assert ci.overlaps_target is True
        assert "app=accounting" in ci.overlap_reason

    def test_same_namespace_different_labels_no_overlap(self):
        ci = self._make_conflict(namespace="cms-demo", labels="app=billing")
        _analyze_overlap(ci, "cms-demo", "", "app=accounting")
        assert ci.overlaps_target is False

    def test_both_name_and_labels_overlap(self):
        ci = self._make_conflict(
            namespace="cms-demo", names="pod-1", labels="app=accounting"
        )
        _analyze_overlap(ci, "cms-demo", "pod-1", "app=accounting")
        assert ci.overlaps_target is True
        assert "same target" in ci.overlap_reason
        assert "same labels" in ci.overlap_reason

    def test_no_namespace_info_no_overlap(self):
        ci = self._make_conflict(names="pod-1")
        _analyze_overlap(ci, "cms-demo", "pod-1", "")
        assert ci.overlaps_target is False

    def test_empty_target_no_overlap(self):
        ci = self._make_conflict(namespace="cms-demo", names="pod-1")
        _analyze_overlap(ci, "cms-demo", "", "")
        assert ci.overlaps_target is False

    def test_conflict_no_names_no_labels_no_overlap(self):
        ci = self._make_conflict(namespace="cms-demo")
        _analyze_overlap(ci, "cms-demo", "pod-1", "app=accounting")
        assert ci.overlaps_target is False


class TestDestroyedStatusFilter:
    """Tests that Destroyed/Revoked experiments are excluded from conflict detection."""

    def _make_blade_status_mock(self, monkeypatch, blade_output: str):
        """Patch execute_via_transport inside the function's local import."""
        from chaos_agent.config.settings import settings as _s
        monkeypatch.setattr(_s, "kube_connection_mode", "kubeconfig")

        async def fake_execute(cmd, target, **kwargs):
            from chaos_agent.tools.shell import CommandResult
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd_str and "pods" in cmd_str:
                return CommandResult(
                    stdout="chaosblade   otel-c-tool-abc12   1/1   Running   0   32d\n",
                    stderr="", exit_code=0, duration_ms=100.0,
                )
            return CommandResult(
                stdout=blade_output,
                stderr="", exit_code=0, duration_ms=200.0,
            )
        # execute_via_transport is imported dynamically inside the function,
        # so we must patch it at the source module.
        monkeypatch.setattr("chaos_agent.transports.execute_via_transport", fake_execute)

    @pytest.mark.asyncio
    async def test_destroyed_experiments_excluded(self, monkeypatch):
        """Destroyed experiments should not appear in conflict list."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "aaaa0000bbbb1111", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=pod-1 --timeout 600", "Status": "Destroyed"},
                {"Uid": "cccc2222dddd3333", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=pod-1 --timeout 600", "Status": "Running"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="cms-demo", target_names="pod-1",
        )
        assert len(uids) == 1
        assert "cccc2222dddd3333" in uids
        assert "aaaa0000bbbb1111" not in uids

    @pytest.mark.asyncio
    async def test_revoked_experiments_excluded(self, monkeypatch):
        """Revoked experiments should not appear in conflict list."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "eeee4444ffff5555", "Flag": "k8s pod-cpu fullload --namespace=test --labels=app=myapp", "Status": "Revoked"},
                {"Uid": "gggg6666hhhh7777", "Flag": "k8s pod-cpu fullload --namespace=test --labels=app=myapp", "Status": "Success"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="test", labels="app=myapp",
        )
        assert len(uids) == 1
        assert "gggg6666hhhh7777" in uids
        assert "eeee4444ffff5555" not in uids

    @pytest.mark.asyncio
    async def test_all_destroyed_returns_empty(self, monkeypatch):
        """When all experiments are Destroyed, conflict list should be empty."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "aaaa0000bbbb1111", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=pod-1", "Status": "Destroyed"},
                {"Uid": "cccc2222dddd3333", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=pod-2", "Status": "Destroyed"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="cms-demo", target_names="pod-1",
        )
        assert uids == []
        assert details == []

    @pytest.mark.asyncio
    async def test_running_experiments_counted_as_overlap(self, monkeypatch):
        """Only Running/Success experiments should trigger target overlap."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "overlap1112223334", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=accounting-6fbdb464c7-zf458 --timeout 600", "Status": "Running"},
                {"Uid": "destroyed5556667778", "Flag": "k8s pod-disk burn --namespace=cms-demo --names=accounting-6fbdb464c7-zf458 --timeout 600", "Status": "Destroyed"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="cms-demo", target_names="accounting-6fbdb464c7-zf458",
            request_scope_target_action="pod-disk-burn",
        )
        assert len(uids) == 1
        assert len(details) == 1
        assert details[0].overlaps_target is True


class TestThreeTierIsolation:
    """Three-tier isolation semantics (inject-17617837 review, round 2).

    Tier 1 — pod-scope experiment in a DIFFERENT namespace (both known):
    provably unrelated → silently skipped. This restores the isolation
    intent of the original namespace filter (63e68f8): shared clusters
    must not surface other teams' experiments as conflicts on every
    injection.

    Tier 2 — Flag carries no --namespace (cri scope targets a
    container-id, node scope a node name — node faults hit every
    namespace on that node, yet carry no ns to compare): visible in
    details (undeterminable=True) but NEVER in uids, so safety_check
    reports a weak note instead of a "consider destroying" warning.
    Dropping tier 2 entirely is how the original bug reported "no active
    experiments" on a cluster with 4 recorded experiments, one of them
    a live 2.5-month-old Success cri mem-load CR.

    Tier 3 — same namespace: conflict candidate → uids + overlap analysis.
    """

    def _make_blade_status_mock(self, monkeypatch, blade_output: str):
        from chaos_agent.config.settings import settings as _s
        monkeypatch.setattr(_s, "kube_connection_mode", "kubeconfig")

        async def fake_execute(cmd, target, **kwargs):
            from chaos_agent.tools.shell import CommandResult
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd_str and "pods" in cmd_str:
                return CommandResult(
                    stdout="chaosblade   otel-c-tool-abc12   1/1   Running   0   32d\n",
                    stderr="", exit_code=0, duration_ms=100.0,
                )
            return CommandResult(
                stdout=blade_output,
                stderr="", exit_code=0, duration_ms=200.0,
            )

        monkeypatch.setattr(
            "chaos_agent.transports.execute_via_transport", fake_execute
        )

    @pytest.mark.asyncio
    async def test_cri_scope_undeterminable_not_conflict(self, monkeypatch):
        """cri experiments (no --namespace in Flag) must stay visible in
        details as undeterminable — the live-cluster scenario: a
        2.5-month-old Success cri mem-load CR — without being asserted
        as a conflict (uids), which would tell the user to destroy
        another team's experiment."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "f6f7ef2ed7ed6565",
                 "Flag": " --mem-percent=80 --timeout=600 --container-id=7720f460d9fd --mode=ram --container-runtime=containerd",
                 "Status": "Success", "Command": "cri", "SubCommand": "mem load"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        # Tier 2: visible in details, never a conflict
        assert uids == []
        assert len(details) == 1
        assert details[0].undeterminable is True
        assert details[0].namespace == ""
        assert details[0].overlaps_target is False

    @pytest.mark.asyncio
    async def test_node_scope_undeterminable_not_conflict(self, monkeypatch):
        """node-scope experiments (node name, no --namespace) are the one
        fault type that PHYSICALLY crosses namespaces, yet carries no ns
        to compare — they must land in the undeterminable tier, not be
        isolated away like a cross-ns pod experiment."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "dddd8888eeee9999",
                 "Flag": "k8s node-network loss --names worker-node-1 --percent 100 --timeout 300",
                 "Status": "Success"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        assert uids == []
        assert len(details) == 1
        assert details[0].undeterminable is True

    @pytest.mark.asyncio
    async def test_cross_namespace_silently_skipped(self, monkeypatch):
        """Tier 1 isolation: pod-scope experiments in OTHER namespaces are
        provably unrelated and must be silently skipped — not reported,
        not warned about, not suggested for destruction. Shared clusters
        must not surface other teams' experiments on every injection."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "aaaabbbbccccdddd",
                 "Flag": "k8s pod-cpu fullload --namespace=other-ns --names=pod-x",
                 "Status": "Success"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="cms-demo", target_names="pod-1",
        )
        assert uids == []
        assert details == []

    @pytest.mark.asyncio
    async def test_mixed_cluster_three_tier_split(self, monkeypatch):
        """Live-cluster shape: 1 cri (no ns) + 1 same-target + 1 other-ns
        + 1 Destroyed → tier 3 only in uids (1), tiers 1 skipped, tier 2
        in details only, Destroyed excluded everywhere."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "f6f7ef2ed7ed6565",
                 "Flag": " --mem-percent=80 --timeout=600 --container-id=7720f460d9fd",
                 "Status": "Success"},
                {"Uid": "77bdc43df25020f5",
                 "Flag": "k8s pod-cpu fullload --namespace=taokeeper --names=zookeeper-0-0",
                 "Status": "Success"},
                {"Uid": "aaaabbbbccccdddd",
                 "Flag": "k8s pod-cpu fullload --namespace=other-ns --names=pod-x",
                 "Status": "Success"},
                {"Uid": "cccc2222dddd3333",
                 "Flag": "k8s pod-disk burn --namespace=taokeeper --names=zookeeper-0-0",
                 "Status": "Destroyed"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        # Tier 3 only: the same-ns experiment
        assert uids == ["77bdc43df25020f5"]
        by_uid = {c.uid: c for c in details}
        # Tier 3: same-ns experiment analyzed and flagged
        assert by_uid["77bdc43df25020f5"].overlaps_target is True
        assert by_uid["77bdc43df25020f5"].undeterminable is False
        # Tier 2: cri experiment visible in details, undeterminable
        assert by_uid["f6f7ef2ed7ed6565"].undeterminable is True
        assert by_uid["f6f7ef2ed7ed6565"].overlaps_target is False
        # Tier 1: cross-ns pod experiment silently skipped
        assert "aaaabbbbccccdddd" not in by_uid
        # Destroyed excluded everywhere
        assert "cccc2222dddd3333" not in by_uid
        assert "cccc2222dddd3333" not in uids

    @pytest.mark.asyncio
    async def test_regex_fallback_all_undeterminable(self, monkeypatch):
        """Unparseable blade status output: UIDs are visible but nothing
        else is known — they follow the undeterminable tier (details,
        never uids) instead of being asserted as conflicts we cannot
        analyze."""
        self._make_blade_status_mock(monkeypatch, "f6f7ef2ed7ed6565   aaaabbbbccccdddd   not-json")

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        assert uids == []
        assert len(details) == 2
        assert all(c.undeterminable for c in details)

    @pytest.mark.asyncio
    async def test_unknown_target_namespace_reports_everything(self, monkeypatch):
        """When the caller passes no target namespace, tier-1 isolation
        cannot be decided — every active experiment stays visible
        (undeterminable ones in details, ns-known ones in uids)."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "aaaabbbbccccdddd",
                 "Flag": "k8s pod-cpu fullload --namespace=other-ns --names=pod-x",
                 "Status": "Success"},
                {"Uid": "f6f7ef2ed7ed6565",
                 "Flag": " --mem-percent=80 --container-id=7720f460d9fd",
                 "Status": "Success"},
            ]
        }))

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="", target_names="",
        )
        assert uids == ["aaaabbbbccccdddd"]
        assert len(details) == 2
        by_uid = {c.uid: c for c in details}
        assert by_uid["f6f7ef2ed7ed6565"].undeterminable is True
        assert by_uid["aaaabbbbccccdddd"].undeterminable is False


class TestBladeStatusFailureReportsUnknown:
    """inject-17617837: a 31s wiz timeout (exit 1, empty stdout) flowed
    through the regex fallback on an empty string and was reported as
    'no active experiments' (clear) — a silent false-negative. A failed
    check must report FAILED/unknown, never clear."""

    def _make_failing_status_mock(self, monkeypatch, exit_code: int, stdout: str):
        from chaos_agent.config.settings import settings as _s
        monkeypatch.setattr(_s, "kube_connection_mode", "kubeconfig")

        async def fake_execute(cmd, target, **kwargs):
            from chaos_agent.tools.shell import CommandResult
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd_str and "pods" in cmd_str:
                return CommandResult(
                    stdout="chaosblade   otel-c-tool-abc12   1/1   Running   0   32d\n",
                    stderr="", exit_code=0, duration_ms=100.0,
                )
            return CommandResult(
                stdout=stdout,
                stderr="", exit_code=exit_code, duration_ms=31000.0,
            )

        monkeypatch.setattr(
            "chaos_agent.transports.execute_via_transport", fake_execute
        )

        dispatched: list[str] = []

        async def fake_dispatch(node, message, **kwargs):
            dispatched.append(message)

        monkeypatch.setattr(
            "chaos_agent.agent.nodes.side_effect._conflict_check.dispatch_node_message",
            fake_dispatch,
        )
        return dispatched

    @pytest.mark.asyncio
    async def test_timeout_empty_stdout_reports_unknown_not_clear(self, monkeypatch):
        dispatched = self._make_failing_status_mock(
            monkeypatch, exit_code=1, stdout="",
        )
        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper", target_names="pod-1",
        )
        assert uids == [] and details == []
        assert len(dispatched) == 1
        assert "FAILED" in dispatched[0]
        assert "UNKNOWN" in dispatched[0]
        assert "no active experiments" not in dispatched[0]

    @pytest.mark.asyncio
    async def test_zero_exit_empty_stdout_reports_unknown(self, monkeypatch):
        dispatched = self._make_failing_status_mock(
            monkeypatch, exit_code=0, stdout="",
        )
        uids, _ = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper",
        )
        assert uids == []
        assert "FAILED" in dispatched[0]

    @pytest.mark.asyncio
    async def test_nonzero_exit_with_output_reports_unknown(self, monkeypatch):
        """exit 1 with non-empty (error) output must also fail loudly —
        error text is not experiment data."""
        dispatched = self._make_failing_status_mock(
            monkeypatch, exit_code=1, stdout="Error: connection refused",
        )
        uids, _ = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="",
            namespace="taokeeper",
        )
        assert uids == []
        assert "FAILED" in dispatched[0]


class TestConclusionPersistence:
    """inject-0db61248 forensics: the check's conclusion (especially the
    tier-2 undeterminable note) was emitted via dispatch_node_message
    (TUI-stream-only) and tracker (not exported), leaving NO trace in the
    persisted task record — post-hoc analysis had to reconstruct it from
    behavioral signatures. Every conclusion point must now persist via
    sync_node_status_to_session so the task JSON carries it."""

    def _make_blade_status_mock(self, monkeypatch, blade_output: str):
        from chaos_agent.config.settings import settings as _s
        monkeypatch.setattr(_s, "kube_connection_mode", "kubeconfig")

        async def fake_execute(cmd, target, **kwargs):
            from chaos_agent.tools.shell import CommandResult
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd_str and "pods" in cmd_str:
                return CommandResult(
                    stdout="chaosblade   otel-c-tool-abc12   1/1   Running   0   32d\n",
                    stderr="", exit_code=0, duration_ms=100.0,
                )
            return CommandResult(
                stdout=blade_output,
                stderr="", exit_code=0, duration_ms=200.0,
            )

        monkeypatch.setattr(
            "chaos_agent.transports.execute_via_transport", fake_execute
        )

    def _capture_persist(self, monkeypatch):
        calls: list[tuple[str, dict, str, dict]] = []

        def fake_sync(state, node_name, message, detail=None):
            calls.append((state.get("task_id", ""), node_name, message, detail or {}))

        monkeypatch.setattr(
            "chaos_agent.agent.nodes.store._store_sync.sync_node_status_to_session",
            fake_sync,
        )
        return calls

    @pytest.mark.asyncio
    async def test_undeterminable_note_persisted(self, monkeypatch):
        """The live-cluster scenario: a cri experiment exists, tier-2 note
        must land in the task record with its uids for post-hoc forensics."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "f6f7ef2ed7ed6565",
                 "Flag": " --mem-percent=80 --container-id=7720f460d9fd",
                 "Status": "Success"},
            ]
        }))
        calls = self._capture_persist(monkeypatch)

        await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="inject-test0001persist",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        assert len(calls) == 1
        task_id, node, message, detail = calls[0]
        assert task_id == "inject-test0001persist"
        assert node == "conflict-check"
        assert "no active experiments in namespace 'taokeeper'" in message
        assert "carry no namespace info" in message
        assert "f6f7ef2ed7ed6565" in message
        assert detail["status"] == "clear"
        assert detail["undeterminable_uids"] == ["f6f7ef2ed7ed6565"]

    @pytest.mark.asyncio
    async def test_conflicts_found_persisted(self, monkeypatch):
        """Tier-3 conflicts must persist with uid list + overlap count."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True,
            "result": [
                {"Uid": "77bdc43df25020f5",
                 "Flag": "k8s pod-cpu fullload --namespace=taokeeper --names=zookeeper-0-0",
                 "Status": "Success"},
            ]
        }))
        calls = self._capture_persist(monkeypatch)

        await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="inject-test0001persist",
            namespace="taokeeper", target_names="zookeeper-0-0",
        )
        assert len(calls) == 1
        _, node, message, detail = calls[0]
        assert node == "conflict-check"
        assert "1 active experiment(s) in namespace 'taokeeper'" in message
        assert "target overlap" in message
        assert detail["status"] == "conflicts_found"
        assert detail["uids"] == ["77bdc43df25020f5"]
        assert detail["overlap_count"] == 1

    @pytest.mark.asyncio
    async def test_clear_persisted(self, monkeypatch):
        """A genuinely empty cluster persists a scoped-clear conclusion."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True, "result": [],
        }))
        calls = self._capture_persist(monkeypatch)

        await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="inject-test0001persist",
            namespace="taokeeper",
        )
        assert len(calls) == 1
        _, _, message, detail = calls[0]
        assert "no active experiments in namespace 'taokeeper'" in message
        assert "but" not in message  # plain clear, no undeterminable clause
        assert detail["status"] == "clear"

    @pytest.mark.asyncio
    async def test_failed_check_persisted_as_failed_not_clear(self, monkeypatch):
        """A failed blade status (the inject-17617837 31s-timeout shape)
        must persist status=failed — the persisted record must never let
        a failed check masquerade as a clear one."""
        from chaos_agent.tools.shell import CommandResult

        async def fake_execute(cmd, target, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd_str and "pods" in cmd_str:
                return CommandResult(
                    stdout="chaosblade   otel-c-tool-abc12   1/1   Running   0   32d\n",
                    stderr="", exit_code=0, duration_ms=100.0,
                )
            return CommandResult(stdout="", stderr="", exit_code=1, duration_ms=30000.0)

        from chaos_agent.config.settings import settings as _s
        monkeypatch.setattr(_s, "kube_connection_mode", "kubeconfig")
        monkeypatch.setattr("chaos_agent.transports.execute_via_transport", fake_execute)
        calls = self._capture_persist(monkeypatch)

        await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="inject-test0001persist",
            namespace="taokeeper",
        )
        assert len(calls) == 1
        _, _, message, detail = calls[0]
        assert "FAILED" in message
        assert "UNKNOWN" in message
        assert detail["status"] == "failed"

    @pytest.mark.asyncio
    async def test_persistence_failure_never_breaks_check(self, monkeypatch):
        """_persist_conclusion is fire-and-forget: a broken session store
        must not corrupt the check's return value."""
        self._make_blade_status_mock(monkeypatch, json.dumps({
            "code": 200, "success": True, "result": [],
        }))

        def exploding_sync(state, node_name, message, detail=None):
            raise RuntimeError("session store exploded")

        monkeypatch.setattr(
            "chaos_agent.agent.nodes.store._store_sync.sync_node_status_to_session",
            exploding_sync,
        )

        uids, details = await check_blade_conflicts(
            kubeconfig="/tmp/config", task_id="inject-test0001persist",
            namespace="taokeeper",
        )
        assert uids == []
        assert details == []
