"""SQLite-backed session store.

Schemas (see PLAN §5.8):
- sessions: id, plan_path, started_at, ended_at, status, total_tokens, total_cost_seconds
- goals: session_id, goal_id, status, iterations, started_at, ended_at
- iterations: session_id, goal_id, iter, llm_request_id, prompt_tokens, completion_tokens,
              verify_passed, verify_log
- tool_calls: session_id, goal_id, iter, ord, tool_name, params_json, result_json, duration_ms
- messages: session_id, goal_id, iter, ord, role, content, tool_call_id

Keep this module purely about persistence. Higher-level orchestration lives in loop/.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    plan_path TEXT,
    started_at REAL,
    ended_at REAL,
    status TEXT,
    total_prompt_tokens INTEGER DEFAULT 0,
    total_completion_tokens INTEGER DEFAULT 0,
    total_seconds REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
    session_id TEXT,
    goal_id TEXT,
    status TEXT,
    iterations INTEGER DEFAULT 0,
    started_at REAL,
    ended_at REAL,
    PRIMARY KEY (session_id, goal_id)
);

CREATE TABLE IF NOT EXISTS iterations (
    session_id TEXT,
    goal_id TEXT,
    iter INTEGER,
    llm_request_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    verify_passed INTEGER,
    verify_log TEXT,
    started_at REAL,
    ended_at REAL,
    PRIMARY KEY (session_id, goal_id, iter)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    session_id TEXT,
    goal_id TEXT,
    iter INTEGER,
    ord INTEGER,
    tool_name TEXT,
    params_json TEXT,
    result_json TEXT,
    duration_ms INTEGER,
    PRIMARY KEY (session_id, goal_id, iter, ord)
);

CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT,
    goal_id TEXT,
    iter INTEGER,
    ord INTEGER,
    role TEXT,
    content TEXT,
    tool_call_id TEXT,
    PRIMARY KEY (session_id, goal_id, iter, ord)
);

CREATE TABLE IF NOT EXISTS todos (
    session_id TEXT,
    goal_id TEXT,
    todo_id TEXT,
    content TEXT,
    status TEXT,
    active_form TEXT,
    created_at REAL,
    updated_at REAL,
    PRIMARY KEY (session_id, goal_id, todo_id)
);
"""


@dataclass
class IterationRecord:
    iter: int
    prompt_tokens: int
    completion_tokens: int
    verify_passed: bool | None
    verify_log: str | None


class SessionStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(
                f"cannot create state dir {Path(self.db_path).parent}: {e}. "
                "Either run as root, or set LOOPCODER_YAML to a config with a "
                "user-writable storage.state_db path."
            ) from e
        with self._conn() as c:
            c.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- session lifecycle ----------

    def start_session(self, plan_path: str | None = None) -> str:
        sid = uuid.uuid4().hex[:12]
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(id, plan_path, started_at, status) VALUES (?,?,?,?)",
                (sid, plan_path, now, "running"),
            )
        return sid

    def end_session(self, session_id: str, status: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "UPDATE sessions SET ended_at=?, status=? WHERE id=?",
                (now, status, session_id),
            )

    def update_token_usage(
        self, session_id: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE sessions
                       SET total_prompt_tokens = total_prompt_tokens + ?,
                           total_completion_tokens = total_completion_tokens + ?
                       WHERE id = ?""",
                (prompt_tokens, completion_tokens, session_id),
            )

    # ---------- goals ----------

    def start_goal(self, session_id: str, goal_id: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO goals(session_id, goal_id, status, iterations, started_at) VALUES (?,?,?,?,?)",
                (session_id, goal_id, "running", 0, now),
            )

    def end_goal(self, session_id: str, goal_id: str, status: str, iterations: int) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "UPDATE goals SET status=?, iterations=?, ended_at=? WHERE session_id=? AND goal_id=?",
                (status, iterations, now, session_id, goal_id),
            )

    # ---------- iterations / tool_calls / messages ----------

    def record_iteration(
        self,
        session_id: str,
        goal_id: str,
        iter_: int,
        llm_request_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        verify_passed: bool | None,
        verify_log: str | None,
        started_at: float,
        ended_at: float,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO iterations
                   (session_id, goal_id, iter, llm_request_id, prompt_tokens, completion_tokens,
                    verify_passed, verify_log, started_at, ended_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    goal_id,
                    iter_,
                    llm_request_id,
                    prompt_tokens,
                    completion_tokens,
                    None if verify_passed is None else int(verify_passed),
                    verify_log,
                    started_at,
                    ended_at,
                ),
            )

    def record_tool_call(
        self,
        session_id: str,
        goal_id: str,
        iter_: int,
        ord_: int,
        tool_name: str,
        params: Any,
        result: Any,
        duration_ms: int,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO tool_calls
                   (session_id, goal_id, iter, ord, tool_name, params_json, result_json, duration_ms)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    goal_id,
                    iter_,
                    ord_,
                    tool_name,
                    json.dumps(params, default=str),
                    json.dumps(result, default=str),
                    duration_ms,
                ),
            )

    def record_message(
        self,
        session_id: str,
        goal_id: str,
        iter_: int,
        ord_: int,
        role: str,
        content: str,
        tool_call_id: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO messages
                   (session_id, goal_id, iter, ord, role, content, tool_call_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (session_id, goal_id, iter_, ord_, role, content, tool_call_id),
            )

    # ---------- queries ----------

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT id, plan_path, started_at, ended_at, status FROM sessions ORDER BY started_at DESC"
            )
            return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

    def session_status(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
            row = cur.fetchone()
            if row is None:
                return None
            keys = [d[0] for d in cur.description]
            return dict(zip(keys, row))

    def goals_for(self, session_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM goals WHERE session_id=? ORDER BY started_at",
                (session_id,),
            )
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]

    def iterations_for(self, session_id: str, goal_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM iterations WHERE session_id=? AND goal_id=? ORDER BY iter",
                (session_id, goal_id),
            )
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]

    # ---------- todos (CC5) ----------

    def upsert_todo(
        self,
        session_id: str,
        goal_id: str,
        todo_id: str,
        content: str,
        status: str,
        active_form: str | None = None,
    ) -> None:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "SELECT created_at FROM todos WHERE session_id=? AND goal_id=? AND todo_id=?",
                (session_id, goal_id, todo_id),
            )
            row = cur.fetchone()
            created = row[0] if row else now
            c.execute(
                """INSERT OR REPLACE INTO todos
                   (session_id, goal_id, todo_id, content, status, active_form, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, goal_id, todo_id, content, status, active_form, created, now),
            )

    def list_todos(self, session_id: str, goal_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM todos WHERE session_id=? AND goal_id=? ORDER BY created_at",
                (session_id, goal_id),
            )
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]

    def clear_todos(self, session_id: str, goal_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "DELETE FROM todos WHERE session_id=? AND goal_id=?",
                (session_id, goal_id),
            )
