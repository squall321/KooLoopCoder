"""Dynamic system-reminder builder (CC4).

Every iteration we prepend a fresh ``system`` message with the rules that
the model should hold in mind right now. These are short, imperative, and
crucially: they reflect *current state* (token usage, consecutive failures,
in-progress todo) so the model can adapt.

Why a separate channel rather than baking everything into the main system
prompt?
- The system prompt is cached for prefix-caching wins; we don't want it to
  change every iter.
- Reminders are short and per-iter, which keeps cache invalidation local.
- The model gives recent system messages high attention, exactly where we
  want our key constraints to live.

This module produces ``ContextSection`` objects ready for the ContextBuilder.
"""

from __future__ import annotations

from dataclasses import dataclass

from loopcoder.llm.context import ContextSection


@dataclass
class ReminderState:
    goal_id: str
    acceptance_count: int
    consecutive_failures: int
    iteration: int
    max_iter: int  # 0 = unbounded
    used_tokens: int | None = None
    budget_tokens: int | None = None
    in_progress_todo: str | None = None
    written_files_unread: list[str] | None = None
    background_running: int = 0


_RULES = [
    "Verification runs OUTSIDE this conversation. submit_goal alone is not enough — only acceptance checks decide.",
    "Read a file before you write or edit it. Do not re-read files you have already read this iteration.",
    "Verification logs are NOT truncated. Read the failure output to the end before deciding what to change.",
    "Match work to the user's request. Do not add features, abstractions, or files that were not asked for.",
    "If the same approach has failed twice, stop and try a genuinely different one.",
    "Use todo_write to plan multi-step work. Keep at most one item in_progress.",
    "Prefer edit_file with unique context over rewriting whole files. Keep diffs small.",
    "For long commands (>30s), use run_shell_background and poll with read_background_output.",
]


def build_reminder(state: ReminderState) -> ContextSection:
    parts: list[str] = []
    parts.append("<state>")
    parts.append(f"  goal: {state.goal_id}  iter: {state.iteration}" + (f"/{state.max_iter}" if state.max_iter else ""))
    parts.append(f"  acceptance checks: {state.acceptance_count}")
    if state.consecutive_failures > 0:
        parts.append(f"  consecutive failures: {state.consecutive_failures}")
    if state.used_tokens is not None and state.budget_tokens:
        pct = int(100 * state.used_tokens / state.budget_tokens)
        parts.append(f"  context: {state.used_tokens}/{state.budget_tokens} tokens ({pct}%)")
    if state.in_progress_todo:
        parts.append(f"  in_progress todo: {state.in_progress_todo}")
    if state.background_running:
        parts.append(f"  background jobs running: {state.background_running}")
    if state.written_files_unread:
        parts.append("  WARNING: files written but not re-read since: " + ", ".join(state.written_files_unread[:5]))
    parts.append("</state>")
    parts.append("")
    parts.append("<rules>")
    for r in _RULES:
        parts.append(f"- {r}")
    parts.append("</rules>")
    return ContextSection(kind="goal", role="system", content="\n".join(parts))
