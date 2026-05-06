"""Sub-agent tool (CC7).

Sometimes the main agent needs a heavy exploration task ("find every place
that constructs a User object", "audit all db migrations") that would burn
through context if done inline. ``spawn_agent`` opens a fresh LLM session
with a separate system prompt, a restricted tool set (read-only by default),
and returns *only the final answer* to the caller. The full sub-session
trace is still persisted in SQLite for post-hoc inspection.

The sub-agent is created by a callable bound onto ``ctx.spawn_agent`` —
typically by ``LoopController``. Tests can supply a mock factory.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


# A factory function: (task: str, allowed_tools: list[str]) -> sub-agent result string
SpawnAgentFn = Callable[[str, list[str]], str]


class SpawnAgentParams(BaseModel):
    task: str = Field(
        ...,
        description=(
            "The exact task for the sub-agent. Be specific. Sub-agent does not see "
            "your conversation, so include all relevant context."
        ),
    )
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "read_files", "list_dir", "grep", "find_files", "git_log", "git_diff"],
        description="Read-only tools by default. Add 'run_shell' / 'run_tests' only when essential.",
    )
    expected_output: str = Field(
        "concise summary",
        description="Briefly describe what kind of answer you want back.",
    )


class SpawnAgentTool(Tool):
    name: ClassVar[str] = "spawn_agent"
    description: ClassVar[str] = (
        "Delegate a heavy investigation to a sub-agent that runs in its own "
        "context. Use this for codebase searches, deep audits, or other tasks "
        "that would otherwise pollute your main context. The sub-agent returns "
        "a single text result; intermediate steps are saved to the session log."
    )
    ParamsModel: ClassVar[type[BaseModel]] = SpawnAgentParams

    def execute(self, params: SpawnAgentParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        spawn: SpawnAgentFn | None = ctx.spawn_agent
        if spawn is None:
            return ToolResult(ok=False, output="sub-agent factory not configured in this context")
        try:
            result = spawn(params.task, params.allowed_tools)
        except Exception as e:
            return ToolResult(ok=False, output=f"sub-agent failed: {e}")
        return ToolResult(ok=True, output=result, data={"task": params.task[:200]})
