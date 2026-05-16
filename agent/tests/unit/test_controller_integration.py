"""End-to-end controller test using a mock LLM.

Drives the full LoopController lifecycle without needing a real vLLM:
- the mock client returns predictable tool_calls so we can verify the
  loop, verifier, snapshots, and state store all integrate correctly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


from loopcoder.config import LoopCoderConfig
from loopcoder.llm.client import LlmResponse, LlmToolCall
from loopcoder.loop.controller import LoopController
from loopcoder.plan import load_plan
from loopcoder.sandbox.host import HostSandbox
from loopcoder.state.store import SessionStore


class MockLlmClient:
    """Records calls; replays scripted responses."""

    def __init__(self, scripted_responses: list[LlmResponse]) -> None:
        self.scripted = list(scripted_responses)
        self.calls: list[dict[str, Any]] = []
        self.model = "mock"

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools, **kwargs})
        if not self.scripted:
            # Default: empty response (no tool calls). Forces verify-fail to break loop.
            return LlmResponse(content="(end)", tool_calls=[], prompt_tokens=10, completion_tokens=2)
        return self.scripted.pop(0)


def _plan(tmp_path: Path) -> dict:
    return {
        "project": {"name": "demo", "workspace": str(tmp_path)},
        "constraints": {"max_iterations_per_goal": 5, "max_total_minutes": 5},
        "goals": [
            {
                "id": "create",
                "title": "create file",
                "description": "create hello.py with content 'print(\"hi\")'",
                "acceptance": [
                    {"kind": "file_exists", "path": "hello.py"},
                    {"kind": "file_contains", "path": "hello.py", "pattern": "print"},
                ],
            }
        ],
    }


def _config(tmp_path: Path) -> LoopCoderConfig:
    cfg = LoopCoderConfig.model_validate({
        "llm": {"base_url": "http://mock", "model": "mock"},
        "sandbox": {"backend": "host"},
        "context": {"total_budget_tokens": 100000, "reserve_for_completion": 1000},
        "tools": {"shell": {"allowed_patterns": ["*"]}, "fs": {"forbidden_paths": []}},
        "loop": {"max_iterations_per_goal": 5, "rollback_after": 999},
        "storage": {
            "state_db": str(tmp_path / "state.db"),
            "log_dir": str(tmp_path / "logs"),
            "workspaces_root": str(tmp_path / "ws"),
        },
    })
    return cfg


def test_controller_passes_one_goal(tmp_path: Path, monkeypatch):
    # Build a workspace + plan
    ws = tmp_path / "workspace"
    ws.mkdir()
    plan_path = tmp_path / "plan.yaml"
    import yaml
    plan_dict = _plan(ws)
    plan_path.write_text(yaml.safe_dump(plan_dict))
    plan = load_plan(plan_path)

    # Mock LLM: single tool call to write_file with the right content
    write_call = LlmToolCall(
        id="call_1",
        name="write_file",
        arguments=json.dumps({"path": "hello.py", "content": "print('hi')\n"}),
    )
    response = LlmResponse(
        content="creating file",
        tool_calls=[write_call],
        prompt_tokens=100,
        completion_tokens=20,
    )
    client = MockLlmClient([response])

    cfg = _config(tmp_path)
    sandbox = HostSandbox()
    store = SessionStore(cfg.storage.state_db)

    controller = LoopController(plan, cfg, client, sandbox, store)
    outcomes = controller.run(plan_path=str(plan_path))

    assert len(outcomes) == 1
    assert outcomes[0].goal_id == "create"
    assert outcomes[0].status == "passed"
    assert outcomes[0].iterations == 1
    # File was actually created
    assert (ws / "hello.py").read_text() == "print('hi')\n"
    # Mock got called once
    assert len(client.calls) == 1
    # Tool schemas were passed
    tool_names = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert "write_file" in tool_names
    assert "read_file" in tool_names
    assert "todo_write" in tool_names


def test_controller_iterates_until_pass(tmp_path: Path):
    """Verify the loop retries until acceptance passes."""
    ws = tmp_path / "ws"
    ws.mkdir()
    plan_path = tmp_path / "plan.yaml"
    import yaml
    plan_path.write_text(yaml.safe_dump(_plan(ws)))
    plan = load_plan(plan_path)

    # First response: write WRONG content (no 'print') → fails file_contains
    bad = LlmResponse(
        content="attempt 1",
        tool_calls=[LlmToolCall(id="c1", name="write_file",
                                arguments=json.dumps({"path": "hello.py", "content": "x = 1\n"}))],
        prompt_tokens=50, completion_tokens=10,
    )
    # Second response: now read the file then edit it to add print
    read_call = LlmToolCall(id="c2", name="read_file",
                            arguments=json.dumps({"path": "hello.py"}))
    edit_call = LlmToolCall(id="c3", name="edit_file",
                            arguments=json.dumps({"path": "hello.py", "old": "x = 1", "new": "print('hi')"}))
    good = LlmResponse(
        content="attempt 2",
        tool_calls=[read_call, edit_call],
        prompt_tokens=80, completion_tokens=15,
    )
    client = MockLlmClient([bad, good])

    cfg = _config(tmp_path)
    sandbox = HostSandbox()
    store = SessionStore(cfg.storage.state_db)
    controller = LoopController(plan, cfg, client, sandbox, store)
    outcomes = controller.run(plan_path=str(plan_path))

    assert outcomes[0].status == "passed"
    assert outcomes[0].iterations == 2
    assert "print" in (ws / "hello.py").read_text()


def test_controller_fails_after_max_iter(tmp_path: Path):
    """If LLM never produces a passing solution, we hit max_iter and fail cleanly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    plan_path = tmp_path / "plan.yaml"
    import yaml
    plan_dict = _plan(ws)
    plan_dict["constraints"]["max_iterations_per_goal"] = 2
    plan_path.write_text(yaml.safe_dump(plan_dict))
    plan = load_plan(plan_path)

    # All responses write unrelated content
    def bad(i):
        return LlmResponse(
            content=f"bad {i}",
            tool_calls=[LlmToolCall(id=f"c{i}", name="write_file",
                                    arguments=json.dumps({"path": "other.txt", "content": "no\n"}))],
            prompt_tokens=10, completion_tokens=5,
        )
    client = MockLlmClient([bad(1), bad(2), bad(3), bad(4)])

    cfg = _config(tmp_path)
    sandbox = HostSandbox()
    store = SessionStore(cfg.storage.state_db)
    controller = LoopController(plan, cfg, client, sandbox, store)
    outcomes = controller.run(plan_path=str(plan_path))

    assert outcomes[0].status == "failed"
    # We attempted up to max_iter; after that the loop ends cleanly
    assert outcomes[0].iterations <= 2


def test_controller_records_tokens(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    plan_path = tmp_path / "plan.yaml"
    import yaml
    plan_path.write_text(yaml.safe_dump(_plan(ws)))
    plan = load_plan(plan_path)

    response = LlmResponse(
        content="ok",
        tool_calls=[LlmToolCall(id="c1", name="write_file",
                                arguments=json.dumps({"path": "hello.py", "content": "print('hi')\n"}))],
        prompt_tokens=1234, completion_tokens=567,
    )
    client = MockLlmClient([response])
    cfg = _config(tmp_path)
    sandbox = HostSandbox()
    store = SessionStore(cfg.storage.state_db)
    controller = LoopController(plan, cfg, client, sandbox, store)
    controller.run(plan_path=str(plan_path))

    sess = store.session_status(controller.session_id)
    assert sess["total_prompt_tokens"] >= 1234
    assert sess["total_completion_tokens"] >= 567
