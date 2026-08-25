import pytest
from pathlib import Path

from chaos_agent.agent.result.task_snapshot import (
    TaskSnapshot,
    _rebuild_inject_verification_summary,
    build_recover_initial_from_task_snapshot,
    resolve_recover_initial_state,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _target(name: str) -> dict:
    return {
        "namespace": "default",
        "names": [name],
        "labels": {},
        "resource_type": "pod",
    }


def test_task_snapshot_prefers_task_store_when_no_increment_log():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "experiment_uid": "uid-from-store",
            "skill_name": "pod-cpu-fullload",
            "target": _target("store-pod"),
            "params": {"cpu-percent": "80"},
            "inject_context": "store context",
            "verification": {"layer2": {"status": "passed", "details": "store"}},
        },
        session={
            "result_summary": {
                "data": {
                    "experiment_uid": "uid-from-session",
                    "fault_type": "pod-network-loss",
                    "target": _target("session-pod"),
                    "params": {"percent": "100"},
                    "verification": {
                        "layer2": {"status": "passed", "details": "session"}
                    },
                }
            },
            "messages": [],
        },
        has_increment_log=False,
    )

    assert snapshot is not None
    assert snapshot.experiment_uid == "uid-from-store"
    assert snapshot.skill_name == "pod-cpu-fullload"
    assert snapshot.fault_type == "pod-cpu-fullload"
    assert snapshot.target["names"] == ["store-pod"]
    assert snapshot.params == {"cpu-percent": "80"}
    assert snapshot.inject_context == "store context"
    assert snapshot.verification["layer2"]["details"] == "store"


def test_task_snapshot_prefers_session_when_increment_log_exists():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "experiment_uid": "uid-from-store",
            "skill_name": "pod-cpu-fullload",
            "target": _target("store-pod"),
            "params": {"cpu-percent": "80"},
            "inject_context": "store context",
            "verification": {"layer2": {"status": "passed", "details": "store"}},
        },
        session={
            "result_summary": {
                "data": {
                    "experiment_uid": "uid-from-session",
                    "fault_type": "pod-network-loss",
                    "target": _target("session-pod"),
                    "params": {"percent": "100"},
                    "verification": {
                        "layer2": {"status": "passed", "details": "session"}
                    },
                }
            },
            "messages": [],
            "tui_session_id": "sid-from-session",
        },
        has_increment_log=True,
    )

    assert snapshot is not None
    assert snapshot.experiment_uid == "uid-from-session"
    assert snapshot.skill_name == "pod-cpu-fullload"
    assert snapshot.fault_type == "pod-network-loss"
    assert snapshot.target["names"] == ["session-pod"]
    assert snapshot.params == {"percent": "100"}
    assert snapshot.inject_context == "store context"
    assert snapshot.verification["layer2"]["details"] == "session"
    assert snapshot.tui_session_id == "sid-from-session"


def test_task_snapshot_reads_jsonl_even_when_json_snapshot_missing(tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage

    from chaos_agent.agent.result.task_snapshot import _read_task_session
    from chaos_agent.memory.session_store import SessionStore, set_global_session_store

    session_store = SessionStore(tmp_path / "tasks")
    set_global_session_store(session_store)
    try:
        session_store.create_session("task-jsonl-only", operation="inject")
        session_store.append_messages(
            "task-jsonl-only",
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "blade_create",
                            "args": {},
                            "id": "tc-create",
                        }
                    ],
                ),
                ToolMessage(
                    content='{"code":200,"success":true,"result":"uid-jsonl-only"}',
                    name="blade_create",
                    tool_call_id="tc-create",
                ),
            ],
        )
        (tmp_path / "tasks" / "task-jsonl-only.json").unlink()

        session, has_increment_log = _read_task_session("task-jsonl-only")
        snapshot = TaskSnapshot.from_sources(
            task_id="task-jsonl-only",
            record={
                "skill_name": "pod-cpu-fullload",
                "target": _target("demo"),
                "params": {"cpu-percent": "80"},
            },
            session=session,
            has_increment_log=has_increment_log,
        )
    finally:
        set_global_session_store(None)  # type: ignore[arg-type]

    assert has_increment_log is True
    assert session is not None
    assert len(session["messages"]) == 2
    assert snapshot is not None
    assert snapshot.experiment_uid == "uid-jsonl-only"
    assert "blade_create" in snapshot.inject_context


