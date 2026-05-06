"""Filesystem tools: read, write, edit, list, grep, find_files, apply_patch."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


# ---------- helpers ----------------------------------------------------------------


def _resolve_in_workspace(ctx: ToolContext, path: str) -> Path:
    """Resolve ``path`` under ctx.workspace_root, refusing escapes."""
    root = Path(ctx.workspace_root).resolve()
    p = (root / path).resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise ValueError(f"path escapes workspace: {path!r}") from e
    return p


def _is_forbidden(ctx: ToolContext, path: str) -> bool:
    """Match path against patterns. Supports glob with '**/' prefix.

    Strategies tried for each pattern:
      1. Direct ``fnmatch(path, pattern)``.
      2. If pattern starts with ``**/``, also match against basename and
         every suffix of the path. This makes ``**/.env`` match ``.env``,
         ``a/.env``, and ``a/b/.env``.
      3. Plain absolute-prefix match for patterns ending with ``/**`` (eg.
         ``/etc/**`` matches ``/etc/foo``).
    """
    parts = path.split(os.sep)
    suffixes = [os.sep.join(parts[i:]) for i in range(len(parts))]
    for pat in ctx.forbidden_paths:
        if fnmatch.fnmatch(path, pat):
            return True
        if pat.startswith("**/"):
            tail = pat[3:]
            if any(fnmatch.fnmatch(s, tail) for s in suffixes):
                return True
        if pat.endswith("/**"):
            head = pat[:-3]
            if path == head or path.startswith(head + os.sep):
                return True
    return False


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    if len(text.encode()) <= max_bytes:
        return text, False
    # Cut at character boundary safely.
    encoded = text.encode()
    cut = encoded[:max_bytes]
    return cut.decode(errors="ignore") + f"\n\n[...truncated, {len(encoded) - max_bytes} bytes omitted]", True


# ---------- read_file ----------------------------------------------------------------


class ReadFileParams(BaseModel):
    path: str = Field(..., description="Path relative to workspace root.")
    offset: int = Field(0, ge=0, description="0-based starting line.")
    limit: int = Field(
        2000,
        ge=1,
        le=20000,
        description="Maximum number of lines to return. Default 2000.",
    )


def _format_with_line_numbers(lines: list[str], start_offset: int) -> str:
    """Format like ``cat -n``: 6-digit right-aligned line number + tab + content."""
    out_parts = []
    for i, line in enumerate(lines):
        ln = start_offset + i + 1
        # Strip trailing newline so output uses our own newline boundaries
        content = line.rstrip("\n")
        out_parts.append(f"{ln:>6}\t{content}")
    return "\n".join(out_parts)


