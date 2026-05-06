"""Tool registry: holds Tool instances, exposes OpenAI-format schemas, dispatches calls."""

from __future__ import annotations

import json
import time
from typing import Any

from loopcoder.tools.base import Tool, ToolContext, ToolError, ToolResult
from loopcoder.tools.hooks import HookRegistry, default_hooks


class ToolRegistry:
    def __init__(self, hooks: HookRegistry | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.hooks: HookRegistry = hooks or HookRegistry()

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"Tool {type(tool).__name__} has empty name")
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def call(self, name: str, raw_args: str | dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate args + run pre-hooks + execute + run post-hooks.

        Always returns a ToolResult (no exceptions propagate). Pre-hooks may
        veto the call by raising ``ToolError``.
        """
        tool = self.get(name)
        start = time.monotonic()
        try:
            args_dict = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            params = tool.parse_params(args_dict)
        except json.JSONDecodeError as e:
            return ToolResult(
                ok=False,
                output=f"invalid JSON arguments for {name}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # validation / type errors
            return ToolResult(
                ok=False,
                output=f"invalid arguments for {name}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        params_dump = params.model_dump()

        # Pre-hooks
        try:
            self.hooks.run_pre(name, params_dump, ctx)
        except ToolError as e:
            return ToolResult(
                ok=False,
                output=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            result = tool.execute(params, ctx)
        except Exception as e:  # tool bug
            result = ToolResult(ok=False, output=f"tool {name!r} crashed: {e!r}")
        result.duration_ms = result.duration_ms or int((time.monotonic() - start) * 1000)

        # Post-hooks (do not change ok/output, but observe & record)
        try:
            self.hooks.run_post(name, params_dump, result, ctx)
        except Exception:
            pass  # post hook bugs must not break the loop
        return result


def default_registry() -> ToolRegistry:
    """Construct a registry with all built-in tools + default hooks."""
    from loopcoder.tools.fs import (
        ReadFileTool,
        ReadFilesTool,
        WriteFileTool,
        EditFileTool,
        ListDirTool,
        GrepTool,
        FindFilesTool,
        ApplyPatchTool,
    )
    from loopcoder.tools.shell import (
        RunShellTool,
        RunShellBackgroundTool,
        ReadBackgroundOutputTool,
        KillBackgroundJobTool,
        ListBackgroundJobsTool,
    )
    from loopcoder.tools.git import GitStatusTool, GitDiffTool, GitLogTool, RevertToSnapshotTool
    from loopcoder.tools.tests import RunTestsTool
    from loopcoder.tools.meta import RecordThoughtTool, SubmitGoalTool
    from loopcoder.tools.todo import TodoWriteTool, TodoReadTool
    from loopcoder.tools.spawn_agent import SpawnAgentTool

    reg = ToolRegistry(hooks=default_hooks())
    for tool in [
        ReadFileTool(),
        ReadFilesTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        GrepTool(),
        FindFilesTool(),
        ApplyPatchTool(),
        RunShellTool(),
        RunShellBackgroundTool(),
        ReadBackgroundOutputTool(),
        KillBackgroundJobTool(),
        ListBackgroundJobsTool(),
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
        RevertToSnapshotTool(),
        RunTestsTool(),
        RecordThoughtTool(),
        SubmitGoalTool(),
        TodoWriteTool(),
        TodoReadTool(),
        SpawnAgentTool(),
    ]:
        reg.register(tool)
    return reg
