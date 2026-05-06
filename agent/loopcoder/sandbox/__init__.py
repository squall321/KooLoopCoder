"""Sandbox backends for executing tool actions in isolation."""

from loopcoder.sandbox.base import Sandbox, SandboxResult
from loopcoder.sandbox.host import HostSandbox

__all__ = ["Sandbox", "SandboxResult", "HostSandbox"]


def make_sandbox(backend: str, **kwargs):  # type: ignore[no-untyped-def]
    if backend == "host":
        return HostSandbox(**kwargs)
    if backend == "apptainer":
        from loopcoder.sandbox.apptainer import ApptainerSandbox
        return ApptainerSandbox(**kwargs)
    raise ValueError(f"unknown sandbox backend: {backend}")