class ReadFileTool(Tool):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read a file. Output uses 'cat -n' style: '   <line>\\t<content>'. "
        "Use offset/limit to page through large files. Required before edit_file/write_file "
        "on existing files."
    )
    ParamsModel: ClassVar[type[BaseModel]] = ReadFileParams

    def execute(self, params: ReadFileParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if _is_forbidden(ctx, params.path):
            return ToolResult(ok=False, output=f"path forbidden by policy: {params.path}")
        try:
            p = _resolve_in_workspace(ctx, params.path)
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        if not p.is_file():
            return ToolResult(ok=False, output=f"not a file: {params.path}")
        try:
            text = p.read_text(errors="replace")
        except Exception as e:
            return ToolResult(ok=False, output=f"read failed: {e}")
        all_lines = text.splitlines()
        total = len(all_lines)
        if params.offset >= total and total > 0:
            return ToolResult(ok=True, output=f"[file has {total} lines; offset {params.offset} is past end]")
        sliced = all_lines[params.offset : params.offset + params.limit]
        body = _format_with_line_numbers(sliced, params.offset)
        max_bytes = int(ctx.extra.get("fs_max_read_bytes", 1_048_576))
        body, trunc_bytes = _truncate(body, max_bytes)
        end_line = params.offset + len(sliced)
        more = total > end_line
        header = f"[file: {params.path}, total {total} lines, showing {params.offset + 1}-{end_line}{'; more available' if more else ''}]"
        return ToolResult(
            ok=True,
            output=f"{header}\n{body}",
            truncated=trunc_bytes or more,
            data={"path": str(p), "total_lines": total, "shown_range": [params.offset + 1, end_line]},
        )


class ReadFilesParams(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=20)
    limit_per_file: int = Field(500, ge=1, le=5000)


class ReadFilesTool(Tool):
    name: ClassVar[str] = "read_files"
    description: ClassVar[str] = (
        "Batch-read several files at once. Returns each file with its line-numbered "
        "content. Use this instead of multiple read_file calls to save round-trips."
    )
    ParamsModel: ClassVar[type[BaseModel]] = ReadFilesParams

    def execute(self, params: ReadFilesParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        chunks: list[str] = []
        any_ok = False
        for raw_path in params.paths:
            if _is_forbidden(ctx, raw_path):
                chunks.append(f"### {raw_path}\n[forbidden]")
                continue
            try:
                p = _resolve_in_workspace(ctx, raw_path)
            except ValueError as e:
                chunks.append(f"### {raw_path}\n[{e}]")
                continue
            if not p.is_file():
                chunks.append(f"### {raw_path}\n[not a file]")
                continue
            try:
                text = p.read_text(errors="replace")
            except Exception as e:
                chunks.append(f"### {raw_path}\n[read failed: {e}]")
                continue
            any_ok = True
            lines = text.splitlines()[: params.limit_per_file]
            body = _format_with_line_numbers(lines, 0)
            chunks.append(f"### {raw_path} (lines 1-{len(lines)}, total {len(text.splitlines())})\n{body}")
            # Track that we read each path so write_file/edit_file pass the hook check.
            ctx.read_files.add(raw_path)
        return ToolResult(ok=any_ok, output="\n\n".join(chunks), data={"paths": params.paths})


# ---------- list_dir ----------------------------------------------------------------


class ListDirParams(BaseModel):
    path: str = Field("", description="Directory under workspace, default = root.")
    max_depth: int = 2
    max_entries: int = 200


class ListDirTool(Tool):
    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = "List files and directories under workspace path (limited tree)."
    ParamsModel: ClassVar[type[BaseModel]] = ListDirParams

    def execute(self, params: ListDirParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            base = _resolve_in_workspace(ctx, params.path)
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        if not base.is_dir():
            return ToolResult(ok=False, output=f"not a directory: {params.path}")
        entries: list[str] = []
        truncated = False
        for root, dirs, files in os.walk(base):
            depth = len(Path(root).relative_to(base).parts)
            if depth > params.max_depth:
                dirs[:] = []
                continue
            # Filter forbidden
            dirs[:] = [d for d in dirs if not _is_forbidden(ctx, os.path.relpath(os.path.join(root, d), ctx.workspace_root))]
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), ctx.workspace_root)
                entries.append(rel + "/")
                if len(entries) >= params.max_entries:
                    truncated = True
                    break
            if truncated:
                break
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), ctx.workspace_root)
                if _is_forbidden(ctx, rel):
                    continue
                entries.append(rel)
                if len(entries) >= params.max_entries:
                    truncated = True
                    break
            if truncated:
                break
        return ToolResult(ok=True, output="\n".join(entries), truncated=truncated, data=entries)


# ---------- grep ----------------------------------------------------------------


class GrepParams(BaseModel):
    pattern: str = Field(..., description="Python regex.")
    path: str = ""
    max_results: int = 200


