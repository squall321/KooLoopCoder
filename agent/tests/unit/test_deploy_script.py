"""Smoke tests for scripts/deploy.sh.

The script SSH's to a remote host, so the only behaviors we can verify
locally are: (a) help text, (b) arg parsing failure modes, (c) dry-run
output mentions the right commands.
"""

import os
import subprocess
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_help_text():
    res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    out = res.stdout
    assert "LoopCoder bundle deployer" in out
    assert "apt-get install" in out
    assert "dpkg" in out  # mentions that we DON'T use dpkg


def test_missing_user_host_fails():
    res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode != 0


def test_unknown_flag_fails():
    res = subprocess.run(
        ["bash", str(SCRIPT), "--bogus", "user@host"], capture_output=True, text=True
    )
    assert res.returncode != 0


def test_dry_run_mentions_apt_install(tmp_path: Path):
    # Make a fake bundle so preflight passes
    bundle = tmp_path / "bundle"
    (bundle / "apt").mkdir(parents=True)
    (bundle / "wheels").mkdir()
    (bundle / "apt" / "fake.deb").write_text("not really a deb")
    (bundle / "manifest.sha256").write_text(
        f"{ 'a' * 64 }  apt/fake.deb\n"
    )
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "user@nowhere.invalid",
            "--bundle",
            str(bundle),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        # Bypass manifest verify by removing the file before running
    )
    # The manifest with a fake hash will fail; remove it for the dry-run
    (bundle / "manifest.sha256").unlink()
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "user@nowhere.invalid",
            "--bundle",
            str(bundle),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    out = res.stdout + res.stderr
    # Dry-run should announce the apt install command without contacting the host
    assert "apt-get install" in out
    assert "dpkg" not in out  # we don't recommend dpkg
    assert "rsync" in out
