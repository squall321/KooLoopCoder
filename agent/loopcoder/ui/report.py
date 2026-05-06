"""Markdown report generator for a finished session."""

from __future__ import annotations

from datetime import datetime
from io import StringIO

from loopcoder.state.store import SessionStore


def generate_report(store: SessionStore, session_id: str) -> str:
    sess = store.session_status(session_id)
    if sess is None:
        return f"# Session {session_id} — not found"

    out = StringIO()
    started = _fmt(sess.get("started_at"))
    ended = _fmt(sess.get("ended_at"))
    out.write(f"# LoopCoder report — session `{session_id}`\n\n")
    out.write(f"- Plan: `{sess.get('plan_path')}`\n")
    out.write(f"- Started: {started}\n")
    out.write(f"- Ended: {ended}\n")
    out.write(f"- Status: **{sess.get('status')}**\n")
    out.write(
        f"- Tokens: prompt={sess.get('total_prompt_tokens', 0)}, "
        f"completion={sess.get('total_completion_tokens', 0)}\n\n"
    )

    out.write("## Goals\n\n")
    for goal in store.goals_for(session_id):
        gid = goal["goal_id"]
        out.write(f"### Goal `{gid}` — {goal['status']}\n\n")
        out.write(f"- Iterations: {goal.get('iterations')}\n")
        out.write(f"- Started: {_fmt(goal.get('started_at'))}\n")
        out.write(f"- Ended: {_fmt(goal.get('ended_at'))}\n\n")
        iters = store.iterations_for(session_id, gid)
        if iters:
            out.write("| iter | tokens (p/c) | verify | log (excerpt) |\n")
            out.write("|---|---|---|---|\n")
            for it in iters:
                vlog = (it.get("verify_log") or "").splitlines()
                excerpt = vlog[-1] if vlog else ""
                out.write(
                    f"| {it['iter']} "
                    f"| {it.get('prompt_tokens',0)}/{it.get('completion_tokens',0)} "
                    f"| {'PASS' if it.get('verify_passed') else 'FAIL'} "
                    f"| `{excerpt[:120]}` |\n"
                )
            out.write("\n")
    return out.getvalue()


def _fmt(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
