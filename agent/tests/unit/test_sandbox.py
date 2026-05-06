"""Tests for Sandbox backends."""

import shutil
from pathlib import Path

import pytest

from loopcoder.sandbox import make_sandbox
from loopcoder.sandbox.apptainer import ApptainerSandbox
from loopcoder.sandbox.host import HostSandbox


def test_host_sandbox_runs_command(tmp_path: Path):
    sb = HostSandbox()
    sb.prepare(str(tmp_path))
    r = sb.exec("echo 42", timeout=5)
    assert r.returncode == 0
    assert "42" in r.stdout


def test_host_sandbox_requires_prepare():
    sb = HostSandbox()
    with pytest.raises(RuntimeError):
        sb.exec("echo x")


def test_apptainer_render_argv():
    sb = ApptainerSandbox(
        image="/x/y.sif",
        bind_mounts=[{"source": "{workspace}", "dest": "/workspace", "mode": "rw"}],
        network=False,
        read_only_paths=["/etc"],
        default_cwd="/workspace",
    )
    sb.prepare("/tmp/ws")
    rendered = sb.render_argv("ls -la", cwd=None)
    assert "apptainer" in rendered
    assert "exec" in rendered
    assert "--containall" in rendered
    assert "--no-home" in rendered
    assert "--net" in rendered  # net=none requested
    assert "/tmp/ws:/workspace:rw" in rendered
    assert "/etc:/etc:ro" in rendered
    assert "/x/y.sif" in rendered
    assert "ls -la" in rendered


def test_apptainer_missing_binary_raises():
    sb = ApptainerSandbox(image="/x/y.sif", apptainer_bin="this_does_not_exist_xyz")
    sb.prepare("/tmp")
    with pytest.raises(RuntimeError):
        sb.exec("echo hi", timeout=5)


def test_make_sandbox_dispatch():
    h = make_sandbox("host")
    assert isinstance(h, HostSandbox)
    a = make_sandbox("apptainer", image="/x.sif")
    assert isinstance(a, ApptainerSandbox)
    with pytest.raises(ValueError):
        make_sandbox("unknown")
