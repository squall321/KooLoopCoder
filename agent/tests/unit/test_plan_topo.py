"""Tests for plan topological ordering."""

import pytest

from loopcoder.plan import topological_order
from loopcoder.plan.schema import Plan


def _plan(goals: list[dict]) -> Plan:
    return Plan.model_validate(
        {
            "project": {"name": "p", "workspace": "/tmp"},
            "goals": [
                {**g, "acceptance": [{"kind": "shell", "run": "true"}]}
                for g in goals
            ],
        }
    )


def test_simple_chain():
    p = _plan([
        {"id": "a", "title": "a", "description": "a"},
        {"id": "b", "title": "b", "description": "b", "depends_on": ["a"]},
        {"id": "c", "title": "c", "description": "c", "depends_on": ["b"]},
    ])
    order = [g.id for g in topological_order(p)]
    assert order == ["a", "b", "c"]


def test_priority_tiebreak():
    p = _plan([
        {"id": "a", "title": "a", "description": "a", "priority": 200},
        {"id": "b", "title": "b", "description": "b", "priority": 100},
    ])
    order = [g.id for g in topological_order(p)]
    assert order == ["b", "a"]  # lower priority first among ready


def test_independent_goals_use_id_as_tiebreak():
    p = _plan([
        {"id": "z", "title": "z", "description": "z"},
        {"id": "a", "title": "a", "description": "a"},
    ])
    order = [g.id for g in topological_order(p)]
    assert order == ["a", "z"]


def test_diamond():
    p = _plan([
        {"id": "a", "title": "a", "description": "a"},
        {"id": "b", "title": "b", "description": "b", "depends_on": ["a"]},
        {"id": "c", "title": "c", "description": "c", "depends_on": ["a"]},
        {"id": "d", "title": "d", "description": "d", "depends_on": ["b", "c"]},
    ])
    order = [g.id for g in topological_order(p)]
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")
