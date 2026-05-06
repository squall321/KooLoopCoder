"""Mock-LLM end-to-end harness.

These scenarios verify SYSTEM behavior — the loop controller, verifier,
snapshot manager, hook system — independent of any real model. They run
fast (< 5 s), are fully deterministic, and stress the parts of LoopCoder
that the user explicitly cares about:
  * "the LLM cannot fake completion" (E2E-5: lying-submit_goal still verifies)
  * "infinite loops do not happen" (E2E-4: max_iter triggers clean fail)
  * "rollback works" (E2E-6: forced repeated failures rewind history)
  * "context preservation" (E2E-7: verify logs survive across iters)

The MockLlmClient below is shared via a fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pytest
import yaml

from loopcoder.config import LoopCoderConfig
from loopcoder.llm.client import LlmResponse, LlmToolCall
from loopcoder.loop.controller import LoopController
from loopcoder.plan import load_plan
from loopcoder.sandbox.host import HostSandbox
from loopcoder.state.store import SessionStore


class MockLlmClient:
    """A scriptable LLM. Each chat() call returns the next scripted response.

    Past the script end it falls back to a no-op response so the loop ends
    cleanly via verification rather than an exception. We track every call
    in ``self.calls`` so tests can assert on prompt content / tools list.
    """

    def __init__(self, scripted: Iterable[LlmResponse], model: str = "mock") -> None:
        self.scripted: list[LlmResponse] = list(scripted)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": list(messages), "tools": list(tools or []), **kwargs})
        if self.scripted:
            return self.scripted.pop(0)
        # Empty fallback — verifier still runs and (likely) fails, ending the loop.
        return LlmResponse(content="(no more scripted responses)", tool_calls=[],
                           prompt_tokens=10, completion_tokens=2)


# ---------- helpers used by tests ----------


def make_tool_call(call_id: str, name: str, args: dict[str, Any]) -> LlmToolCall:
    return LlmToolCall(id=call_id, name=name, arguments=json.dumps(args))


def write_plan(tmp_path: Path, plan_dict: dict) -> Path:
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(plan_dict))
    return p


def make_config(tmp_path: Path, *, max_iter: int = 5, rollback_after: int = 999,
                strategy_change_after: int = 99) -> LoopCoderConfig:
    return LoopCoderConfig.model_validate({
        "llm": {"base_url": "http://mock", "model": "mock"},
        "sandbox": {"backend": "host"},
        "context": {"total_budget_tokens": 100_000, "reserve_for_completion": 1000},
        "tools": {
            "shell": {"allowed_patterns": ["*"]},
            "fs": {"forbidden_paths": []},
        },
        "loop": {
            "max_iterations_per_goal": max_iter,
            "rollback_after": rollback_after,
            "strategy_change_after": strategy_change_after,
        },
        "storage": {
            "state_db": str(tmp_path / "state.db"),
            "log_dir": str(tmp_path / "logs"),
            "workspaces_root": str(tmp_path / "ws"),
        },
    })


@pytest.fixture
def make_workspace(tmp_path: Path):
    """Return a factory: callable(name) -> Path of a fresh workspace dir."""
    def _factory(name: str = "ws") -> Path:
        ws = tmp_path / name
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    return _factory


@pytest.fixture
def run_controller(tmp_path: Path):
    """Return a callable that wires the controller to a mock and runs it."""

    def _run(plan_dict: dict, scripted_responses: list[LlmResponse],
             *, max_iter: int = 5, rollback_after: int = 999,
             strategy_change_after: int = 99):
        plan_path = write_plan(tmp_path, plan_dict)
        plan = load_plan(plan_path)
        cfg = make_config(tmp_path, max_iter=max_iter, rollback_after=rollback_after,
                          strategy_change_after=strategy_change_after)
        client = MockLlmClient(scripted_responses)
        store = SessionStore(cfg.storage.state_db)
        sandbox = HostSandbox()
        controller = LoopController(plan, cfg, client, sandbox, store)
        outcomes = controller.run(plan_path=str(plan_path))
        return controller, store, client, outcomes

    return _run
