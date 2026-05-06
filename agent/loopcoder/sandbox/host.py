"""Host sandbox: runs commands directly on the host (no isolation).

This is the OPT-IN insecure backend. It is provided so the agent can
function in environments where Apptainer isn't available (CI, dev, the
Test VM itself). Real production runs should use ApptainerSandbox.
"""

from __future__ import annotations

import subprocess

from loopcoder.sandbox.base import Sandbox, SandboxResult


class HostSandbox(Sandbox):
    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    def prepare(self, workspace: str) -> None:
        self.workspace = workspace

    def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        if self.workspace is None:
            raise RuntimeError("HostSandbox.prepare() not called")
        wd = self.workspace if not cwd else f"{self.workspace}/{cwd}"
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=wd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(str(e)) from e
        return SandboxResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def cleanup(self) -> None:
        # Nothing persistent to clean.
        pass
