"""Tests for ``GET /api/v1/sessions`` — the live-session list backing
the web UI's multi-session sidebar.

Sessions are seeded directly into the in-memory ``SessionStore`` (via
``SessionStore.create``), NOT via ``POST /sessions``: the HTTP create
handler also writes to the on-disk TuiSessionStore and the task DB,
neither of which belongs in a route-shape test. What this file locks
down is the list contract — field subset (no internals leak), ordering
(newest first), and consistency with delete.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaos_agent.server.routes.sessions import (
    CreateSessionRequest,
    get_store,
    sessions_router,
)


@pytest.fixture
def client():
    """Minimal app with just the sessions router, and a CLEAN global
    store — it's a module-level singleton, so without explicit cleanup
    sessions would leak across tests within this process."""
    store = get_store()
    store._items.clear()
    app = FastAPI()
    app.include_router(sessions_router)
    yield TestClient(app)
    store._items.clear()


def test_list_empty(client: TestClient):
    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "total": 0}


def test_list_returns_reduced_fields_newest_first(client: TestClient):
    store = get_store()
    older = store.create(CreateSessionRequest(cluster="c1", namespace="ns1"))
    newer = store.create(CreateSessionRequest(cluster="c2", namespace="ns2"))
    # Force a deterministic order — both creates land within the same
    # second, and created_at resolution is seconds-level.
    store._items[older]["created_at"] = "2026-08-18 10:00:00"
    store._items[newer]["created_at"] = "2026-08-18 11:00:00"
    store.add_task(older, "task-1")

    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [s["id"] for s in body["sessions"]] == [newer, older]

    row = body["sessions"][1]  # the older one, with the task
    assert row == {
        "id": older,
        "cluster": "c1",
        "namespace": "ns1",
        "model_name": row["model_name"],  # settings-derived, don't pin
        "created_at": "2026-08-18 10:00:00",
        "task_count": 1,
    }
    # Internals must not leak into the list payload.
    assert "conversation_thread_id" not in row
    assert "task_ids" not in row
    assert "first_turn_done" not in row


def test_list_reflects_delete(client: TestClient):
    store = get_store()
    sid = store.create(CreateSessionRequest())
    assert client.get("/api/v1/sessions").json()["total"] == 1

    store.delete(sid)
    assert client.get("/api/v1/sessions").json() == {"sessions": [], "total": 0}
