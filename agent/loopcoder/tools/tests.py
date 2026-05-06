"""Test runner tool. Auto-detects pytest / npm test / cargo test / go test."""

from __future__ import annotations

import os
import subprocess
from typing import ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


class RunTestsParams(BaseModel):
    target: str | None = Field(
        None, description="Optional test target (e.g. tests/test_foo.py::test_bar)."
    )
    extra_args: list[str] = Field(default_factory=list)
    timeout: int = 600


class RunTestsTool(Tool):
    name: ClassVar[str] = "run_tests"
    description: ClassVar[str] = (
        "Run the project's test suite. Auto-detects pytest, npm test, cargo test, go test. "
        "Returns structured pass/fail counts when possible."
    )
    ParamsModel: ClassVar[type[BaseModel]] = RunTestsParams

    def execute(self, params: RunTestsParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        framework = _detect(ctx.workspace_root)
        if framework is None:
            return ToolResult(ok=False, output="No test framework detected.")
        cmd = _build_cmd(framework, params.target, params.extra_args)
        try:
            proc = subprocess.run(
                cmd,
                cwd=ctx.workspace_root,
                capture_output=True,
                text=True,
                timeout=params.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"tests timed out after {params.timeout}s")

        ok = proc.returncode == 0
        summary = _parse_summary(framework, proc.stdout + proc.stderr)
        body = (proc.stdout + proc.stderr).strip()
        return ToolResult(
            ok=ok,
            output=f"$ {' '.join(cmd)}\nexit={proc.returncode}\n{body}",
            data={"framework": framework, "returncode": proc.returncode, **summary},
        )


def _detect(root: str) -> str | None:
    if os.path.isfile(os.path.join(root, "pytest.ini")) or _has_pytest_in_pyproject(root):
        return "pytest"
    if os.path.isfile(os.path.join(root, "package.json")):
        return "npm"
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return "cargo"
    if os.path.isfile(os.path.join(root, "go.mod")):
        return "go"
    if any(f.startswith("test_") and f.endswith(".py") for f in os.listdir(root)):
        return "pytest"
    return None


def _has_pytest_in_pyproject(root: str) -> bool:
    p = os.path.join(root, "pyproject.toml")
    if not os.path.isfile(p):
        return False
    try:
        return "pytest" in open(p, encoding="utf-8").read()
    except Exception:
        return False


def _build_cmd(fw: str, target: str | None, extra: list[str]) -> list[str]:
    if fw == "pytest":
        return ["pytest", "-q", *(["-k", target] if target and "::" not in target else ([target] if target else [])), *extra]
    if fw == "npm":
        return ["npm", "test", "--", *extra] if extra else ["npm", "test"]
    if fw == "cargo":
        cmd = ["cargo", "test"]
        if target:
            cmd.append(target)
        return cmd + extra
    if fw == "go":
        return ["go", "test", target or "./...", *extra]
    return [fw]


def _parse_summary(fw: str, out: str) -> dict:
    if fw == "pytest":
        # Look for "X passed, Y failed" style summary line
        import re
        passed = failed = skipped = 0
        m = re.search(r"(\d+) passed", out)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", out)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) skipped", out)
        if m:
            skipped = int(m.group(1))
        return {"passed": passed, "failed": failed, "skipped": skipped}
    return {}
