"""Smoke tests for scripts/build-sif-bundle.sh and scripts/fetch-cwrsync.sh.

These scripts run apptainer builds (slow, need sudo) so we only verify
locally checkable behavior: existence, executability, help text, arg
parsing, and that --dry-run plans the right steps without building.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD = REPO_ROOT / "scripts" / "build-sif-bundle.sh"
FETCH = REPO_ROOT / "scripts" / "fetch-cwrsync.sh"


def test_scripts_exist_and_executable():
    for s in (BUILD, FETCH):
        assert s.is_file(), f"missing {s}"
        assert os.access(s, os.X_OK), f"not executable: {s}"


def test_build_help_text():
    res = subprocess.run(
        ["bash", str(BUILD), "--help"], capture_output=True, text=True
    )
    assert res.returncode == 0
    out = res.stdout
    assert "SIF-only" in out
    assert "no VM" in out or "no VM, no WSL2" in out


def test_build_unknown_flag_fails():
    res = subprocess.run(
        ["bash", str(BUILD), "--bogus"], capture_output=True, text=True
    )
    assert res.returncode != 0


def test_build_dry_run_plans_sifs_no_build(tmp_path: Path):
    out_dir = tmp_path / "sif-bundle"
    res = subprocess.run(
        ["bash", str(BUILD), "--output", str(out_dir), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    combined = res.stdout + res.stderr
    # It must plan all three SIFs and the cwRsync staging…
    assert "vllm" in combined
    assert "loopcoder-sandbox" in combined or "sandbox" in combined
    assert "loopcoder-suite" in combined or "suite" in combined
    assert "cwRsync" in combined or "fetch-cwrsync" in combined
    # …but must NOT have actually built anything (no SIF on disk).
    assert not list(tmp_path.rglob("*.sif"))


def test_build_skip_flags_dry_run(tmp_path: Path):
    res = subprocess.run(
        [
            "bash",
            str(BUILD),
            "--output",
            str(tmp_path / "b"),
            "--skip-vllm",
            "--skip-wheels",
            "--no-win-tools",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    combined = res.stdout + res.stderr
    assert "--skip-vllm" in combined or "skipping vllm" in combined
    assert "no-win-tools" in combined or "skipping cwRsync" in combined


def test_fetch_cwrsync_requires_dest():
    res = subprocess.run(
        ["bash", str(FETCH)], capture_output=True, text=True
    )
    assert res.returncode != 0
