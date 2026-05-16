"""Tests for plan schema validation."""

import pytest
import yaml

from loopcoder.plan import load_plan
from loopcoder.plan.schema import (
    Plan,
    ShellAcceptance,
)


def _minimal_plan_dict() -> dict:
    return {
        "project": {"name": "p", "workspace": "/tmp/p"},
        "goals": [
            {
                "id": "g1",
                "title": "t",
                "description": "d",
                "acceptance": [{"kind": "shell", "run": "true"}],
            }
        ],
    }


def test_minimal_plan_validates():
    p = Plan.model_validate(_minimal_plan_dict())
    assert p.project.name == "p"
    assert len(p.goals) == 1
    assert isinstance(p.goals[0].acceptance[0], ShellAcceptance)


def test_plan_must_have_at_least_one_goal():
    bad = _minimal_plan_dict()
    bad["goals"] = []
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_acceptance_required():
    bad = _minimal_plan_dict()
    bad["goals"][0]["acceptance"] = []
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_duplicate_goal_ids_rejected():
    bad = _minimal_plan_dict()
    bad["goals"].append({**bad["goals"][0]})
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_unknown_dependency_rejected():
    bad = _minimal_plan_dict()
    bad["goals"][0]["depends_on"] = ["nonexistent"]
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_self_dependency_rejected():
    bad = _minimal_plan_dict()
    bad["goals"][0]["depends_on"] = ["g1"]
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_invalid_goal_id_rejected():
    bad = _minimal_plan_dict()
    bad["goals"][0]["id"] = "has spaces"
    with pytest.raises(Exception):
        Plan.model_validate(bad)


def test_all_acceptance_kinds_parse():
    p = _minimal_plan_dict()
    p["goals"][0]["acceptance"] = [
        {"kind": "shell", "run": "true"},
        {"kind": "file_exists", "path": "x"},
        {"kind": "file_contains", "path": "x", "pattern": "y"},
        {"kind": "file_not_contains", "path": "x", "pattern": "y"},
        {"kind": "http", "request": {"method": "GET", "url": "http://x"}},
    ]
    plan = Plan.model_validate(p)
    kinds = [type(a).__name__ for a in plan.goals[0].acceptance]
    assert kinds == [
        "ShellAcceptance",
        "FileExistsAcceptance",
        "FileContainsAcceptance",
        "FileNotContainsAcceptance",
        "HttpAcceptance",
    ]


def test_load_plan_yaml(tmp_path):
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(_minimal_plan_dict()))
    plan = load_plan(p)
    assert plan.goals[0].id == "g1"


def test_load_plan_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_plan(tmp_path / "no.yaml")


def test_constraints_defaults():
    p = Plan.model_validate(_minimal_plan_dict())
    assert p.constraints.max_iterations_per_goal == 50
    assert p.constraints.network_allowed is False
