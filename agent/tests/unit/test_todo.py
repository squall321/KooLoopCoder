"""Tests for TodoWrite/TodoRead (CC5)."""

from pathlib import Path

import pytest

from loopcoder.state.store import SessionStore
from loopcoder.tools.base import ToolContext
from loopcoder.tools.todo import TodoList, TodoWriteTool, TodoReadTool, TodoWriteParams


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "s.db")


def _ctx(tmp_path: Path, store: SessionStore, sid: str, gid: str) -> ToolContext:
    ctx = ToolContext(workspace_root=str(tmp_path), allowed_shell_patterns=["*"])
    ctx.todo_list = TodoList(store, sid, gid)
    return ctx


def test_todo_replace_and_render(tmp_path: Path):
    store = _store(tmp_path)
    sid = store.start_session(plan_path=None)
    store.start_goal(sid, "g1")
    ctx = _ctx(tmp_path, store, sid, "g1")
    tool = TodoWriteTool()
    params = tool.parse_params({
        "todos": [
            {"id": "t1", "content": "Read README", "status": "completed"},
            {"id": "t2", "content": "Write tests", "status": "in_progress",
             "active_form": "Writing tests"},
            {"id": "t3", "content": "Refactor", "status": "pending"},
        ]
    })
    result = tool.execute(params, ctx)
    assert result.ok
    assert "[x]" in result.output  # completed
    assert "[~]" in result.output  # in_progress
    assert "[ ]" in result.output  # pending
    items = store.list_todos(sid, "g1")
    assert len(items) == 3
    assert sum(1 for t in items if t["status"] == "in_progress") == 1


def test_todo_two_in_progress_rejected():
    with pytest.raises(Exception):
        TodoWriteParams.model_validate({
            "todos": [
                {"id": "t1", "content": "a", "status": "in_progress"},
                {"id": "t2", "content": "b", "status": "in_progress"},
            ]
        })


def test_todo_duplicate_ids_rejected():
    with pytest.raises(Exception):
        TodoWriteParams.model_validate({
            "todos": [
                {"id": "t1", "content": "a"},
                {"id": "t1", "content": "b"},
            ]
        })


def test_todo_replace_clears_old(tmp_path: Path):
    store = _store(tmp_path)
    sid = store.start_session(plan_path=None)
    ctx = _ctx(tmp_path, store, sid, "g1")
    tool = TodoWriteTool()
    tool.execute(tool.parse_params({"todos": [{"id": "t1", "content": "a"}]}), ctx)
    tool.execute(tool.parse_params({"todos": [{"id": "t2", "content": "b"}]}), ctx)
    items = store.list_todos(sid, "g1")
    ids = [t["todo_id"] for t in items]
    assert ids == ["t2"]


def test_todo_read(tmp_path: Path):
    store = _store(tmp_path)
    sid = store.start_session(plan_path=None)
    ctx = _ctx(tmp_path, store, sid, "g1")
    TodoWriteTool().execute(
        TodoWriteTool().parse_params({"todos": [{"id": "t1", "content": "x"}]}), ctx
    )
    rt = TodoReadTool()
    result = rt.execute(rt.parse_params({}), ctx)
    assert result.ok
    assert "t1" in result.output
