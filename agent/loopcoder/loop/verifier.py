"""Goal verifier.

Runs each acceptance check OUTSIDE the LLM and returns a structured result.
The LLM cannot fake passing this — only matching real-world output passes.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopcoder.logsetup import get_logger
from loopcoder.plan.schema import (
    AcceptanceCheck,
    FileContainsAcceptance,
    FileExistsAcceptance,
    FileNotContainsAcceptance,
    HttpAcceptance,
    ShellAcceptance,
)

log = get_logger("loopcoder.verifier")


@dataclass
class CheckResult:
    kind: str
    passed: bool
    detail: str
    duration_ms: int = 0


@dataclass
class VerificationResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    log: str = ""

    def short_summary(self) -> str:
        ok = sum(1 for c in self.checks if c.passed)
        return f"{ok}/{len(self.checks)} acceptance checks passed"


class Verifier:
    """Runs acceptance checks for a goal in a given workspace."""

    def __init__(self, workspace: str, sandbox: Any | None = None) -> None:
        self.workspace = workspace
        self.sandbox = sandbox

    def run(self, checks: list[AcceptanceCheck]) -> VerificationResult:
        results: list[CheckResult] = []
        for c in checks:
            if isinstance(c, ShellAcceptance):
                results.append(self._run_shell(c))
            elif isinstance(c, FileExistsAcceptance):
                results.append(self._run_file_exists(c))
            elif isinstance(c, FileContainsAcceptance):
                results.append(self._run_file_contains(c, expect_match=True))
            elif isinstance(c, FileNotContainsAcceptance):
                results.append(self._run_file_contains(c, expect_match=False))
            elif isinstance(c, HttpAcceptance):
                results.append(self._run_http(c))
            else:
                results.append(CheckResult(kind=str(type(c).__name__), passed=False, detail="unknown kind"))

        passed = all(r.passed for r in results)
        log_lines = []
        for r in results:
            mark = "PASS" if r.passed else "FAIL"
            log_lines.append(f"[{mark}] ({r.kind}) {r.detail}")
        log.info("verify %s (%d/%d checks)",
                 "PASS" if passed else "FAIL",
                 sum(1 for r in results if r.passed),
                 len(results))
        return VerificationResult(passed=passed, checks=results, log="\n".join(log_lines))

    # ---------- individual checks ----------

    def _run_shell(self, c: ShellAcceptance) -> CheckResult:
        cwd = (self.workspace if not c.cwd else os.path.join(self.workspace, c.cwd))
        if self.sandbox is not None and getattr(self.sandbox, "exec", None):
            try:
                rs = self.sandbox.exec(c.run, cwd=c.cwd, timeout=c.timeout)
                rc, out, err = rs.returncode, rs.stdout, rs.stderr
            except TimeoutError:
                return CheckResult(kind="shell", passed=False, detail=f"$ {c.run}\n[timed out after {c.timeout}s]")
        else:
            try:
                proc = subprocess.run(
                    c.run, shell=True, cwd=cwd, capture_output=True, text=True,
                    timeout=c.timeout, check=False,
                )
                rc, out, err = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                return CheckResult(kind="shell", passed=False, detail=f"$ {c.run}\n[timed out after {c.timeout}s]")
        passed = rc == c.expect.exit_code
        if c.expect.stdout_contains is not None and c.expect.stdout_contains not in out:
            passed = False
        if c.expect.stderr_not_contains is not None and c.expect.stderr_not_contains in err:
            passed = False
        if c.expect.stdout_matches is not None and not re.search(c.expect.stdout_matches, out):
            passed = False
        detail = f"$ {c.run}\nexit={rc}\n--stdout--\n{out}\n--stderr--\n{err}"
        return CheckResult(kind="shell", passed=passed, detail=detail)

    def _run_file_exists(self, c: FileExistsAcceptance) -> CheckResult:
        p = Path(self.workspace) / c.path
        return CheckResult(kind="file_exists", passed=p.exists(), detail=str(p))

    def _run_file_contains(self, c: FileContainsAcceptance | FileNotContainsAcceptance, expect_match: bool) -> CheckResult:
        kind = "file_contains" if expect_match else "file_not_contains"
        p = Path(self.workspace) / c.path
        if not p.is_file():
            return CheckResult(kind=kind, passed=False, detail=f"file missing: {c.path}")
        try:
            content = p.read_text(errors="replace")
        except Exception as e:
            return CheckResult(kind=kind, passed=False, detail=f"read error: {e}")
        try:
            found = re.search(c.pattern, content) is not None
        except re.error as e:
            return CheckResult(kind=kind, passed=False, detail=f"bad regex: {e}")
        passed = found if expect_match else (not found)
        return CheckResult(kind=kind, passed=passed, detail=f"path={c.path} pattern={c.pattern!r} found={found}")

    def _run_http(self, c: HttpAcceptance) -> CheckResult:
        # Optional preparation step
        if c.prepare:
            try:
                subprocess.run(c.prepare, shell=True, cwd=self.workspace, timeout=120, check=False)
            except Exception as e:
                return CheckResult(kind="http", passed=False, detail=f"prepare failed: {e}")
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return CheckResult(kind="http", passed=False, detail="httpx not installed")
        try:
            r = httpx.request(
                c.request.method,
                c.request.url,
                headers=c.request.headers or None,
                json=c.request.body if isinstance(c.request.body, (dict, list)) else None,
                content=(c.request.body if isinstance(c.request.body, (bytes, str)) else None),
                timeout=30.0,
            )
        except Exception as e:
            return CheckResult(kind="http", passed=False, detail=f"request failed: {e}")
        passed = r.status_code == c.expect.status
        body_text = r.text or ""
        if c.expect.body_contains is not None and c.expect.body_contains not in body_text:
            passed = False
        return CheckResult(
            kind="http",
            passed=passed,
            detail=f"{c.request.method} {c.request.url} -> {r.status_code}\n{body_text[:2048]}",
        )
