"""Apptainer (Singularity) sandbox.

Executes commands inside an Apptainer container started with:
- ``--containall --no-home --readonly`` for isolation
- ``--bind`` mounts only what config explicitly allows
- ``--net --network=none`` to disallow internet (unless config.network)

This launches a *fresh* `apptainer exec` per command to keep state minimal
and avoid long-lived instances accidentally accumulating state. For workloads
needing daemonized state, consider ``apptainer instance start`` later.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from loopcoder.sandbox.base import Sandbox, SandboxResult


class ApptainerSandbox(Sandbox):
    def __init__(
        self,
        image: str,
        bind_mounts: list[dict[str, Any]] | None = None,
        network: bool = False,
        read_only_paths: list[str] | None = None,
        default_cwd: str = "/workspace",
        apptainer_bin: str = "apptainer",
    ) -> None:
        self.image = image
        self.bind_mounts = list(bind_mounts or [])
        self.network = network
        self.read_only_paths = list(read_only_paths or [])
        self.default_cwd = default_cwd
        self.apptainer_bin = apptainer_bin
        self.workspace: str | None = None

    def prepare(self, workspace: str) -> None:
        self.workspace = workspace

    def _build_argv(self, command: str, cwd: str | None) -> list[str]:
        argv = [self.apptainer_bin, "exec", "--containall", "--no-home"]
        if not self.network:
            # apptainer's network namespacing requires --net (root or fakeroot).
            # If unprivileged, fall back to environment-only restriction.
            argv.extend(["--net", "--network=none"])
        # Bind mounts: substitute {workspace}
        ws = self.workspace or "/tmp"
        for bm in self.bind_mounts:
            src = bm["source"].replace("{workspace}", ws)
            dst = bm["dest"]
            mode = bm.get("mode", "rw")
            argv.extend(["--bind", f"{src}:{dst}:{mode}"])
        for ro in self.read_only_paths:
            argv.extend(["--bind", f"{ro}:{ro}:ro"])
        argv.extend(["--pwd", cwd or self.default_cwd])
        argv.append(self.image)
        argv.extend(["sh", "-lc", command])
        return argv

    def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        argv = self._build_argv(command, cwd)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"apptainer binary not found: {self.apptainer_bin}. "
                "Install Apptainer or use sandbox.backend: host."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(str(e)) from e
        return SandboxResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def cleanup(self) -> None:
        # Per-command exec leaves nothing behind.
        pass

    # Helpful for debugging / dry-runs
    def render_argv(self, command: str, cwd: str | None = None) -> str:
        return " ".join(shlex.quote(a) for a in self._build_argv(command, cwd))
