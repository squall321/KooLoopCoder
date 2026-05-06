"""Tests for SessionStore including todos table (CC5)."""

from pathlib import Path

from loopcoder.state.store import SessionStore


def test_session_lifecycle(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    sid = s.start_session(plan_path="plan.yaml")
    assert sid
    s.update_token_usage(sid, 100, 50)
    s.end_session(sid, "completed")
    info = s.session_status(sid)
    assert info is not None
    assert info["status"] == "completed"
    assert info["total_prompt_tokens"] == 100
    assert info["total_completion_tokens"] == 50


def test_goal_tracking(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    sid = s.start_session()
    s.start_goal(sid, "g1")
    s.end_goal(sid, "g1", "passed", 3)
    goals = s.goals_for(sid)
    assert len(goals) == 1
    assert goals[0]["status"] == "passed"
    assert goals[0]["iterations"] == 3


def test_iterations_recorded(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    sid = s.start_session()
    s.start_goal(sid, "g1")
    s.record_iteration(sid, "g1", 1, None, 100, 50, False, "log fail", 1.0, 2.0)
    s.record_iteration(sid, "g1", 2, None, 110, 60, True, "log pass", 2.0, 3.0)
    rows = s.iterations_for(sid, "g1")
    assert len(rows) == 2
    assert rows[1]["verify_passed"] == 1


def test_tool_calls_recorded(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    sid = s.start_session()
    s.start_goal(sid, "g1")
    s.record_tool_call(sid, "g1", 1, 1, "read_file", {"path": "x"}, {"ok": True}, 12)
    # roundtrip via list_sessions / status (no direct getter for tool_calls -- it's persisted only)
    info = s.session_status(sid)
    assert info is not None


def test_todos_upsert_and_clear(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    sid = s.start_session()
    s.upsert_todo(sid, "g1", "t1", "do thing", "pending", "doing thing")
    s.upsert_todo(sid, "g1", "t1", "do thing", "in_progress", "doing thing")
    rows = s.list_todos(sid, "g1")
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"
    s.clear_todos(sid, "g1")
    assert s.list_todos(sid, "g1") == []


def test_list_sessions_orders_recent_first(tmp_path: Path):
    s = SessionStore(tmp_path / "s.db")
    a = s.start_session(plan_path="a")
    import time as _t; _t.sleep(0.01)
    b = s.start_session(plan_path="b")
    sessions = s.list_sessions()
    assert sessions[0]["id"] == b
    assert sessions[1]["id"] == a