def test_task_snapshot_builds_fault_spec_from_merged_context():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "skill_name": "pod-network-loss",
            "target": _target("demo"),
            "params": {"percent": "100"},
        },
        session=None,
        has_increment_log=False,
    )

    assert snapshot is not None
    assert snapshot.has_recover_context is True
    assert snapshot.fault_spec() == {
        "namespace": "default",
        "scope": "pod",
        "names": ["demo"],
        "labels": {},
        "fault_target": "network",
        "fault_action": "loss",
        "params": {"percent": "100"},
        "params_flags": [],
        "duration_seconds": 0,
        "source": "task_snapshot_rebuild",
        "user_description": "",
        "case_resource_path": "",
        "revision": 0,
        "objective": "",
        "boundaries": [],
        "constraints": [],
        "assumptions": [],
    }


def test_task_snapshot_prefers_record_fault_spec_over_stale_legacy_fields():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "skill_name": "active-chaos-skill",
            "target": _target("stale-pod"),
            "params": {"cpu-percent": "80"},
            "fault_spec": {
                "namespace": "prod",
                "scope": "pod",
                "names": ["fresh-pod"],
                "labels": {"app": "demo"},
                "fault_target": "network",
                "fault_action": "loss",
                "params": {"percent": "100"},
                "params_flags": [],
                "duration_seconds": 0,
                "source": "task_store",
                "user_description": "",
            },
        },
        session=None,
        has_increment_log=False,
    )

    assert snapshot is not None
    assert snapshot.skill_name == "active-chaos-skill"
    assert snapshot.fault_type == "pod-network-loss"
    assert snapshot.target == {
        "namespace": "prod",
        "names": ["fresh-pod"],
        "labels": {"app": "demo"},
        "resource_type": "pod",
    }
    assert snapshot.params == {"percent": "100"}
    assert snapshot.fault_spec()["fault_target"] == "network"
    assert snapshot.fault_spec()["names"] == ["fresh-pod"]


def test_task_snapshot_increment_log_can_override_record_fault_spec():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "skill_name": "active-chaos-skill",
            "fault_spec": {
                "namespace": "prod",
                "scope": "pod",
                "names": ["old-pod"],
                "labels": {},
                "fault_target": "network",
                "fault_action": "loss",
                "params": {"percent": "100"},
                "params_flags": [],
                "duration_seconds": 0,
                "source": "task_store",
                "user_description": "",
            },
        },
        session={
            "result_summary": {
                "data": {
                    "fault_type": "pod-cpu-fullload",
                    "target": _target("session-pod"),
                    "params": {"cpu-percent": "80"},
                }
            },
            "messages": [],
        },
        has_increment_log=True,
    )

    assert snapshot is not None
    assert snapshot.skill_name == "active-chaos-skill"
    assert snapshot.fault_type == "pod-cpu-fullload"
    assert snapshot.target["names"] == ["session-pod"]
    assert snapshot.params == {"cpu-percent": "80"}
    assert snapshot.fault_spec()["fault_target"] == "cpu"
    assert snapshot.fault_spec()["names"] == ["session-pod"]


def test_task_snapshot_incomplete_fault_spec_does_not_mask_legacy_target():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "skill_name": "pod-cpu-fullload",
            "target": _target("legacy-pod"),
            "params": {"cpu-percent": "80"},
            "fault_spec": {"params": {"cpu-percent": "90"}},
        },
        session=None,
        has_increment_log=False,
    )

    assert snapshot is not None
    assert snapshot.target["names"] == ["legacy-pod"]
    assert snapshot.fault_spec()["duration_seconds"] == 0


