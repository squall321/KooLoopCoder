"""Tests for run_shell + background job tools (CC11, CC12)."""

import time
from pathlib import Path

from loopcoder.tools.base import ToolContext
from loopcoder.tools.registry import default_registry
from loopcoder.tools.shell import BackgroundJobManager


def _ctx(tmp_path: Path) -> ToolContext:
    ctx = ToolContext(
        workspace_root=str(tmp_path),
        allowed_shell_patterns=["echo*", "sleep*", "true", "false", "/bin/sh*", "sh *", "bash *"],
    )
    ctx.background_jobs = BackgroundJobManager()
    return ctx


def test_run_shell_basic(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    result = reg.call("run_shell", {"command": "echo hello"}, ctx)
    assert result.ok
    assert "hello" in result.output


def test_run_shell_disallowed_command(tmp_path: Path):
    reg = default_registry()
    ctx = ToolContext(workspace_root=str(tmp_path), allowed_shell_patterns=["echo*"])
    result = reg.call("run_shell", {"command": "ls /"}, ctx)
    assert not result.ok
    assert "not allowed" in result.output


def test_run_shell_timeout(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    result = reg.call("run_shell", {"command": "sleep 5", "timeout": 1}, ctx)
    assert not result.ok
    assert "timed out" in result.output.lower()


def test_run_shell_output_truncation(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    ctx.extra["shell_output_max_kb"] = 1
    cmd = "/bin/sh -c 'for i in $(seq 1 1000); do echo line_$i; done'"
    ctx.allowed_shell_patterns = ["/bin/sh*"]
    result = reg.call("run_shell", {"command": cmd}, ctx)
    assert result.ok
    assert "omitted from middle" in result.output


def test_background_job_lifecycle(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    start = reg.call("run_shell_background", {"command": "/bin/sh -c 'echo a; sleep 0.3; echo b'"}, ctx)
    assert start.ok
    job_id = start.data["job_id"]
    # Poll until finished
    for _ in range(60):
        out = reg.call("read_background_output", {"job_id": job_id}, ctx)
        if out.data.get("finished"):
            break
        time.sleep(0.1)
    assert out.data["finished"]
    assert out.data["returncode"] == 0
    assert "a" in out.output and "b" in out.output


def test_background_kill(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    start = reg.call("run_shell_background", {"command": "sleep 30"}, ctx)
    job_id = start.data["job_id"]
    time.sleep(0.1)
    killed = reg.call("kill_background_job", {"job_id": job_id}, ctx)
    assert killed.ok
    # Eventually finished
    for _ in range(40):
        out = reg.call("read_background_output", {"job_id": job_id}, ctx)
        if out.data.get("finished"):
            break
        time.sleep(0.05)
    assert out.data.get("finished")


def test_list_background_jobs(tmp_path: Path):
    reg = default_registry()
    ctx = _ctx(tmp_path)
    reg.call("run_shell_background", {"command": "echo 1"}, ctx)
    reg.call("run_shell_background", {"command": "echo 2"}, ctx)
    time.sleep(0.2)
    listing = reg.call("list_background_jobs", {}, ctx)
    assert listing.ok
    assert len(listing.output.splitlines()) == 2
