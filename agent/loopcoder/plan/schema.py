"""Pydantic models for plan.yaml.

The plan is the user-authored contract: it states what to build (goals)
and how to verify each goal is done (acceptance checks). Verification is
performed *outside* the LLM so the agent cannot fake completion.
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------- Acceptance ----------

class _AcceptanceBase(BaseModel):
    kind: str


class ShellExpect(BaseModel):
    exit_code: int = 0
    stdout_contains: str | None = None
    stderr_not_contains: str | None = None
    stdout_matches: str | None = None  # regex


class ShellAcceptance(_AcceptanceBase):
    kind: Literal["shell"] = "shell"
    run: str
    cwd: str | None = None
    timeout: int = 300
    expect: ShellExpect = ShellExpect()


class FileExistsAcceptance(_AcceptanceBase):
    kind: Literal["file_exists"] = "file_exists"
    path: str


class FileContainsAcceptance(_AcceptanceBase):
    kind: Literal["file_contains"] = "file_contains"
    path: str
    pattern: str  # regex


class FileNotContainsAcceptance(_AcceptanceBase):
    kind: Literal["file_not_contains"] = "file_not_contains"
    path: str
    pattern: str


class HttpRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None


class HttpExpect(BaseModel):
    status: int = 200
    body_contains: str | None = None


class HttpAcceptance(_AcceptanceBase):
    kind: Literal["http"] = "http"
    prepare: str | None = None  # shell command to run before checking
    request: HttpRequest
    expect: HttpExpect = HttpExpect()


AcceptanceCheck = Union[
    ShellAcceptance,
    FileExistsAcceptance,
    FileContainsAcceptance,
    FileNotContainsAcceptance,
    HttpAcceptance,
]


# ---------- Goal ----------

class Goal(BaseModel):
    id: str
    title: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 100
    acceptance: list[AcceptanceCheck] = Field(..., min_length=1)

    @field_validator("id")
    @classmethod
    def _id_simple(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_." for c in v):
            raise ValueError(f"goal.id must be non-empty alnum/-_.: {v!r}")
        return v


# ---------- Project / context / constraints ----------

class PlanProject(BaseModel):
    name: str
    workspace: str
    language: str | None = None  # e.g. "python", "node", auto-detected if None


class PlanConstraints(BaseModel):
    max_iterations_per_goal: int = 50  # 0 = unbounded
    max_total_minutes: int = 360  # 0 = unbounded
    max_tokens_per_iter: int = 200_000
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_shell_commands: list[str] | None = None
    network_allowed: bool = False


class PlanContextPin(BaseModel):
    path: str


class PlanContext(BaseModel):
    description: str = ""
    files_to_read_first: list[str] = Field(default_factory=list)
    reference_docs: list[str] = Field(default_factory=list)
    pin_in_context: list[PlanContextPin] = Field(default_factory=list)


class PlanLLM(BaseModel):
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int | None = None


class Plan(BaseModel):
    project: PlanProject
    constraints: PlanConstraints = PlanConstraints()
    context: PlanContext = PlanContext()
    goals: list[Goal] = Field(..., min_length=1)
    llm: PlanLLM = PlanLLM()

    @model_validator(mode="after")
    def _check_unique_goal_ids_and_deps(self) -> "Plan":
        ids = [g.id for g in self.goals]
        dups = {x for x in ids if ids.count(x) > 1}
        if dups:
            raise ValueError(f"duplicate goal ids: {sorted(dups)}")
        idset = set(ids)
        for g in self.goals:
            for d in g.depends_on:
                if d not in idset:
                    raise ValueError(f"goal {g.id!r} depends_on unknown id {d!r}")
                if d == g.id:
                    raise ValueError(f"goal {g.id!r} depends on itself")
        return self
