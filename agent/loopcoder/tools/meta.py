"""Meta tools: record_thought (free memo), submit_goal (declare done)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


class RecordThoughtParams(BaseModel):
    text: str = Field(..., description="Free-form note to record (not verified, not executed).")


class RecordThoughtTool(Tool):
    name: ClassVar[str] = "record_thought"
    description: ClassVar[str] = (
        "Record a free-form thought / plan / hypothesis. Useful for self-criticism "
        "or planning the next move. Not executed, not verified, just persisted to the log."
    )
    ParamsModel: ClassVar[type[BaseModel]] = RecordThoughtParams

    def execute(self, params: RecordThoughtParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        return ToolResult(ok=True, output=f"thought recorded ({len(params.text)} chars)")


class SubmitGoalParams(BaseModel):
    goal_id: str = Field(..., description="ID of the goal you believe is complete.")
    summary: str = Field("", description="Optional one-line summary of what you did.")


class SubmitGoalTool(Tool):
    name: ClassVar[str] = "submit_goal"
    description: ClassVar[str] = (
        "Declare that the goal is complete. Verification will be run afterwards "
        "OUTSIDE this conversation. If verification fails you must continue iterating."
    )
    ParamsModel: ClassVar[type[BaseModel]] = SubmitGoalParams

    def execute(self, params: SubmitGoalParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        return ToolResult(
            ok=True,
            output=f"submitted {params.goal_id}; verification will run.",
            data={"submitted_goal": params.goal_id, "summary": params.summary},
        )
