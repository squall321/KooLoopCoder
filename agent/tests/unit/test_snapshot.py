"""Tests for SnapshotManager (git-tag based)."""

from pathlib import Path

import pytest

from loopcoder.state.snapshot import SnapshotManager


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello\n")
    return tmp_path


def test_init_creates_repo_and_initial_commit(tmp_path: Path):
    sm = SnapshotManager(_ws(tmp_path))
    assert (tmp_path / ".git").is_dir()
    log = sm.repo.git.log("--oneline")
    assert log != ""


def test_snapshot_creates_tag(tmp_path: Path):
    sm = SnapshotManager(_ws(tmp_path))
    tag = sm.snapshot("sess1", goal_id="g1", message="done g1")
    assert tag == "loopcoder/sess1/g1"
    assert tag in [t.name for t in sm.repo.tags]


def test_snapshot_picks_up_changes(tmp_path: Path):
    sm = SnapshotManager(_ws(tmp_path))
    sm.snapshot("sess1", goal_id=None)
    (tmp_path / "b.txt").write_text("new\n")
    sm.snapshot("sess1", goal_id="g1")
    diff = sm.repo.git.log("-n2", "--oneline")
    assert diff.count("\n") >= 1  # at least 2 commits


def test_revert(tmp_path: Path):
    sm = SnapshotManager(_ws(tmp_path))
    initial = sm.snapshot("sess1", goal_id=None)
    (tmp_path / "a.txt").write_text("MODIFIED\n")
    sm.snapshot("sess1", goal_id="g1")
    sm.revert(initial)
    assert (tmp_path / "a.txt").read_text() == "hello\n"


def test_diff_since(tmp_path: Path):
    sm = SnapshotManager(_ws(tmp_path))
    initial = sm.snapshot("s1", goal_id=None)
    (tmp_path / "a.txt").write_text("changed\n")
    diff = sm.diff_since(initial)
    assert "changed" in diff or "-hello" in diff
