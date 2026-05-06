"""Plan loader.

Accepts YAML files (the canonical format). Markdown front-matter could be
added later, but YAML is unambiguous and what the rest of the pipeline expects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from loopcoder.plan.schema import Plan


def load_plan(path: str | Path) -> Plan:
    """Load and validate a plan from a YAML file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"plan file not found: {p}")
    raw: Any = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level must be a mapping")
    return Plan.model_validate(raw)


def dump_plan(plan: Plan, path: str | Path) -> None:
    """Write a plan back as YAML (useful for tests / round-trips)."""
    Path(path).write_text(yaml.safe_dump(plan.model_dump(), sort_keys=False))
