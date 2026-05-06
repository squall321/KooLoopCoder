"""Shell execution tools.

- ``run_shell``: synchronous command, allowlist + timeout + output cap.
- ``run_shell_background`` / ``read_background_output`` / ``kill_background_job``
  / ``list_background_jobs``: long-running tasks (CC12). The agent kicks off
  e.g. a build, then polls for output without blocking the iter cycle.

Background jobs are managed by ``BackgroundJobManager`` attached to
``ctx.background_jobs``. Each job has a stable id, a started timestamp, and
a rolling stdout/stderr buffer that the agent can read incrementally.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


class RunShellParams(BaseModel):
    command: str = Field(..., description="Shell command to run.")
    cwd: str | None = Field(None, description="Relative to workspace; default = workspace root.")
    timeout: int = Field(300, description="Seconds.")


class RunShellTool(Tool):
    name: ClassVar[str] = "run_shell"
    description: ClassVar[str] = (
        "Run a shell command in the sandboxed workspace. "
        "Allowed commands are restricted by the allowed_shell_patterns policy."
    )
    ParamsModel: ClassVar[type[BaseModel]] = RunShellParams

    def execute(self, params: RunShellParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if not _allowed(params.command, ctx.allowed_shell_patterns):
            return ToolResult(
                ok=False,
                output=(
                    f"command not allowed by policy: {params.command!r}. "
                    f"Allowed patterns: {ctx.allowed_shell_patterns}"
                ),
            )
        max_kb = int(ctx.extra.get("shell_output_max_kb", 256))

        # Prefer sandbox if available; fall back to direct subprocess (host mode).
        if ctx.sandbox is not None and getattr(ctx.sandbox, "exec", None):
            try:
                completed = ctx.sandbox.exec(
                    params.command,
                    cwd=params.cwd,
                    timeout=params.timeout,
                )
                output = _truncate_kb(completed.stdout + completed.stderr, max_kb)
                return ToolResult(
                    ok=completed.returncode == 0,
                    output=f"$ {params.command}\nexit={completed.returncode}\n{output}",
                    data={"returncode": completed.returncode},
                )
            except TimeoutError:
                return ToolResult(ok=False, output=f"command timed out after {params.timeout}s")

        # Host fallback (no sandbox configured)
        try:
            proc = subprocess.run(
                params.command,
                shell=True,
                cwd=(ctx.workspace_root if not params.cwd else f"{ctx.workspace_root}/{params.cwd}"),
                capture_output=True,
                text=True,
                timeout=params.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"command timed out after {params.timeout}s")
        out = _truncate_kb(proc.stdout + proc.stderr, max_kb)
        return ToolResult(
            ok=proc.returncode == 0,
            output=f"$ {params.command}\nexit={proc.returncode}\n{out}",
            data={"returncode": proc.returncode},
        )


def _allowed(command: str, patterns: list[str]) -> bool:
    if not patterns:
        # Empty list means "deny all" — opt out by setting at least one pattern.
        return False
    cmd = command.strip()
    return any(fnmatch.fnmatchcase(cmd, p) for p in patterns)


def _truncate_kb(text: str, max_kb: int) -> str:
    max_bytes = max_kb * 1024
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    head = encoded[: max_bytes // 2]
    tail = encoded[-max_bytes // 2 :]
    omitted = len(encoded) - len(head) - len(tail)
    return (
        head.decode(errors="ignore")
        + f"\n\n[... {omitted} bytes omitted from middle ...]\n\n"
        + tail.decode(errors="ignore")
    )


# ---------- Background job manager (CC12) ----------


@dataclass
class _Job:
    id: str
    command: str
    proc: subprocess.Popen
    started: float
    cwd: str | None
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    finished: bool = False
    returncode: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_read: int = 0


class BackgroundJobManager:
    """Thread-safe registry of background subprocess jobs.

    Each job is read by a dedicated reader thread that drains stdout/stderr
    into bytearrays the LLM can incrementally read via read_background_output.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._mu = threading.Lock()

    def start(self, command: str, cwd: str | None) -> _Job:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            preexec_fn=os.setsid,  # new process group → kill children too
        )
        job = _Job(id=uuid.uuid4().hex[:8], command=command, proc=proc, started=time.time(), cwd=cwd)
        with self._mu:
            self._jobs[job.id] = job
        threading.Thread(target=self._drain, args=(job,), daemon=True).start()
        return job

    def _drain(self, job: _Job) -> None:
        try:
            while True:
                # Read in small chunks so output appears quickly
                out = job.proc.stdout.read(4096) if job.proc.stdout else b""
                err = job.proc.stderr.read(4096) if job.proc.stderr else b""
                if out:
                    with job._lock:
                        job.stdout.extend(out)
                if err:
                    with job._lock:
                        job.stderr.extend(err)
                if not out and not err:
                    if job.proc.poll() is not None:
                        # Drain remaining
                        if job.proc.stdout:
                            rest = job.proc.stdout.read()
                            if rest:
                                with job._lock:
                                    job.stdout.extend(rest)
                        if job.proc.stderr:
                            rest = job.proc.stderr.read()
                            if rest:
                                with job._lock:
                                    job.stderr.extend(rest)
                        break
                    time.sleep(0.05)
        finally:
            job.returncode = job.proc.returncode
            job.finished = True

    def get(self, job_id: str) -> _Job | None:
        with self._mu:
            return self._jobs.get(job_id)

    def list(self) -> list[_Job]:
        with self._mu:
            return list(self._jobs.values())

    def kill(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        if not job.finished:
            try:
                os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                job.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        return True


# ---------- Background tools ----------


class RunShellBackgroundParams(BaseModel):
    command: str
    cwd: str | None = None


class RunShellBackgroundTool(Tool):
    name: ClassVar[str] = "run_shell_background"
    description: ClassVar[str] = (
        "Start a long-running shell command in the background. Returns a job id. "
        "Use read_background_output(job_id) to poll output."
    )
    ParamsModel: ClassVar[type[BaseModel]] = RunShellBackgroundParams

    def execute(self, params: RunShellBackgroundParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if not _allowed(params.command, ctx.allowed_shell_patterns):
            return ToolResult(ok=False, output=f"command not allowed by policy: {params.command!r}")
        if ctx.background_jobs is None:
            return ToolResult(ok=False, output="background job manager not available in this context")
        cwd = ctx.workspace_root if not params.cwd else os.path.join(ctx.workspace_root, params.cwd)
        job = ctx.background_jobs.start(params.command, cwd)
        return ToolResult(ok=True, output=f"job {job.id} started: {params.command}", data={"job_id": job.id})


class ReadBackgroundOutputParams(BaseModel):
    job_id: str
    since_offset: int = Field(0, ge=0, description="Bytes already read previously; pass 0 to read from start.")
    max_bytes: int = Field(8192, ge=1, le=262144)


class ReadBackgroundOutputTool(Tool):
    name: ClassVar[str] = "read_background_output"
    description: ClassVar[str] = (
        "Read incremental output from a background job. Pass since_offset returned "
        "previously to avoid re-reading the same bytes."
    )
    ParamsModel: ClassVar[type[BaseModel]] = ReadBackgroundOutputParams

    def execute(self, params: ReadBackgroundOutputParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if ctx.background_jobs is None:
            return ToolResult(ok=False, output="no manager")
        job = ctx.background_jobs.get(params.job_id)
        if job is None:
            return ToolResult(ok=False, output=f"unknown job: {params.job_id}")
        with job._lock:
            full_out = bytes(job.stdout)
            full_err = bytes(job.stderr)
        if params.since_offset >= len(full_out) + len(full_err) and not job.finished:
            return ToolResult(
                ok=True,
                output=f"[no new output yet; running for {time.time() - job.started:.1f}s]",
                data={"job_id": job.id, "finished": False, "next_offset": params.since_offset},
            )
        chunk_out = full_out[params.since_offset : params.since_offset + params.max_bytes].decode(errors="replace")
        new_offset = params.since_offset + len(chunk_out.encode())
        # Append err only if we drained out fully
        body = chunk_out
        if job.finished:
            err = full_err.decode(errors="replace")
            if err:
                body += "\n--stderr--\n" + err
            body += f"\n[exit={job.returncode}, total {time.time() - job.started:.1f}s]"
        return ToolResult(
            ok=True if not job.finished or job.returncode == 0 else False,
            output=body,
            data={"job_id": job.id, "finished": job.finished, "next_offset": new_offset, "returncode": job.returncode},
        )


class _JobIdParams(BaseModel):
    job_id: str


class KillBackgroundJobTool(Tool):
    name: ClassVar[str] = "kill_background_job"
    description: ClassVar[str] = "Terminate a background job."
    ParamsModel: ClassVar[type[BaseModel]] = _JobIdParams

    def execute(self, params: _JobIdParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if ctx.background_jobs is None:
            return ToolResult(ok=False, output="no manager")
        ok = ctx.background_jobs.kill(params.job_id)
        return ToolResult(ok=ok, output=f"killed {params.job_id}" if ok else "unknown job")


class _NoArgs(BaseModel):
    pass


class ListBackgroundJobsTool(Tool):
    name: ClassVar[str] = "list_background_jobs"
    description: ClassVar[str] = "List active and recent background jobs."
    ParamsModel: ClassVar[type[BaseModel]] = _NoArgs

    def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if ctx.background_jobs is None:
            return ToolResult(ok=False, output="no manager")
        jobs = ctx.background_jobs.list()
        if not jobs:
            return ToolResult(ok=True, output="(none)")
        rows = [
            f"{j.id}  {'done' if j.finished else 'running':>7}  rc={j.returncode}  age={time.time()-j.started:.1f}s  {j.command[:80]}"
            for j in jobs
        ]
        return ToolResult(ok=True, output="\n".join(rows))