@pytest.mark.asyncio
async def test_recover_initial_from_task_snapshot_uses_snapshot_fields():
    class _Registry:
        def activate(self, skill_name):
            assert skill_name == "pod-cpu-fullload"
            return "skill case text"

    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "experiment_uid": "uid-from-store",
            "skill_name": "pod-cpu-fullload",
            "target": _target("demo"),
            "params": {"cpu-percent": "80"},
            "kubeconfig": "/old/kubeconfig",
            "kube_context": "ctx-a",
            "injection_method": "kubectl_exec",
            "execution_artifacts": [{"artifact_id": "uid-debug", "type": "debug_pod"}],
            "kubectl_exec_pod_name": "tool-pod-a",
            "gmt_create": "2026-06-18T10:00:00+08:00",
            "verification": {
                "layer2": {"status": "passed", "details": "verified"}
            },
        },
        session={"messages": []},
        has_increment_log=False,
        tui_session_id="sid-1",
    )

    initial = await build_recover_initial_from_task_snapshot(
        snapshot,
        record_task_id="task-recover",
        agents={"skill_registry": _Registry()},
        kubeconfig_override="/new/kubeconfig",
    )

    assert initial["task_id"] == "task-recover"
    assert initial["parent_task_id"] == "task-inject"
    assert initial["tui_session_id"] == "sid-1"
    assert initial["experiment_uid"] == "uid-from-store"
    assert initial["skill_name"] == "pod-cpu-fullload"
    assert initial["fault_type"] == "pod-cpu-fullload"
    assert initial["skill_case_content"] == "skill case text"
    assert initial["inject_verification_summary"] == (
        "Layer2=passed, Details=verified"
    )
    assert initial["kubeconfig"] == "/new/kubeconfig"
    assert initial["kube_context"] == "ctx-a"
    assert initial["injection_method"] == "kubectl_exec"
    assert initial["execution_artifacts"] == [
        {"artifact_id": "uid-debug", "type": "debug_pod"}
    ]
    assert initial["kubectl_exec_pod_name"] == "tool-pod-a"


def test_runtime_recover_entrypoints_use_task_snapshot_resolver():
    """Recover entrypoints should not bypass TaskSnapshot merge policy."""

    required_resolver_paths = {
        "src/chaos_agent/cli/runner.py",
        "src/chaos_agent/server/routes/recover_common.py",
        "src/chaos_agent/server/routes/turn_event_stream.py",
        "src/chaos_agent/server/routes/turn_result.py",
        # L4 SDK recover entrypoints: agent.py was split into mixins in the
        # baseline refactor (b78c82c); the recover path now lives in
        # recovery.py (_L4RecoveryMixin) and execution.py (_L4ExecutionMixin).
        "src/chaos_agent/l4/recovery.py",
        "src/chaos_agent/l4/execution.py",
    }
    allowed_checkpoint_builder_paths = {
        "src/chaos_agent/agent/state_mgmt/recovery_state.py",
        "src/chaos_agent/agent/result/task_snapshot.py",
        # Compatibility helper used by adapter unit tests and older SDK callers;
        # runtime L4 recover paths are guarded above through l4/agent.py.
        "src/chaos_agent/l4/adapter.py",
    }

    violations = []
    for rel in required_resolver_paths:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        if "resolve_recover_initial_state" not in text:
            violations.append(f"{rel}: missing resolve_recover_initial_state")

    for path in (PROJECT_ROOT / "src/chaos_agent").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if (
            "build_recover_initial_from_checkpoint" in text
            and rel not in allowed_checkpoint_builder_paths
        ):
            violations.append(f"{rel}: direct build_recover_initial_from_checkpoint")

    assert violations == []


