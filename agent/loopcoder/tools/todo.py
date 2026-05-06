"""TodoWrite / TodoRead — internal task tracker for the LLM (CC5).

Distinct from the project-wide PROGRESS.md (user-facing). This is the
agent's own scratch list for the *current goal*: helps it break work into
steps and reflect on progress. Persisted in SQLite so resume/replay works.

Invariants enforced:
- exactly one ``in_progress`` task at a time (or zero)
- valid statuses: ``pending`` / ``in_progress`` / ``completed`` / ``cancelled``
- writing the list always REPLACES the entire goal's todo set, mirroring how
  Claude Code's TodoWrite expects the model to send the full updated state.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from loopcoder.tools.base import Tool, ToolContext, ToolResult


TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]


class TodoItem(BaseModel):
    id: str = Field(..., description="Stable id for this todo across updates.")
    content: str = Field(..., description="Imperative form, e.g. 'Add JWT decode helper'.")
    active_form: str | None = Field(
        None,
        description="Continuous form shown while in_progress, e.g. 'Adding JWT decode helper'.",
    )
    status: TodoStatus = "pending"


class TodoWriteParams(BaseModel):
    todos: list[TodoItem]

    @model_validator(mode="after")
    def _at_most_one_in_progress(self) -> "TodoWriteParams":
        n = sum(1 for t in self.todos if t.status == "in_progress")
        if n > 1:
            raise ValueError(f"only one todo may be 'in_progress' at a time (got {n})")
        ids = [t.id for t in self.todos]
        dups = {x for x in ids if ids.count(x) > 1}
        if dups:
            raise ValueError(f"duplicate todo ids: {sorted(dups)}")
        return self


class TodoList:
    """In-memory cache of todos for the active goal.

    Owned by the LoopController; bound onto ToolContext so tools can mutate it.
    The store is the source of truth — this class just keeps a snapshot to
    render in system reminders without an extra DB hit per iter.
    """

    def __init__(self, store, session_id: str, goal_id: str) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.session_id = session_id
        self.goal_id = goal_id
        self.items: list[dict] = []

    def replace(self, todos: list[TodoItem]) -> None:
        self.store.clear_todos(self.session_id, self.goal_id)
        for t in todos:
            self.store.upsert_todo(
                self.session_id,
                self.goal_id,
                t.id,
                t.content,
                t.status,
                t.active_form,
            )
        self.items = self.store.list_todos(self.session_id, self.goal_id)

    def render(self) -> str:
        if not self.items:
            return "(no todos)"
        out: list[str] = []
        for t in self.items:
            mark = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}.get(
                t["status"], "[?]"
            )
            out.append(f"{mark} {t['todo_id']}: {t['content']}")
        return "\n".join(out)


class TodoWriteTool(Tool):
    name: ClassVar[str] = "todo_write"
    description: ClassVar[str] = (
        "Replace the goal's todo list. Send the COMPLETE updated list every time "
        "(not a delta). Use this to plan and track multi-step work. "
        "Mark exactly one item 'in_progress' while working on it."
    )
    ParamsModel: ClassVar[type[BaseModel]] = TodoWriteParams

    def execute(self, params: TodoWriteParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        todo_list: TodoList | None = ctx.todo_list
        if todo_list is None:
            return ToolResult(ok=False, output="todo_list not available in this context")
        todo_list.replace(params.todos)
        return ToolResult(ok=True, output=f"todos updated:\n{todo_list.render()}")


class _Empty(BaseModel):
    pass


class TodoReadTool(Tool):
    name: ClassVar[str] = "todo_read"
    description: ClassVar[str] = "Read the current todo list for this goal."
    ParamsModel: ClassVar[type[BaseModel]] = _Empty

    def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        todo_list: TodoList | None = ctx.todo_list
        if todo_list is None:
            return ToolResult(ok=False, output="todo_list not available")
        # Refresh from store
        todo_list.items = todo_list.store.list_todos(todo_list.session_id, todo_list.goal_id)
        return ToolResult(ok=True, output=todo_list.render())
