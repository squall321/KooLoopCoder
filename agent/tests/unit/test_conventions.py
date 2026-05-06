"""Tests for project-convention auto-loader (CC14)."""

from pathlib import Path

from loopcoder.loop.conventions import CONVENTION_FILE_NAMES, load_conventions


def test_load_conventions_finds_known_files(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("# claude rules\n")
    (tmp_path / "AGENTS.md").write_text("# agent rules\n")
    (tmp_path / "README.md").write_text("# readme\n")
    convs = load_conventions(tmp_path)
    paths = [c.path for c in convs]
    assert "CLAUDE.md" in paths
    assert "AGENTS.md" in paths
    assert "README.md" in paths


def test_load_conventions_priority_order(tmp_path: Path):
    for name in CONVENTION_FILE_NAMES:
        (tmp_path / name).write_text(f"# {name}\n")
    convs = load_conventions(tmp_path)
    # Must be in declared priority order
    paths = [c.path for c in convs]
    assert paths == list(CONVENTION_FILE_NAMES)


def test_load_conventions_searches_subdirs(tmp_path: Path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "AGENTS.md").write_text("subdir\n")
    convs = load_conventions(tmp_path)
    assert any(".github/AGENTS.md" in c.path for c in convs)


def test_load_conventions_truncates_huge(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("x" * 200_000)
    convs = load_conventions(tmp_path, max_bytes_per_file=1024)
    assert len(convs) == 1
    assert "truncated" in convs[0].content
