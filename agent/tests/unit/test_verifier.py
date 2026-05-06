"""Tests for the Verifier (acceptance executor)."""

import os
from pathlib import Path

from loopcoder.loop.verifier import Verifier
from loopcoder.plan.schema import (
    FileContainsAcceptance,
    FileExistsAcceptance,
    FileNotContainsAcceptance,
    ShellAcceptance,
)


def test_shell_acceptance_passes(tmp_path: Path):
    v = Verifier(str(tmp_path))
    res = v.run([ShellAcceptance(run="echo ok")])
    assert res.passed
    assert "PASS" in res.log


def test_shell_acceptance_exit_code_check(tmp_path: Path):
    v = Verifier(str(tmp_path))
    res = v.run([ShellAcceptance(run="false")])
    assert not res.passed


def test_shell_stdout_contains(tmp_path: Path):
    v = Verifier(str(tmp_path))
    from loopcoder.plan.schema import ShellExpect
    res = v.run([ShellAcceptance(run="echo hello", expect=ShellExpect(stdout_contains="hell"))])
    assert res.passed


def test_shell_stdout_contains_fails_when_missing(tmp_path: Path):
    v = Verifier(str(tmp_path))
    from loopcoder.plan.schema import ShellExpect
    res = v.run([ShellAcceptance(run="echo hello", expect=ShellExpect(stdout_contains="goodbye"))])
    assert not res.passed


def test_shell_timeout(tmp_path: Path):
    v = Verifier(str(tmp_path))
    res = v.run([ShellAcceptance(run="sleep 5", timeout=1)])
    assert not res.passed
    assert "timed out" in res.log


def test_file_exists(tmp_path: Path):
    (tmp_path / "x.txt").write_text("hi")
    v = Verifier(str(tmp_path))
    assert v.run([FileExistsAcceptance(path="x.txt")]).passed
    assert not v.run([FileExistsAcceptance(path="missing.txt")]).passed


def test_file_contains(tmp_path: Path):
    (tmp_path / "x.txt").write_text("import os\n")
    v = Verifier(str(tmp_path))
    assert v.run([FileContainsAcceptance(path="x.txt", pattern=r"import\s+\w+")]).passed
    assert not v.run([FileContainsAcceptance(path="x.txt", pattern="from .* import")]).passed


def test_file_not_contains(tmp_path: Path):
    (tmp_path / "x.txt").write_text("safe\n")
    v = Verifier(str(tmp_path))
    assert v.run([FileNotContainsAcceptance(path="x.txt", pattern=r"SECRET")]).passed
    (tmp_path / "y.txt").write_text("SECRET=hidden\n")
    assert not v.run([FileNotContainsAcceptance(path="y.txt", pattern=r"SECRET")]).passed


def test_combined_all_pass(tmp_path: Path):
    (tmp_path / "x.txt").write_text("hello\n")
    v = Verifier(str(tmp_path))
    res = v.run([
        ShellAcceptance(run="true"),
        FileExistsAcceptance(path="x.txt"),
        FileContainsAcceptance(path="x.txt", pattern="hello"),
    ])
    assert res.passed
    assert res.short_summary().startswith("3/3")


def test_combined_one_fail(tmp_path: Path):
    v = Verifier(str(tmp_path))
    res = v.run([
        ShellAcceptance(run="true"),
        FileExistsAcceptance(path="missing.txt"),
    ])
    assert not res.passed
    assert "1/2" in res.short_summary() or "FAIL" in res.log
