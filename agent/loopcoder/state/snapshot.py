"""Git-tag based snapshots for the workspace.

The workspace is initialized as a git repo on first use. Every successful
goal gets a tag named ``loopcoder/<session_id>/<goal_id>`` so we can revert
to a known-good state.
"""

from __future__ import annotations

from pathlib import Path

try:
    import git  # type: ignore[import-not-found]
    _GIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GIT_AVAILABLE = False
    git = None  # type: ignore[assignment]


class SnapshotManager:
    def __init__(self, workspace: str | Path):
        if not _GIT_AVAILABLE:
            raise RuntimeError("GitPython is required for SnapshotManager")
        self.workspace = str(Path(workspace).resolve())
        self.repo = self._open_or_init()

    def _open_or_init(self):  # type: ignore[no-untyped-def]
        try:
            return git.Repo(self.workspace)
        except git.InvalidGitRepositoryError:
            repo = git.Repo.init(self.workspace)
            # Ensure user identity for the first commit (use neutral defaults).
            with repo.config_writer() as cw:
                if not cw.has_section("user"):
                    cw.add_section("user")
                cw.set("user", "name", "loopcoder")
                cw.set("user", "email", "loopcoder@local")
            # Stage everything currently in the workspace as the initial commit.
            repo.git.add(A=True)
            try:
                repo.index.commit("loopcoder: initial workspace snapshot")
            except Exception:
                pass
            return repo

    def _make_tag(self, session_id: str, goal_id: str | None) -> str:
        if goal_id is None:
            return f"loopcoder/{session_id}/initial"
        return f"loopcoder/{session_id}/{goal_id}"

    def snapshot(self, session_id: str, goal_id: str | None = None, message: str = "") -> str:
        """Stage everything, commit if dirty, then tag. Returns tag name."""
        self.repo.git.add(A=True)
        if self.repo.is_dirty(untracked_files=True):
            self.repo.index.commit(message or f"loopcoder: snapshot for {goal_id or 'initial'}")
        tag = self._make_tag(session_id, goal_id)
        # Replace existing tag if it exists.
        if tag in [t.name for t in self.repo.tags]:
            self.repo.delete_tag(tag)
        self.repo.create_tag(tag, message=message or tag)
        return tag

    def revert(self, tag: str) -> None:
        """Hard-reset workspace to a given tag."""
        self.repo.git.reset("--hard", tag)

    def diff_since(self, tag: str) -> str:
        try:
            return self.repo.git.diff(tag)
        except Exception as e:
            return f"[diff unavailable: {e}]"
