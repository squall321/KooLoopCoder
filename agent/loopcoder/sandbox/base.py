"""Sandbox abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str


class Sandbox(ABC):
    @abstractmethod
    def prepare(self, workspace: str) -> None: ...

    @abstractmethod
    def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> SandboxResult: ...

    @abstractmethod
    def cleanup(self) -> None: ...
