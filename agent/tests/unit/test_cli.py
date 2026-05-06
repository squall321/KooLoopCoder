"""CLI smoke tests using click.testing."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from loopcoder import __version__
from loopcoder.cli import main


def _write_dev_loopcoder_yaml(tmp_path: Path) -> Path:
    cfg = tmp_path / "loopcoder.yaml"
    cfg.write_text(f"""
storage:
  state_db: {tmp_path}/state.db
  log_dir: {tmp_path}/logs
  workspaces_root: {tmp_path}/ws
sandbox:
  backend: host
""")
    return cfg


def test_version_flag():
    r = CliRunner().invoke(main, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.output


def test_help_lists_subcommands():
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0
    for cmd in ["run", "list", "status", "report", "tokens", "export", "config"]:
        assert cmd in r.output


def test_list_no_sessions(tmp_path, monkeypatch):
    cfg = _write_dev_loopcoder_yaml(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    r = CliRunner().invoke(main, ["list"])
    assert r.exit_code == 0
    assert "no sessions" in r.output.lower()


def test_dry_run_passes_when_workspace_already_satisfies(tmp_path, monkeypatch):
    cfg = _write_dev_loopcoder_yaml(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hi')\n")
    plan = tmp_path / "plan.yaml"
    plan.write_text(f"""
project:
  name: x
  workspace: {ws}
goals:
  - id: g1
    title: t
    description: d
    acceptance:
      - kind: file_exists
        path: hello.py
""")
    r = CliRunner().invoke(main, ["run", "--plan", str(plan), "--dry-run"])
    assert r.exit_code == 0
    assert "g1" in r.output


def test_dry_run_fails_with_exit_2_when_unsatisfied(tmp_path, monkeypatch):
    cfg = _write_dev_loopcoder_yaml(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    ws = tmp_path / "ws"
    ws.mkdir()
    plan = tmp_path / "plan.yaml"
    plan.write_text(f"""
project:
  name: x
  workspace: {ws}
goals:
  - id: g1
    title: t
    description: d
    acceptance:
      - kind: file_exists
        path: missing.py
""")
    r = CliRunner().invoke(main, ["run", "--plan", str(plan), "--dry-run"])
    assert r.exit_code == 2


def test_export_session_to_tarball(tmp_path, monkeypatch):
    from loopcoder.state.store import SessionStore

    cfg = _write_dev_loopcoder_yaml(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    store = SessionStore(str(tmp_path / "state.db"))
    sid = store.start_session(plan_path="x")
    store.start_goal(sid, "g1")
    store.record_iteration(sid, "g1", 1, None, 100, 50, True, "PASS", 1.0, 2.0)
    store.end_goal(sid, "g1", "passed", 1)
    store.end_session(sid, "completed")

    out = tmp_path / "exp.tar.gz"
    r = CliRunner().invoke(main, ["export", sid, "--out", str(out)])
    assert r.exit_code == 0
    assert out.is_file() and out.stat().st_size > 0


def test_export_unknown_session(tmp_path, monkeypatch):
    cfg = _write_dev_loopcoder_yaml(tmp_path)
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    out = tmp_path / "exp.tar.gz"
    r = CliRunner().invoke(main, ["export", "nope", "--out", str(out)])
    assert r.exit_code == 1
