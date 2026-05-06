"""Git tools: status / diff / log / revert_to_snapshot."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopcoder.tools.base import Tool, ToolContext, ToolResult


def _repo(ctx: ToolContext):  # type: ignore[no-untyped-def]
    if ctx.git_repo is None:
        try:
            import git  # type: ignore[import-not-found]
            return git.Repo(ctx.workspace_root)
        except Exception as e:
            raise RuntimeError(f"git unavailable: {e}") from e
    return ctx.git_repo


class _Empty(BaseModel):
    pass


class GitStatusTool(Tool):
    name: ClassVar[str] = "git_status"
    description: ClassVar[str] = "Show git status of the workspace."
    ParamsModel: ClassVar[type[BaseModel]] = _Empty

    def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            repo = _repo(ctx)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=repo.git.status())


class GitDiffParams(BaseModel):
    since_tag: str | None = Field(None, description="If set, diff since this tag.")
    paths: list[str] | None = None


class GitDiffTool(Tool):
    name: ClassVar[str] = "git_diff"
    description: ClassVar[str] = "Show git diff. Optionally since a tag (e.g. goal-start)."
    ParamsModel: ClassVar[type[BaseModel]] = GitDiffParams

    def execute(self, params: GitDiffParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            repo = _repo(ctx)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))
        args: list[str] = []
        if params.since_tag:
            args.append(params.since_tag)
        if params.paths:
            args.append("--")
            args.extend(params.paths)
        try:
            out = repo.git.diff(*args)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=out)


class GitLogParams(BaseModel):
    n: int = 10


class GitLogTool(Tool):
    name: ClassVar[str] = "git_log"
    description: ClassVar[str] = "Show recent commit log entries."
    ParamsModel: ClassVar[type[BaseModel]] = GitLogParams

    def execute(self, params: GitLogParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            repo = _repo(ctx)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))
        out = repo.git.log(f"-n{params.n}", "--oneline")
        return ToolResult(ok=True, output=out)


class RevertParams(BaseModel):
    tag: str = Field(..., description="Existing tag to revert workspace to (hard reset).")


class RevertToSnapshotTool(Tool):
    name: ClassVar[str] = "revert_to_snapshot"
    description: ClassVar[str] = (
        "Hard-reset workspace to a previously created snapshot tag. Destructive — "
        "use only if current approach is failing and you want to start fresh."
    )
    ParamsModel: ClassVar[type[BaseModel]] = RevertParams

    def execute(self, params: RevertParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            repo = _repo(ctx)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))
        try:
            repo.git.reset("--hard", params.tag)
        except Exception as e:
            return ToolResult(ok=False, output=f"revert failed: {e}")
        return ToolResult(ok=True, output=f"reset to {params.tag}")
