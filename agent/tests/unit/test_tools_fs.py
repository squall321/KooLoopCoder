"""Tests for filesystem tools (CC1, CC2, CC3 — read/edit/write + line numbers + read-before-write)."""

from pathlib import Path

import pytest

from loopcoder.tools.base import ToolContext
from loopcoder.tools.fs import (
    EditFileTool,
    FindFilesTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    ReadFilesTool,
    WriteFileTool,
)
from loopcoder.tools.registry import default_registry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import os\nprint('hi')\n")
    (tmp_path / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "README.md").write_text("# project\n")
    (tmp_path / ".env").write_text("SECRET=top\n")
    return tmp_path


def _ctx(workspace: Path, **kw) -> ToolContext:
    return ToolContext(
        workspace_root=str(workspace),
        forbidden_paths=kw.pop("forbidden_paths", ["**/.env"]),
        allowed_shell_patterns=["*"],
        **kw,
    )


def test_read_file_with_line_numbers(workspace: Path):
    tool = ReadFileTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"path": "src/main.py"}), ctx)
    assert result.ok
    # Output should have a header and cat -n style line numbers
    assert "src/main.py" in result.output
    assert "\t" in result.output  # tab between line number and content
    assert "import os" in result.output
    # Line number format
    assert "     1" in result.output or "1\t" in result.output


def test_read_file_offset_and_limit(workspace: Path):
    big = workspace / "big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(1, 101)) + "\n")
    tool = ReadFileTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"path": "big.txt", "offset": 50, "limit": 10}), ctx)
    assert result.ok
    assert "line 51" in result.output  # 1-based
    assert "line 60" in result.output
    assert "line 1\n" not in result.output


def test_read_file_forbidden(workspace: Path):
    tool = ReadFileTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"path": ".env"}), ctx)
    assert not result.ok
    assert "forbidden" in result.output.lower()


def test_read_file_outside_workspace(workspace: Path):
    tool = ReadFileTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"path": "../../etc/passwd"}), ctx)
    assert not result.ok


def test_read_files_batch(workspace: Path):
    tool = ReadFilesTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"paths": ["src/main.py", "src/util.py", "missing.py"]}), ctx)
    assert result.ok
    assert "main.py" in result.output
    assert "util.py" in result.output
    assert "missing.py" in result.output  # listed as not-found
    # CC3: paths are recorded in read_files set
    assert "src/main.py" in ctx.read_files
    assert "src/util.py" in ctx.read_files


def test_edit_file_unique_match(workspace: Path):
    # Read first to satisfy hook
    ctx = _ctx(workspace)
    ReadFileTool().execute(ReadFileTool().parse_params({"path": "src/main.py"}), ctx)
    ctx.read_files.add("src/main.py")  # hook would do this; do it directly here
    tool = EditFileTool()
    result = tool.execute(tool.parse_params({"path": "src/main.py", "old": "print('hi')", "new": "print('bye')"}), ctx)
    assert result.ok
    assert "print('bye')" in (workspace / "src" / "main.py").read_text()


def test_edit_file_ambiguous_match_refused(workspace: Path):
    (workspace / "dup.py").write_text("x=1\nx=1\n")
    ctx = _ctx(workspace)
    ctx.read_files.add("dup.py")
    tool = EditFileTool()
    result = tool.execute(tool.parse_params({"path": "dup.py", "old": "x=1", "new": "x=2"}), ctx)
    assert not result.ok
    assert "matches" in result.output.lower()


def test_edit_file_replace_all(workspace: Path):
    (workspace / "dup.py").write_text("x=1\nx=1\n")
    ctx = _ctx(workspace)
    ctx.read_files.add("dup.py")
    tool = EditFileTool()
    result = tool.execute(
        tool.parse_params({"path": "dup.py", "old": "x=1", "new": "x=2", "replace_all": True}),
        ctx,
    )
    assert result.ok
    assert (workspace / "dup.py").read_text() == "x=2\nx=2\n"


def test_write_file_creates(workspace: Path):
    tool = WriteFileTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"path": "new/created.py", "content": "x = 1\n"}), ctx)
    assert result.ok
    assert (workspace / "new" / "created.py").read_text() == "x = 1\n"


def test_grep(workspace: Path):
    tool = GrepTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"pattern": "import"}), ctx)
    assert result.ok
    assert "src/main.py" in result.output


def test_grep_skips_forbidden(workspace: Path):
    tool = GrepTool()
    ctx = _ctx(workspace, forbidden_paths=["**/.env", "**/main.py"])
    result = tool.execute(tool.parse_params({"pattern": "."}), ctx)
    assert "main.py" not in result.output


def test_find_files(workspace: Path):
    tool = FindFilesTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({"glob": "**/*.py"}), ctx)
    assert result.ok
    paths = result.output.splitlines()
    assert any(p.endswith("main.py") for p in paths)


def test_list_dir(workspace: Path):
    tool = ListDirTool()
    ctx = _ctx(workspace)
    result = tool.execute(tool.parse_params({}), ctx)
    assert result.ok
    assert any("src" in line for line in result.output.splitlines())


def test_read_before_write_hook_blocks_overwrite(workspace: Path):
    """CC3 — write_file on an existing file requires prior read."""
    reg = default_registry()
    ctx = _ctx(workspace, sandbox=None)
    # Direct write without read should be blocked by the pre-hook
    result = reg.call("write_file", {"path": "README.md", "content": "# changed\n"}, ctx)
    assert not result.ok
    assert "read_file" in result.output
    # After reading, write succeeds
    reg.call("read_file", {"path": "README.md"}, ctx)
    result2 = reg.call("write_file", {"path": "README.md", "content": "# changed\n"}, ctx)
    assert result2.ok


def test_read_before_write_does_not_block_new_files(workspace: Path):
    reg = default_registry()
    ctx = _ctx(workspace)
    result = reg.call("write_file", {"path": "fresh.py", "content": "x=1\n"}, ctx)
    assert result.ok


def test_post_hook_records_reads_and_writes(workspace: Path):
    reg = default_registry()
    ctx = _ctx(workspace)
    reg.call("read_file", {"path": "src/main.py"}, ctx)
    reg.call("write_file", {"path": "src/main.py", "content": "print('updated')\n"}, ctx)
    assert "src/main.py" in ctx.read_files
    assert "src/main.py" in ctx.written_files
