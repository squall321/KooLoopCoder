"""Smoke tests for the HPC (Slurm) mode scripts.

These run apptainer/sbatch on a real cluster, so locally we only verify
the host-checkable behavior: existence, executability, syntax, help
text, the no-sudo/no-systemd guarantee, init creating the layout under
$LOOPCODER_HOME, and sbatch templates rendering with no leftover tokens.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HPC = REPO_ROOT / "scripts" / "hpc" / "loopcoder-hpc.sh"
T_AIO = REPO_ROOT / "scripts" / "hpc" / "sbatch-allinone.sh.tmpl"
T_SRV = REPO_ROOT / "scripts" / "hpc" / "sbatch-serve.sh.tmpl"


def test_files_exist_and_executable():
    assert HPC.is_file() and os.access(HPC, os.X_OK)
    assert T_AIO.is_file()
    assert T_SRV.is_file()


def test_bash_syntax_ok():
    for f in (HPC, T_AIO, T_SRV):
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f}: {r.stderr}"


def test_no_sudo_no_systemd():
    # Only inspect executable lines; the header comment legitimately
    # says "no sudo, no systemd, no root".
    code = [
        ln for ln in HPC.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    body = "\n".join(code)
    assert "sudo" not in body, "HPC path must not use sudo"
    assert "systemctl" not in body and "systemd" not in body, "HPC path must not use systemd"


def test_help_text():
    r = subprocess.run(["bash", str(HPC), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "no sudo, no systemd, no root" in r.stdout


def test_init_creates_layout_under_loopcoder_home(tmp_path: Path):
    home = tmp_path / "lh"
    env = {**os.environ, "LOOPCODER_HOME": str(home)}
    r = subprocess.run(["bash", str(HPC), "init"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    for sub in ("sif", "models", "cache", "logs", "workspaces", "state", "etc"):
        assert (home / sub).is_dir(), f"missing {sub}"


def test_unknown_subcommand_fails(tmp_path: Path):
    env = {**os.environ, "LOOPCODER_HOME": str(tmp_path / "lh")}
    r = subprocess.run(
        ["bash", str(HPC), "bogus-cmd"], capture_output=True, text=True, env=env
    )
    assert r.returncode != 0


def test_submit_allinone_renders_clean_job(tmp_path: Path):
    home = tmp_path / "lh"
    env = {**os.environ, "LOOPCODER_HOME": str(home)}
    subprocess.run(["bash", str(HPC), "init"], capture_output=True, env=env, check=True)
    plan = home / "plan.yaml"
    plan.write_text("project:\n  name: x\n")
    r = subprocess.run(
        ["bash", str(HPC), "submit-allinone", str(plan), "--model", "fast", "--gpus", "2"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    jobs = list((home / "logs").glob("sbatch-allinone.*.sh"))
    assert jobs, "no rendered sbatch job"
    rendered = jobs[0].read_text()
    # Real SBATCH directives substituted; the only AT-tokens allowed are
    # in the descriptive comment line.
    assert "#SBATCH --gres=gpu:2" in rendered
    assert f'LOOPCODER_HOME="{home}"' in rendered
    code_lines = [
        ln for ln in rendered.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    leftover = [ln for ln in code_lines if "@" in ln and ln.count("@") >= 2]
    assert not leftover, f"unsubstituted tokens in job body: {leftover}"
