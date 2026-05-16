"""Tests for deploy.sh YAML config parsing (the embedded yaml_get helper)."""

import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"


def _write_cfg(path: Path, **overrides):
    base = {
        "target": {"host": "u@h", "remote_bundle": "/r/b", "sudo_remote": "sudo", "ssh_opts": []},
        "bundle": {"local_dir": "/data/lc"},
        "flags": {"skip_gpu_stages": False, "skip_model_stage": True},
        "model": {"mode": "none"},
    }
    # shallow override
    for k, v in overrides.items():
        if isinstance(v, dict):
            base[k] = {**base.get(k, {}), **v}
        else:
            base[k] = v
    path.write_text(yaml.safe_dump(base))


def test_dry_run_with_yaml_uses_target_host(tmp_path: Path):
    cfg = tmp_path / "deploy.yaml"
    bundle = tmp_path / "bundle"
    (bundle / "apt").mkdir(parents=True)
    (bundle / "wheels").mkdir()
    _write_cfg(cfg, target={"host": "ssh-target.invalid", "remote_bundle": "/x/y"},
               bundle={"local_dir": str(bundle)})
    res = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(cfg), "--dry-run"],
        capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert "ssh-target.invalid" in out
    assert "/x/y" in out


def test_yaml_skip_gpu_flag_propagates(tmp_path: Path):
    cfg = tmp_path / "deploy.yaml"
    bundle = tmp_path / "bundle"
    (bundle / "apt").mkdir(parents=True)
    (bundle / "wheels").mkdir()
    _write_cfg(cfg, flags={"skip_gpu_stages": True, "skip_model_stage": False},
               bundle={"local_dir": str(bundle)})
    res = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(cfg), "--dry-run"],
        capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert "--skip-gpu-stages" in out


def test_cli_overrides_yaml(tmp_path: Path):
    cfg = tmp_path / "deploy.yaml"
    bundle = tmp_path / "bundle"
    (bundle / "apt").mkdir(parents=True)
    (bundle / "wheels").mkdir()
    _write_cfg(cfg, bundle={"local_dir": str(bundle)})
    res = subprocess.run(
        ["bash", str(SCRIPT), "cli-takes-precedence@elsewhere", "--config", str(cfg), "--dry-run"],
        capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert "cli-takes-precedence@elsewhere" in out
    # Make sure the YAML host did NOT win
    assert "u@h" not in out


def test_missing_config_fails(tmp_path: Path):
    res = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(tmp_path / "no.yaml")],
        capture_output=True, text=True,
    )
    assert res.returncode != 0


def test_yaml_with_model_mode_rsync(tmp_path: Path):
    cfg = tmp_path / "deploy.yaml"
    bundle = tmp_path / "bundle"
    (bundle / "apt").mkdir(parents=True)
    (bundle / "wheels").mkdir()
    model_local = tmp_path / "model"
    model_local.mkdir()
    _write_cfg(cfg,
               bundle={"local_dir": str(bundle)},
               flags={"skip_gpu_stages": False, "skip_model_stage": False},
               model={"mode": "rsync", "local_path": str(model_local),
                      "remote_path": "/scratch/models/foo", "hf_id": ""})
    res = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(cfg), "--dry-run"],
        capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert "/scratch/models/foo" in out
    assert "rsync model" in out
