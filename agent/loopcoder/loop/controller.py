"""Main PDCA loop controller.

Glues together:
- plan + LLM client + tools (with hooks) + sandbox
- verifier (acceptance checks run OUTSIDE the LLM)
- snapshot manager (git tags per goal)
- session store (SQLite log)
- todo list (CC5)
- background jobs manager (CC12)
- system reminders (CC4)
- convention loader (CC14)
- sub-agent factory (CC7)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loopcoder.config import LoopCoderConfig
from loopcoder.events import Event, EventBus
from loopcoder.llm.client import LlmClient
from loopcoder.llm.context import ContextBuilder, ContextSection
from loopcoder.llm.prompts import (
    FAILURE_FEEDBACK_PROMPT,
    GIT_DIFF_PROMPT,
    GOAL_PROMPT,
    STRATEGY_CHANGE_PROMPT,
    SUBAGENT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    render,
)
from loopcoder.llm.tokens import TokenCounter
from loopcoder.loop.conventions import load_conventions
from loopcoder.loop.reminders import ReminderState, build_reminder
from loopcoder.loop.strategy import decide as decide_strategy
from loopcoder.loop.verifier import Verifier, VerificationResult
from loopcoder.plan.schema import Goal, Plan
from loopcoder.plan.topo import topological_order
from loopcoder.state.snapshot import SnapshotManager
from loopcoder.state.store import SessionStore
from loopcoder.tools.base import ToolContext
from loopcoder.tools.registry import ToolRegistry, default_registry
from loopcoder.tools.shell import BackgroundJobManager
from loopcoder.tools.todo import TodoList


INITIAL_TAG_NAME = "initial"


@dataclass
class GoalOutcome:
    goal_id: str
    status: str  # "passed" | "failed" | "skipped"
    iterations: int


class LoopController:
    def __init__(
        self,
        plan: Plan,
        config: LoopCoderConfig,
        client: LlmClient,
        sandbox: Any,
        store: SessionStore,
        registry: ToolRegistry | None = None,
        events: EventBus | None = None,
    ) -> None:
        self.plan = plan
        self.config = config
        self.client = client
        self.sandbox = sandbox
        self.store = store
        self.registry = registry or default_registry()
        self.tokens = TokenCounter(client.model)
        self.workspace = plan.project.workspace
        self.snap = SnapshotManager(self.workspace)
        self.verifier = Verifier(self.workspace, sandbox=sandbox)
        self.session_id: str = ""
        self.bg_jobs = BackgroundJobManager()
        self._conventions: list[Any] = []
        self._tool_ctx_cache: ToolContext | None = None
        self.events: EventBus = events or EventBus()
        self._stop_requested = False

    def request_stop(self) -> None:
        """Soft stop — finish current tool calls then exit."""
        self._stop_requested = True

    def _emit(self, type_: str, *, goal_id: str | None = None, iter_: int | None = None, **payload: Any) -> None:
        if self.events is None:
            return
        self.events.emit(
            Event(
                type=type_,
                session_id=self.session_id,
                goal_id=goal_id,
                iter=iter_,
                payload=payload,
            )
        )

    # ---------- ToolContext factory -----------------------------------------

    def _make_tool_ctx(self, goal_id: str) -> ToolContext:
        forbidden = list(self.config.tools.fs.forbidden_paths) + list(self.plan.constraints.forbidden_paths)
        allowed = self.plan.constraints.allowed_shell_commands or list(self.config.tools.shell.allowed_patterns)
        ctx = ToolContext(
            workspace_root=self.workspace,
            forbidden_paths=forbidden,
            allowed_shell_patterns=allowed,
            sandbox=self.sandbox,
            git_repo=self.snap.repo,
            extra={
                "fs_max_read_bytes": self.config.tools.fs.max_read_bytes,
                "shell_output_max_kb": self.config.tools.shell.output_max_kb,
            },
        )
        ctx.background_jobs = self.bg_jobs
        ctx.todo_list = TodoList(self.store, self.session_id, goal_id)
        ctx.spawn_agent = self._make_spawn_agent_factory(ctx)
        return ctx

    # ---------- entrypoint ---------------------------------------------------

    def run(self, only_goal: str | None = None, plan_path: str | None = None) -> list[GoalOutcome]:
        self.session_id = self.store.start_session(plan_path=plan_path)
        self._emit("session.started", project=self.plan.project.name, plan_path=plan_path)
        try:
            self._prepare_workspace()
            self._conventions = load_conventions(self.workspace)
            outcomes: list[GoalOutcome] = []
            ordered = topological_order(self.plan)
            if only_goal:
                ordered = [g for g in ordered if g.id == only_goal]
            session_start = time.monotonic()
            for goal in ordered:
                if self._stop_requested:
                    self._mark_skipped(goal, outcomes)
                    continue
                if self._budget_exceeded(session_start):
                    self._mark_skipped(goal, outcomes)
                    continue
                outcomes.append(self._run_goal(goal, session_start))
            status = "stopped" if self._stop_requested else "completed"
            self.store.end_session(self.session_id, status=status)
            self._emit("session.ended", status=status,
                       outcomes=[{"goal": o.goal_id, "status": o.status, "iters": o.iterations} for o in outcomes])
            return outcomes
        except Exception as e:
            self.store.end_session(self.session_id, status="error")
            self._emit("session.ended", status="error", error=str(e))
            raise

    # ---------- helpers ------------------------------------------------------

    def _prepare_workspace(self) -> None:
        Path(self.workspace).mkdir(parents=True, exist_ok=True)
        self.sandbox.prepare(self.workspace)
        self.snap.snapshot(self.session_id, goal_id=None, message="initial snapshot")

    def _mark_skipped(self, goal: Goal, outcomes: list[GoalOutcome]) -> None:
        self.store.start_goal(self.session_id, goal.id)
        self.store.end_goal(self.session_id, goal.id, status="skipped", iterations=0)
        outcomes.append(GoalOutcome(goal_id=goal.id, status="skipped", iterations=0))

    def _budget_exceeded(self, session_start: float) -> bool:
        if self.config.loop.max_total_minutes <= 0:
            return False
        return (time.monotonic() - session_start) > self.config.loop.max_total_minutes * 60

    # ---------- per-goal loop ------------------------------------------------

    def _run_goal(self, goal: Goal, session_start: float) -> GoalOutcome:
        self.store.start_goal(self.session_id, goal.id)
        self._emit("goal.started", goal_id=goal.id, title=goal.title,
                   acceptance_count=len(goal.acceptance))
        consecutive_failures = 0
        max_iter = self.plan.constraints.max_iterations_per_goal
        last_good_tag: str | None = None
        for prev in self._goal_history():
            if prev.get("status") == "passed":
                last_good_tag = f"loopcoder/{self.session_id}/{prev['goal_id']}"

        ctx = self._make_tool_ctx(goal.id)
        sys_section = ContextSection(
            kind="system",
            role="system",
            content=render(
                SYSTEM_PROMPT,
                forbidden_paths=ctx.forbidden_paths,
                allowed_shell_patterns=ctx.allowed_shell_patterns,
                network_allowed=self.plan.constraints.network_allowed,
            ),
        )
        pinned = self._load_pinned_files()
        goal_section = ContextSection(
            kind="goal",
            role="user",
            content=render(
                GOAL_PROMPT,
                goal=goal,
                file_tree=self._workspace_tree(),
                description=self.plan.context.description,
                conventions=self._conventions,
                pinned_files=pinned,
            ),
        )

        running_messages: list[ContextSection] = []
        last_verify: VerificationResult | None = None
        iter_ = 0

        while True:
            iter_ += 1
            if self._stop_requested:
                self.store.end_goal(self.session_id, goal.id, status="stopped", iterations=iter_ - 1)
                self._emit("goal.ended", goal_id=goal.id, status="stopped", iterations=iter_ - 1)
                return GoalOutcome(goal.id, "stopped", iter_ - 1)
            if max_iter > 0 and iter_ > max_iter:
                self.store.end_goal(self.session_id, goal.id, status="failed", iterations=iter_ - 1)
                self._emit("goal.ended", goal_id=goal.id, status="failed",
                           iterations=iter_ - 1, reason="max_iter")
                return GoalOutcome(goal.id, "failed", iter_ - 1)
            if self._budget_exceeded(session_start):
                self.store.end_goal(self.session_id, goal.id, status="failed", iterations=iter_ - 1)
                self._emit("goal.ended", goal_id=goal.id, status="failed",
                           iterations=iter_ - 1, reason="time_budget")
                return GoalOutcome(goal.id, "failed", iter_ - 1)

            iter_started = time.time()
            self._emit("iter.started", goal_id=goal.id, iter_=iter_)

            # Build context with budget enforcement
            builder = ContextBuilder(
                token_counter=self.tokens,
                total_budget_tokens=self.config.context.total_budget_tokens,
                reserve_for_completion=self.config.context.reserve_for_completion,
            )
            builder.add(sys_section)

            # CC4 — dynamic system reminder
            todo_items = ctx.todo_list.store.list_todos(self.session_id, goal.id) if ctx.todo_list else []
            in_progress = next((t["content"] for t in todo_items if t["status"] == "in_progress"), None)
            unread_writes = sorted(ctx.written_files - ctx.read_files)
            running_bg = sum(1 for j in self.bg_jobs.list() if not j.finished)
            reminder = build_reminder(
                ReminderState(
                    goal_id=goal.id,
                    acceptance_count=len(goal.acceptance),
                    consecutive_failures=consecutive_failures,
                    iteration=iter_,
                    max_iter=max_iter,
                    in_progress_todo=in_progress,
                    written_files_unread=unread_writes or None,
                    background_running=running_bg,
                )
            )
            builder.add(reminder)
            builder.add(goal_section)
            builder.add(
                ContextSection(
                    kind="git_diff",
                    role="user",
                    content=render(
                        GIT_DIFF_PROMPT,
                        diff=self.snap.diff_since(f"loopcoder/{self.session_id}/{INITIAL_TAG_NAME}"),
                    ),
                )
            )
            if last_verify is not None and not last_verify.passed:
                builder.add(
                    ContextSection(
                        kind="verify_log",
                        role="user",
                        content=render(FAILURE_FEEDBACK_PROMPT, verify_log=last_verify.log),
                    )
                )
            for sec in running_messages[-self.config.context.list_dir_max_entries :]:
                builder.add(sec)

            messages, _report = builder.pack()
            llm_response = self.client.chat(
                messages=messages,
                tools=self.registry.schemas(),
                temperature=self._effective_temperature(),
                top_p=self._effective_top_p(),
                max_completion_tokens=self._effective_max_completion(),
                parallel_tool_calls=True,
            )
            self.store.update_token_usage(
                self.session_id, llm_response.prompt_tokens, llm_response.completion_tokens
            )

            assistant_text = llm_response.content or ""
            running_messages.append(
                ContextSection(
                    kind="attempt",
                    role="assistant",
                    content=assistant_text,
                    meta={"tool_calls": [tc.__dict__ for tc in llm_response.tool_calls]},
                )
            )
            self.store.record_message(
                self.session_id, goal.id, iter_, ord_=0, role="assistant", content=assistant_text
            )

            # Execute each tool call in order
            ord_ = 1
            for tc in llm_response.tool_calls:
                if tc.name not in self.registry:
                    result_text = f"unknown tool: {tc.name}"
                    self.store.record_tool_call(
                        self.session_id, goal.id, iter_, ord_,
                        tool_name=tc.name, params=tc.arguments, result={"error": result_text},
                        duration_ms=0,
                    )
                    running_messages.append(
                        ContextSection(kind="attempt", role="tool", content=result_text,
                                       tool_call_id=tc.id, name=tc.name)
                    )
                    ord_ += 1
                    continue
                try:
                    args = tc.parse()
                except Exception as e:
                    args = {}
                    result_text = f"invalid arguments: {e}"
                    self.store.record_tool_call(
                        self.session_id, goal.id, iter_, ord_,
                        tool_name=tc.name, params=tc.arguments, result={"error": result_text},
                        duration_ms=0,
                    )
                    running_messages.append(
                        ContextSection(kind="attempt", role="tool", content=result_text,
                                       tool_call_id=tc.id, name=tc.name)
                    )
                    ord_ += 1
                    continue
                result = self.registry.call(tc.name, args, ctx)
                self.store.record_tool_call(
                    self.session_id, goal.id, iter_, ord_,
                    tool_name=tc.name, params=args,
                    result={"ok": result.ok, "output_len": len(result.output)},
                    duration_ms=result.duration_ms,
                )
                self._emit("tool.called", goal_id=goal.id, iter_=iter_,
                           tool=tc.name, ok=result.ok, duration_ms=result.duration_ms)
                running_messages.append(
                    ContextSection(kind="attempt", role="tool", content=result.output,
                                   tool_call_id=tc.id, name=tc.name)
                )
                ord_ += 1

            # Verify (always, regardless of submit_goal)
            verify = self.verifier.run(goal.acceptance)
            self.store.record_iteration(
                session_id=self.session_id,
                goal_id=goal.id,
                iter_=iter_,
                llm_request_id=None,
                prompt_tokens=llm_response.prompt_tokens,
                completion_tokens=llm_response.completion_tokens,
                verify_passed=verify.passed,
                verify_log=verify.log,
                started_at=iter_started,
                ended_at=time.time(),
            )

            self._emit("iter.ended", goal_id=goal.id, iter_=iter_,
                       verify_passed=verify.passed, summary=verify.short_summary())

            if verify.passed:
                self.snap.snapshot(self.session_id, goal.id, message=f"goal {goal.id} passed")
                self.store.end_goal(self.session_id, goal.id, status="passed", iterations=iter_)
                self._emit("goal.ended", goal_id=goal.id, status="passed", iterations=iter_)
                # Clean up background jobs that survived
                for j in self.bg_jobs.list():
                    if not j.finished:
                        self.bg_jobs.kill(j.id)
                return GoalOutcome(goal.id, "passed", iter_)

            consecutive_failures += 1
            last_verify = verify
            self._emit("verify.failed", goal_id=goal.id, iter_=iter_,
                       consecutive=consecutive_failures, log_excerpt=verify.log[-512:])

            action = decide_strategy(
                consecutive_failures,
                strategy_change_after=self.config.loop.strategy_change_after,
                rollback_after=self.config.loop.rollback_after,
                last_good_tag=last_good_tag,
            )
            if action.revert_to_tag is not None:
                self.snap.revert(action.revert_to_tag)
                consecutive_failures = 0
                running_messages.clear()
                ctx.read_files.clear()
                ctx.written_files.clear()
            elif action.inject_strategy_change:
                running_messages.append(
                    ContextSection(
                        kind="attempt",
                        role="user",
                        content=render(STRATEGY_CHANGE_PROMPT, failures=consecutive_failures),
                    )
                )

    # ---------- LLM-effective params (plan overrides config) -----------------

    def _effective_temperature(self) -> float:
        return self.plan.llm.temperature if self.plan.llm.temperature is not None else self.config.llm.temperature

    def _effective_top_p(self) -> float:
        return self.plan.llm.top_p if self.plan.llm.top_p is not None else self.config.llm.top_p

    def _effective_max_completion(self) -> int:
        return (
            self.plan.llm.max_completion_tokens
            if self.plan.llm.max_completion_tokens is not None
            else self.config.llm.max_completion_tokens
        )

    # ---------- workspace introspection --------------------------------------

    def _load_pinned_files(self) -> list[dict[str, Any]]:
        pinned: list[dict[str, Any]] = []
        seen: set[str] = set()
        # plan-level pins first, then config-level always_pin
        candidates: list[str] = [pin.path for pin in self.plan.context.pin_in_context]
        candidates.extend(self.config.context.always_pin)
        for raw in candidates:
            if raw in seen:
                continue
            p = Path(self.workspace) / raw
            if not p.is_file():
                continue
            try:
                txt = p.read_text(errors="replace")
            except Exception:
                continue
            seen.add(raw)
            pinned.append({
                "path": raw,
                "content": txt[: self.config.context.per_file_max_kb * 1024],
                "lang": _lang(p),
            })
        return pinned

    def _workspace_tree(self, max_entries: int = 200, max_depth: int = 2) -> str:
        ws = Path(self.workspace)
        out: list[str] = []
        for root, dirs, files in os.walk(ws):
            depth = len(Path(root).relative_to(ws).parts)
            if depth > max_depth:
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for d in dirs:
                out.append(os.path.relpath(os.path.join(root, d), ws) + "/")
                if len(out) >= max_entries:
                    return "\n".join(out)
            for f in sorted(files):
                if f.startswith("."):
                    continue
                out.append(os.path.relpath(os.path.join(root, f), ws))
                if len(out) >= max_entries:
                    return "\n".join(out)
        return "\n".join(out)

    def _goal_history(self) -> list[dict[str, Any]]:
        return self.store.goals_for(self.session_id)

    # ---------- sub-agent factory (CC7) --------------------------------------

    def _make_spawn_agent_factory(self, parent_ctx: ToolContext):  # type: ignore[no-untyped-def]
        client = self.client
        registry = self.registry

        def factory(task: str, allowed_tools: list[str]) -> str:
            sub_messages = [
                {"role": "system", "content": render(SUBAGENT_SYSTEM_PROMPT)},
                {"role": "user", "content": task},
            ]
            # Filter tools to allowed set
            sub_tools = [s for s in registry.schemas() if s["function"]["name"] in allowed_tools]
            # Sub-agent runs up to 8 internal turns
            for _ in range(8):
                resp = client.chat(
                    messages=sub_messages,
                    tools=sub_tools or None,
                    temperature=0.2,
                    top_p=0.95,
                    max_completion_tokens=4096,
                    parallel_tool_calls=True,
                )
                if not resp.tool_calls:
                    return resp.content or "(no response)"
                # Append assistant turn
                sub_messages.append(
                    {"role": "assistant", "content": resp.content or ""}
                )
                for tc in resp.tool_calls:
                    if tc.name not in registry:
                        sub_messages.append({
                            "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                            "content": f"unknown tool: {tc.name}",
                        })
                        continue
                    try:
                        args = tc.parse()
                    except Exception as e:
                        sub_messages.append({
                            "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                            "content": f"invalid args: {e}",
                        })
                        continue
                    result = registry.call(tc.name, args, parent_ctx)
                    sub_messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                        "content": result.output[:8192],
                    })
            return "(sub-agent ran out of turns without producing a final answer)"

        return factory


def _lang(p: Path) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
        ".rs": "rust", ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
        ".sh": "bash", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
        ".md": "markdown", ".toml": "toml",
    }.get(p.suffix.lower(), "")
