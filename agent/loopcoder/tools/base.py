"""Tool ABC and shared utilities.

A Tool exposes:
  - name (LLM-visible)
  - description
  - params model (Pydantic) → JSON schema for tool definition
  - execute(params, ctx) → ToolResult

The registry converts Tools into the OpenAI tools-format used in
chat completions, and dispatches calls back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel


class ToolError(Exception):
    """Raised when a tool fails in a recoverable way (will be sent back to LLM)."""


@dataclass
class ToolContext:
    """Information passed to every tool call.

    ``read_files`` / ``written_files`` are mutable per-session sets used by
    PreToolUse hooks to enforce CC3 ("must Read before Write/Edit") and to
    let the agent avoid redundant rereads.
    """

    workspace_root: str
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_shell_patterns: list[str] = field(default_factory=list)
    sandbox: Any = None  # set later when sandbox is wired in
    git_repo: Any = None  # set later
    extra: dict[str, Any] = field(default_factory=dict)
    read_files: set[str] = field(default_factory=set)
    written_files: set[str] = field(default_factory=set)
    background_jobs: Any = None  # BackgroundJobManager, attached by controller
    todo_list: Any = None  # TodoList instance, attached by controller
    spawn_agent: Any = None  # Sub-agent factory, attached by controller


@dataclass
class ToolResult:
    ok: bool
    output: str  # human/LLM-readable result text
    data: Any = None  # structured payload (optional)
    truncated: bool = False
    duration_ms: int = 0


class Tool(ABC):
    """Subclass and set ``name``, ``description``, ``ParamsModel``."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    ParamsModel: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        """Produce an OpenAI tools entry for this tool."""
        json_schema = self.ParamsModel.model_json_schema()
        # Pydantic emits a $defs / $ref tree. OpenAI accepts standard JSON Schema.
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json_schema,
            },
        }

    def parse_params(self, raw: dict[str, Any]) -> BaseModel:
        return self.ParamsModel.model_validate(raw)

    @abstractmethod
    def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:
        """Run the tool. Implementations must not raise for recoverable errors;
        instead return ToolResult(ok=False, output=...).
        """
        raise NotImplementedError
