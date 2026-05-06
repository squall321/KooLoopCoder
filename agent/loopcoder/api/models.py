"""Pydantic models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- requests ----------


class SessionStartRequest(BaseModel):
    plan: dict[str, Any] = Field(..., description="Inline plan.yaml (parsed dict).")
    only_goal: str | None = None
    config_overrides: dict[str, Any] | None = None
    """Optional partial loopcoder.yaml override merged on top of /etc config."""


class SessionStartFromPathRequest(BaseModel):
    plan_path: str = Field(..., description="Absolute path to a plan.yaml file.")
    only_goal: str | None = None


class StopRequest(BaseModel):
    """Soft stop request — current iter finishes then the loop exits."""

    reason: str | None = None


class ToolCallRequest(BaseModel):
    """Direct tool invocation for debugging / external automation."""

    name: str
    arguments: dict[str, Any]
    workspace: str = Field(..., description="Absolute workspace root.")


# ---------- responses ----------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    sessions_active: int


class SessionRef(BaseModel):
    id: str
    status: str
    plan_path: str | None
    started_at: float | None
    ended_at: float | None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


class GoalView(BaseModel):
    goal_id: str
    status: str
    iterations: int
    started_at: float | None
    ended_at: float | None


class IterationView(BaseModel):
    iter: int
    prompt_tokens: int
    completion_tokens: int
    verify_passed: bool | None
    verify_log: str | None
    started_at: float | None
    ended_at: float | None


class ToolMeta(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolCallResponse(BaseModel):
    ok: bool
    output: str
    truncated: bool = False
    duration_ms: int = 0
    data: Any = None