class GrepTool(Tool):
    name: ClassVar[str] = "grep"
    description: ClassVar[str] = "Search regex pattern in workspace files. Returns matches with file:line."
    ParamsModel: ClassVar[type[BaseModel]] = GrepParams

    def execute(self, params: GrepParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            base = _resolve_in_workspace(ctx, params.path)
            regex = re.compile(params.pattern)
        except (ValueError, re.error) as e:
            return ToolResult(ok=False, output=f"invalid: {e}")
        results: list[str] = []
        truncated = False
        for p in base.rglob("*") if base.is_dir() else [base]:
            if not p.is_file():
                continue
            rel = os.path.relpath(p, ctx.workspace_root)
            if _is_forbidden(ctx, rel):
                continue
            try:
                with p.open("r", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if regex.search(line):
                            results.append(f"{rel}:{i}:{line.rstrip()}")
                            if len(results) >= params.max_results:
                                truncated = True
                                break
            except Exception:
                continue
            if truncated:
                break
        return ToolResult(ok=True, output="\n".join(results), truncated=truncated, data=results)


# ---------- find_files ----------------------------------------------------------------


class FindFilesParams(BaseModel):
    glob: str = Field(..., description="Glob like '**/*.py'.")
    max_results: int = 200


class FindFilesTool(Tool):
    name: ClassVar[str] = "find_files"
    description: ClassVar[str] = "Find files matching a glob under workspace."
    ParamsModel: ClassVar[type[BaseModel]] = FindFilesParams

    def execute(self, params: FindFilesParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        root = Path(ctx.workspace_root).resolve()
        results: list[str] = []
        for p in root.glob(params.glob):
            if not p.is_file():
                continue
            rel = os.path.relpath(p, root)
            if _is_forbidden(ctx, rel):
                continue
            results.append(rel)
            if len(results) >= params.max_results:
                break
        return ToolResult(ok=True, output="\n".join(results), data=results)


# ---------- write_file ----------------------------------------------------------------


class WriteFileParams(BaseModel):
    path: str
    content: str
    create_parents: bool = True


class WriteFileTool(Tool):
    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = "Create or overwrite a file. Auto-stages for git."
    ParamsModel: ClassVar[type[BaseModel]] = WriteFileParams

    def execute(self, params: WriteFileParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if _is_forbidden(ctx, params.path):
            return ToolResult(ok=False, output=f"path forbidden by policy: {params.path}")
        try:
            p = _resolve_in_workspace(ctx, params.path)
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        if params.create_parents:
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(params.content)
        # git-add is handled by the post-hook in tools/hooks.py
        return ToolResult(ok=True, output=f"wrote {params.path} ({len(params.content)} bytes)")


# ---------- edit_file (unique-match replace) -------------------------------


class EditFileParams(BaseModel):
    path: str
    old: str = Field(..., description="Existing text to replace. Must be unique in file.")
    new: str
    replace_all: bool = False


class EditFileTool(Tool):
    name: ClassVar[str] = "edit_file"
    description: ClassVar[str] = (
        "Replace exact text 'old' with 'new'. Refuses if 'old' is not unique unless replace_all."
    )
    ParamsModel: ClassVar[type[BaseModel]] = EditFileParams

    def execute(self, params: EditFileParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if _is_forbidden(ctx, params.path):
            return ToolResult(ok=False, output=f"path forbidden by policy: {params.path}")
        try:
            p = _resolve_in_workspace(ctx, params.path)
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        if not p.is_file():
            return ToolResult(ok=False, output=f"not a file: {params.path}")
        text = p.read_text()
        if not params.old:
            return ToolResult(ok=False, output="'old' must be non-empty")
        count = text.count(params.old)
        if count == 0:
            return ToolResult(ok=False, output=f"'old' not found in {params.path}")
        if count > 1 and not params.replace_all:
            return ToolResult(
                ok=False,
                output=f"'old' matches {count} times in {params.path}; use replace_all or expand context",
            )
        new_text = text.replace(params.old, params.new) if params.replace_all else text.replace(
            params.old, params.new, 1
        )
        p.write_text(new_text)
        # git-add is handled by the post-hook
        return ToolResult(ok=True, output=f"edited {params.path} ({count} replacement{'s' if count > 1 else ''})")


# ---------- apply_patch ----------------------------------------------------------------


class ApplyPatchParams(BaseModel):
    patch: str = Field(..., description="Unified diff text.")


class ApplyPatchTool(Tool):
    name: ClassVar[str] = "apply_patch"
    description: ClassVar[str] = "Apply a unified diff to the workspace."
    ParamsModel: ClassVar[type[BaseModel]] = ApplyPatchParams

    def execute(self, params: ApplyPatchParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        import subprocess
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=params.patch,
            text=True,
            cwd=ctx.workspace_root,
            capture_output=True,
        )
        if proc.returncode != 0:
            return ToolResult(ok=False, output=f"patch failed:\n{proc.stderr}")
        # git-add is handled by the post-hook
        return ToolResult(ok=True, output="patch applied")