@pytest.mark.asyncio
async def test_resolver_source_values_preserve_snapshot_verification(monkeypatch):
    """Recover graph stays clean while result/reporting source values keep inject facts."""

    from chaos_agent.agent.result import task_snapshot

    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "experiment_uid": "uid-from-store",
            "skill_name": "pod-cpu-fullload",
            "target": _target("demo"),
            "params": {"cpu-percent": "80"},
            "kubeconfig": "/snapshot/kubeconfig",
            "verification": {
                "level": "verified",
                "layer2": {"status": "passed", "details": "snapshot verification"},
            },
        },
        session={"messages": []},
        has_increment_log=False,
    )
    assert snapshot is not None

    async def fake_load_task_snapshot(task_id, *, tui_session_id=""):
        assert task_id == "task-inject"
        return snapshot

    monkeypatch.setattr(task_snapshot, "load_task_snapshot", fake_load_task_snapshot)

    resolution = await resolve_recover_initial_state(
        "task-inject",
        record_task_id="task-recover",
        checkpoint_values={
            "verification": {"level": "stale-checkpoint"},
            "messages": ["baseline-message"],
        },
    )

    assert resolution is not None
    assert resolution.initial_state["verification"] is None
    assert resolution.source_values["verification"] == snapshot.verification
    assert resolution.source_values["inject_verification_summary"] == (
        "Layer2=passed, Details=snapshot verification"
    )
    assert resolution.source_values["kubeconfig"] == "/snapshot/kubeconfig"
    assert resolution.source_values["messages"] == ["baseline-message"]


def test_rebuild_inject_verification_summary_includes_warnings():
    """Side-effect warnings are structural facts and must survive rebuild."""
    summary = _rebuild_inject_verification_summary({
        "layer2": {"status": "passed", "details": "fault active as planned"},
        "warnings": ["endpoint removed from svc", "readiness probe failing"],
    })
    assert "Layer2=passed" in summary
    assert "fault active as planned" in summary
    assert "Recorded side-effect warnings at injection" in summary
    assert "(1) endpoint removed from svc" in summary
    assert "(2) readiness probe failing" in summary


def test_rebuild_inject_verification_summary_no_warnings_unchanged():
    summary = _rebuild_inject_verification_summary({
        "layer2": {"status": "passed", "details": "ok"},
        "warnings": [],
    })
    assert summary == "Layer2=passed, Details=ok"


def test_task_snapshot_prefers_persisted_inject_context():
    """Durable-first: finalize-persisted inject_context beats record/message scan."""
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={"inject_context": "record context", "target": _target("p")},
        session={
            "result_summary": {"data": {"inject_context": "persisted context"}},
            "messages": [],
        },
        has_increment_log=True,
    )
    assert snapshot is not None
    assert snapshot.inject_context == "persisted context"


def test_task_snapshot_extracts_blast_radius_and_side_effects():
    session_data = {
        "blast_radius_detail": "session blast radius",
        "side_effects": {"endpoint_removals": ["svc-a"]},
    }
    record = {
        "target": _target("p"),
        "blast_radius_detail": "record blast radius",
        "side_effects": {"probe_failures": ["pod-a"]},
    }

    # increment-log branch: session (finalize-persisted) values win.
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record=record,
        session={"result_summary": {"data": session_data}, "messages": []},
        has_increment_log=True,
    )
    assert snapshot is not None
    assert snapshot.blast_radius_detail == "session blast radius"
    assert snapshot.side_effects == {"endpoint_removals": ["svc-a"]}

    # store-preferred branch: record values win.
    snapshot2 = TaskSnapshot.from_sources(
        task_id="task-inject",
        record=record,
        session={"result_summary": {"data": session_data}, "messages": []},
        has_increment_log=False,
    )
    assert snapshot2 is not None
    assert snapshot2.blast_radius_detail == "record blast radius"
    assert snapshot2.side_effects == {"probe_failures": ["pod-a"]}


@pytest.mark.asyncio
async def test_build_recover_initial_from_task_snapshot_carries_side_effects():
    snapshot = TaskSnapshot.from_sources(
        task_id="task-inject",
        record={
            "skill_name": "pod-network-loss",
            "target": _target("demo"),
            "params": {"percent": "100"},
            "inject_context": "ctx",
        },
        session={
            "result_summary": {
                "data": {
                    "blast_radius_detail": "2 replicas impacted",
                    "side_effects": {"endpoint_removals": ["svc-a"]},
                }
            },
            "messages": [],
        },
        has_increment_log=True,
    )
    assert snapshot is not None

    initial = await build_recover_initial_from_task_snapshot(
        snapshot, record_task_id="task-recover"
    )
    assert initial is not None
    assert initial["blast_radius_detail"] == "2 replicas impacted"
    assert initial["side_effects"] == {"endpoint_removals": ["svc-a"]}

    # Live checkpoint fills fields an older record lacks.
    initial2 = await build_recover_initial_from_task_snapshot(
        TaskSnapshot.from_sources(
            task_id="task-inject",
            record={
                "skill_name": "pod-network-loss",
                "target": _target("demo"),
                "params": {"percent": "100"},
                "inject_context": "ctx",
            },
            session={"messages": []},
            has_increment_log=False,
        ),
        record_task_id="task-recover",
        checkpoint_values={"blast_radius_detail": "checkpoint blast radius"},
    )
    assert initial2 is not None
    assert initial2["blast_radius_detail"] == "checkpoint blast radius"


