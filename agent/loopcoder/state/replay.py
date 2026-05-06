"""Replay (read-only): reconstruct what happened in a session for inspection."""

from __future__ import annotations

from typing import Any

from loopcoder.state.store import SessionStore


def replay(store: SessionStore, session_id: str, until_iter: int | None = None) -> list[dict[str, Any]]:
    """Return iteration records (with messages and tool calls), in chronological order.

    If ``until_iter`` is provided, stop at the first goal/iter that exceeds it.
    """
    out: list[dict[str, Any]] = []
    for goal in store.goals_for(session_id):
        gid = goal["goal_id"]
        for it in store.iterations_for(session_id, gid):
            if until_iter is not None and it["iter"] > until_iter:
                continue
            out.append({"goal": goal, "iteration": it})
    return out
