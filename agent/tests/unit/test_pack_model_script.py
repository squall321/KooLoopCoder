"""Smoke tests for scripts/pack-model.sh."""

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "pack-model.sh"


def test_exists_and_executable():
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK)


def test_help_text():
    res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    out = res.stdout
    assert "pack" in out.lower()
    assert "huggingface" in out.lower() or "hf" in out.lower()


def test_missing_args_fails():
    # No --hf and no positional model_dir
    res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode != 0


def test_unknown_flag_fails():
    res = subprocess.run(["bash", str(SCRIPT), "--bogus", "/x", "/y"], capture_output=True, text=True)
    assert res.returncode != 0


def test_missing_config_json_rejected(tmp_path: Path):
    """Source dir without config.json must be rejected (it's not a HF model)."""
    fake = tmp_path / "fake_model"
    fake.mkdir()
    out_sif = tmp_path / "out.sif"
    res = subprocess.run(
        ["bash", str(SCRIPT), str(fake), str(out_sif)],
        capture_output=True, text=True,
    )
    assert res.returncode != 0
    assert "config.json" in (res.stdout + res.stderr)
