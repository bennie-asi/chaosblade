"""Contract tests for GET /api/v1/recordings — error-code semantics.

Locks down the fix for the OSError branch that used to return
``SERVER_SHUTTING_DOWN`` (5001) on a plain file-read failure: the code is
now ``INTERNAL_ERROR`` (5099), so clients never mistake a disk hiccup for
a shutdown signal. Also pins the path-traversal guard (charset gate →
unified TASK_NOT_FOUND) and the happy path including malformed-line skip.
"""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaos_agent.config.settings import settings


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    """Bare app + recordings router; memory_dir redirected to tmp_path."""
    monkeypatch.setattr(settings, "memory_dir", tmp_path / "memory")
    app = FastAPI()
    from chaos_agent.server.routes.recordings import recordings_router

    app.include_router(recordings_router)
    return TestClient(app)


def _write_recording(task_id: str, lines: list[dict]) -> None:
    rec_dir = settings.resolved_memory_dir / "recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    (rec_dir / f"{task_id}.jsonl").write_text(body, encoding="utf-8")


class TestRecordingRead:
    def test_valid_recording_returned(self, test_client, tmp_path):
        _write_recording(
            "task-ok",
            [{"ts": 1, "type": "UserInput", "data": {"text": "hi"}}],
        )
        resp = test_client.get("/api/v1/recordings/task-ok")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["task_id"] == "task-ok"
        assert data["data"]["total"] == 1
        assert data["data"]["events"][0]["type"] == "UserInput"

    def test_malformed_line_skipped_not_fatal(self, test_client, tmp_path):
        rec_dir = settings.resolved_memory_dir / "recordings"
        rec_dir.mkdir(parents=True, exist_ok=True)
        (rec_dir / "task-mixed.jsonl").write_text(
            '{"ts": 1, "type": "A"}\n'
            "not json at all\n"
            '{"ts": 2, "type": "B"}\n',
            encoding="utf-8",
        )
        resp = test_client.get("/api/v1/recordings/task-mixed")
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 2

    def test_missing_recording_is_not_found(self, test_client):
        resp = test_client.get("/api/v1/recordings/task-nope")
        assert resp.status_code == 200
        assert resp.json()["code"] == 2001  # TASK_NOT_FOUND


class TestTraversalGuard:
    @pytest.mark.parametrize(
        "task_id",
        ["..", "task..id", "a.b"],
    )
    def test_illegal_charset_rejected_as_not_found(self, test_client, task_id):
        # ``/`` would need an extra path segment — only single-segment ids
        # reach the handler; everything else collapses to TASK_NOT_FOUND so no
        # existence information leaks.
        url = f"/api/v1/recordings/{task_id}"
        resp = test_client.get(url)
        if resp.status_code == 404:
            return  # FastAPI rejected the path shape itself — equally safe
        assert resp.json()["code"] == 2001

    @pytest.mark.parametrize(
        "task_id",
        ["bad/id", "id\x00x", "../../etc/passwd"],
    )
    def test_path_shaped_ids_blocked_at_gate(self, task_id):
        # These can't traverse a single URL path segment, so they are
        # asserted directly against the resolver instead of via HTTP.
        from chaos_agent.server.routes.recordings import _safe_recording_path

        assert _safe_recording_path(task_id) is None


class TestOSErrorMapping:
    def test_read_failure_is_internal_error_not_shutdown(
        self, test_client, tmp_path
    ):
        """The regression this file exists for: an OSError during the read
        must surface as INTERNAL_ERROR (5099), never SERVER_SHUTTING_DOWN
        (5001) — the latter would route clients into their shutdown /
        retry-later branch for a plain disk failure."""
        _write_recording("task-io", [{"ts": 1, "type": "A"}])
        with patch(
            "pathlib.Path.open", side_effect=OSError("disk I/O error")
        ):
            resp = test_client.get("/api/v1/recordings/task-io")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 5099  # INTERNAL_ERROR
        assert data["code"] != 5001  # NOT SERVER_SHUTTING_DOWN
        assert "disk I/O error" in data["message"]
