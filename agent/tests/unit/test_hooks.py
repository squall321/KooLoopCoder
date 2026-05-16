"""Tests for tool hook system (CC6)."""

from pathlib import Path

import pytest

from loopcoder.tools.base import ToolContext, ToolError, ToolResult
from loopcoder.tools.hooks import HookRegistry
from loopcoder.tools.registry import default_registry


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_root=str(tmp_path), allowed_shell_patterns=["*"])


def test_hook_registry_dispatches(tmp_path: Path):
    h = HookRegistry()
    seen: list[tuple[str, str]] = []
    h.on_pre("write_file", lambda n, p, c: seen.append(("pre", n)))
    h.on_post("write_file", lambda n, p, r, c: seen.append(("post", n)))
    h.on_pre_any(lambda n, p, c: seen.append(("pre*", n)))
    ctx = _ctx(tmp_path)
    h.run_pre("write_file", {"path": "x"}, ctx)
    h.run_post("write_file", {"path": "x"}, ToolResult(ok=True, output=""), ctx)
    assert ("pre*", "write_file") in seen
    assert ("pre", "write_file") in seen
    assert ("post", "write_file") in seen


def test_default_hooks_record_reads_writes(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hi\n")
    reg = default_registry()
    ctx = _ctx(tmp_path)
    reg.call("read_file", {"path": "f.txt"}, ctx)
    assert "f.txt" in ctx.read_files
    reg.call("write_file", {"path": "f.txt", "content": "ho\n"}, ctx)
    assert "f.txt" in ctx.written_files


def test_pre_hook_can_veto():
    h = HookRegistry()
    def deny(n, p, c):
        raise ToolError("nope")
    h.on_pre("xtool", deny)
    with pytest.raises(ToolError):
        h.run_pre("xtool", {}, _ctx(Path("/tmp")))


def test_registry_pre_hook_failure_returns_failed_result(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hi\n")
    reg = default_registry()
    ctx = _ctx(tmp_path)
    # write without prior read on existing file -> blocked
    result = reg.call("write_file", {"path": "f.txt", "content": "ho"}, ctx)
    assert not result.ok
    assert "read" in result.output.lower()