# ---------------------------------------------------------------------------
# R4: attribution facts (injection_method / fault_handle) survive finalize
# persistence and feed recover hydration
# ---------------------------------------------------------------------------

class TestR4AttributionHydration:
    def test_session_attribution_wins_with_increment_log(self):
        native_handle = {"kind": "native", "method": "kubectl_native"}
        snapshot = TaskSnapshot.from_sources(
            task_id="task-inject",
            record={
                "injection_method": "kubectl_exec",
                "fault_handle": {"kind": "blade_uid", "value": "u", "method": "kubectl_exec"},
                "target": _target("p"),
            },
            session={
                "result_summary": {
                    "data": {
                        "injection_method": "kubectl_native",
                        "fault_handle": native_handle,
                    }
                },
                "messages": [],
            },
            has_increment_log=True,
        )
        assert snapshot is not None
        assert snapshot.injection_method == "kubectl_native"
        assert snapshot.fault_handle == native_handle

    def test_record_attribution_wins_without_increment_log(self):
        record_handle = {"kind": "blade_uid", "value": "uid-r", "method": "host_blade"}
        snapshot = TaskSnapshot.from_sources(
            task_id="task-inject",
            record={
                "injection_method": "host_blade",
                "fault_handle": record_handle,
                "target": _target("p"),
            },
            session={
                "result_summary": {
                    "data": {
                        "injection_method": "kubectl_exec",
                        "fault_handle": {"kind": "blade_uid", "value": "uid-s"},
                    }
                },
                "messages": [],
            },
            has_increment_log=False,
        )
        assert snapshot is not None
        assert snapshot.injection_method == "host_blade"
        assert snapshot.fault_handle == record_handle

    @pytest.mark.asyncio
    async def test_native_fault_handle_hydrates_recover_initial(self):
        """Old store row (no injection_method column) + finalize-persisted
        native attribution: the recover initial state still carries the
        handle — a UID-less fault is recoverable across a restart."""
        native_handle = {"kind": "native", "method": "kubectl_native"}
        snapshot = TaskSnapshot.from_sources(
            task_id="task-inject",
            record={
                "skill_name": "pod-replicas-scale",
                "target": _target("demo"),
                "params": {"replicas": "0"},
            },
            session={
                "result_summary": {
                    "data": {
                        "injection_method": "kubectl_native",
                        "fault_handle": native_handle,
                    }
                },
                "messages": [],
            },
            has_increment_log=True,
        )
        assert snapshot is not None
        assert snapshot.experiment_uid == ""

        initial = await build_recover_initial_from_task_snapshot(
            snapshot, record_task_id="task-recover"
        )
        assert initial is not None
        assert initial["injection_method"] == "kubectl_native"
        assert initial["fault_handle"] == native_handle
        assert initial["experiment_uid"] == ""

    def test_build_inject_data_persists_attribution_facts(self):
        """Supply side: finalize projection freezes the attribution facts into
        the persisted result-card data."""
        from chaos_agent.agent.result.operation_result import (
            build_inject_data_from_state,
        )

        data = build_inject_data_from_state(
            {"injection_method": "kubectl_native"}, "task-inject"
        )
        assert data["injection_method"] == "kubectl_native"
        assert data["fault_handle"] == {
            "kind": "native", "method": "kubectl_native"
        }
        assert data["experiment_uid"] == ""
        assert "blade_uid" not in data
