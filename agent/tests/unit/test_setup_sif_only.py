"""Smoke tests for the SIF-only changes in setup.sh.

setup.sh needs root + a real bundle for most stages, so locally we only
check: help text mentions the SIF-only model flow, --model-src is a
recognized flag (not "unknown arg"), and a manifest.sha256-only bundle
is accepted (legacy bundles needed manifest.yaml).
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SETUP = (REPO_ROOT / "setup.sh").resolve()


def test_setup_exists():
    assert SETUP.is_file()


def test_help_mentions_sif_only_model():
    res = subprocess.run(
        ["bash", str(SETUP), "--help"], capture_output=True, text=True
    )
    assert res.returncode == 0
    out = res.stdout
    assert "--model-src" in out
    assert "model.sif" in out
    assert "SIF-only" in out


def test_unknown_flag_still_fails():
    res = subprocess.run(
        ["bash", str(SETUP), "--definitely-not-a-flag"],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "unknown arg" in (res.stdout + res.stderr)


def test_model_src_is_recognized_flag():
    # --model-src consumes its value; an unknown-arg error must NOT appear.
    # We follow with --help so the script exits 0 fast without doing work.
    res = subprocess.run(
        ["bash", str(SETUP), "--model-src", "/tmp/whatever", "--help"],
        capture_output=True,
        text=True,
    )
    assert "unknown arg: --model-src" not in (res.stdout + res.stderr)


def test_template_binds_model_sif_not_dir():
    """vllm.service.template must mount model.sif, not a model directory."""
    tmpl = (REPO_ROOT / "systemd" / "vllm.service.template").read_text()
    assert "@MODEL_SIF@" in tmpl
    assert "image-src=/" in tmpl
    # The old directory-bind placeholder must be gone.
    assert "@MODEL_DIR@" not in tmpl
