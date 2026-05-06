"""Pre/Post tool-use hook system (CC6).

Registered hook callables run before and after every tool execution. They
can short-circuit a call by raising ``ToolError`` (which the registry
converts into ``ToolResult(ok=False, ...)``), or simply observe and mutate
the ToolContext (e.g. add a path to ``read_files``).

Default hooks (registered by ``default_hooks``):
- pre(write_file/edit_file/apply_patch): require prior read of target path (CC3)
- post(read_file): record path in ctx.read_files (CC3)
- post(write_file/edit_file/apply_patch): record in ctx.written_files + git add
- post(*): no-op for tools without a hook
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Callable

from loopcoder.tools.base import ToolContext, ToolError, ToolResult


PreHook = Callable[[str, dict[str, Any], ToolContext], None]
PostHook = Callable[[str, dict[str, Any], ToolResult, ToolContext], None]


class HookRegistry:
    def __init__(self) -> None:
        self._pre: dict[str, list[PreHook]] = defaultdict(list)
        self._post: dict[str, list[PostHook]] = defaultdict(list)
        self._pre_any: list[PreHook] = []
        self._post_any: list[PostHook] = []

    def on_pre(self, tool_name: str, fn: PreHook) -> None:
        self._pre[tool_name].append(fn)

    def on_post(self, tool_name: str, fn: PostHook) -> None:
        self._post[tool_name].append(fn)

    def on_pre_any(self, fn: PreHook) -> None:
        self._pre_any.append(fn)

    def on_post_any(self, fn: PostHook) -> None:
        self._post_any.append(fn)

    def run_pre(self, tool_name: str, params: dict[str, Any], ctx: ToolContext) -> None:
        for h in self._pre_any:
            h(tool_name, params, ctx)
        for h in self._pre.get(tool_name, []):
            h(tool_name, params, ctx)

    def run_post(
        self, tool_name: str, params: dict[str, Any], result: ToolResult, ctx: ToolContext
    ) -> None:
        for h in self._post_any:
            h(tool_name, params, result, ctx)
        for h in self._post.get(tool_name, []):
            h(tool_name, params, result, ctx)


# ---------- default hooks ----------


def _norm(path: str, ctx: ToolContext) -> str:
    """Normalize a path for tracking (relative to workspace root, no '..')."""
    abs_ = os.path.normpath(os.path.join(ctx.workspace_root, path))
    try:
        return os.path.relpath(abs_, ctx.workspace_root)
    except ValueError:
        return path


def require_read_before_write(name: str, params: dict[str, Any], ctx: ToolContext) -> None:
    path = params.get("path")
    if not path:
        return
    rel = _norm(path, ctx)
    abs_ = os.path.normpath(os.path.join(ctx.workspace_root, rel))
    if not os.path.exists(abs_):
        return  # creating a new file is fine
    if rel not in ctx.read_files:
        raise ToolError(
            f"You must read_file({rel!r}) before {name}() on an existing file. "
            "This prevents accidental overwrites."
        )


def record_read(name: str, params: dict[str, Any], result: ToolResult, ctx: ToolContext) -> None:
    if not result.ok:
        return
    path = params.get("path")
    if path:
        ctx.read_files.add(_norm(path, ctx))


def record_write_and_git_add(
    name: str, params: dict[str, Any], result: ToolResult, ctx: ToolContext
) -> None:
    if not result.ok:
        return
    path = params.get("path")
    if path:
        rel = _norm(path, ctx)
        ctx.written_files.add(rel)
        # Stage automatically so git_diff reflects work-in-progress
        if ctx.git_repo is not None:
            try:
                ctx.git_repo.index.add([os.path.join(ctx.workspace_root, rel)])
            except Exception:
                pass
    elif name == "apply_patch":
        if ctx.git_repo is not None:
            try:
                ctx.git_repo.git.add(A=True)
            except Exception:
                pass


def default_hooks() -> HookRegistry:
    h = HookRegistry()
    # Pre: enforce read-before-write (CC3)
    for tool in ("write_file", "edit_file"):
        h.on_pre(tool, require_read_before_write)
    # Post: track reads
    h.on_post("read_file", record_read)
    h.on_post("read_files", record_read)
    # Post: track writes + git add
    for tool in ("write_file", "edit_file", "apply_patch"):
        h.on_post(tool, record_write_and_git_add)
    return h
