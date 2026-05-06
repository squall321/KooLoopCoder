"""HTTP API tests using FastAPI's TestClient."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loopcoder.api.server import build_app
from loopcoder.state.store import SessionStore


def _dev_cfg(tmp_path: Path) -> Path:
    cfg = tmp_path / "loopcoder.yaml"
    cfg.write_text(f"""
storage:
  state_db: {tmp_path}/state.db
  log_dir: {tmp_path}/logs
  workspaces_root: {tmp_path}/ws
sandbox:
  backend: host
""")
    return cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = _dev_cfg(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    monkeypatch.delenv("LOOPCODER_API_KEY", raising=False)
    app = build_app(str(cfg))
    return TestClient(app)


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "version" in r.json()


def test_list_tools_returns_all(client):
    r = client.get("/v1/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    # core tools should all be present
    for required in ["read_file", "write_file", "edit_file", "run_shell", "todo_write", "spawn_agent"]:
        assert required in names


def test_tool_call_direct(client, tmp_path):
    # Create a workspace + a file
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("hi\n")
    body = {
        "name": "read_file",
        "arguments": {"path": "hello.txt"},
        "workspace": str(ws),
    }
    r = client.post("/v1/tools/read_file", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert "hi" in j["output"]


def test_tool_unknown(client, tmp_path):
    body = {
        "name": "no_such_tool",
        "arguments": {},
        "workspace": str(tmp_path),
    }
    r = client.post("/v1/tools/no_such_tool", json=body)
    assert r.status_code == 404


def test_list_sessions_empty(client):
    r = client.get("/v1/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_get_session_404(client):
    r = client.get("/v1/sessions/nonexistent")
    assert r.status_code == 404


def test_session_iterations_via_db(client, tmp_path, monkeypatch):
    # Pre-populate DB so we can hit the read endpoints without running a session
    cfg = tmp_path / "loopcoder.yaml"
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    db = str(tmp_path / "state.db")
    store = SessionStore(db)
    sid = store.start_session(plan_path="x")
    store.start_goal(sid, "g1")
    store.record_iteration(sid, "g1", 1, None, 50, 25, True, "PASS", 1.0, 2.0)
    store.end_goal(sid, "g1", "passed", 1)
    store.end_session(sid, "completed")

    r = client.get(f"/v1/sessions/{sid}/iterations/g1")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["iter"] == 1
    assert body[0]["verify_passed"] is True


def test_auth_blocks_when_token_set(tmp_path, monkeypatch):
    cfg = _dev_cfg(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    monkeypatch.setenv("LOOPCODER_API_KEY", "shh-secret")
    app = build_app(str(cfg))
    c = TestClient(app)
    r = c.get("/v1/tools")
    assert r.status_code == 401
    r2 = c.get("/v1/tools", headers={"Authorization": "Bearer shh-secret"})
    assert r2.status_code == 200


def test_session_report_for_known_session(client, tmp_path, monkeypatch):
    cfg = tmp_path / "loopcoder.yaml"
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    db = str(tmp_path / "state.db")
    store = SessionStore(db)
    sid = store.start_session(plan_path="x")
    store.end_session(sid, "completed")
    r = client.get(f"/v1/sessions/{sid}/report")
    assert r.status_code == 200
    assert "LoopCoder" in r.text or sid in r.text


def test_export_tarball(client, tmp_path, monkeypatch):
    cfg = tmp_path / "loopcoder.yaml"
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    db = str(tmp_path / "state.db")
    store = SessionStore(db)
    sid = store.start_session(plan_path="x")
    store.end_session(sid, "completed")
    r = client.get(f"/v1/sessions/{sid}/export.tar.gz")
    assert r.status_code == 200
    assert r.content[:2] == b"\x1f\x8b"  # gzip magic
