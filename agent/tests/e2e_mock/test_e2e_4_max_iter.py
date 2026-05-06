"""E2E-4 — infinite-loop prevention.

If the LLM keeps producing useless tool calls, the controller must
terminate cleanly after ``max_iterations_per_goal`` and report failure.
The session store must reflect ``status=failed`` and the iteration count
must equal max_iter.
"""

from loopcoder.llm.client import LlmResponse
from .conftest import make_tool_call


def _useless(idx: int) -> LlmResponse:
    """A response whose only effect is one allowed but unhelpful tool call."""
    return LlmResponse(
        content=f"attempt {idx}",
        tool_calls=[make_tool_call(f"c{idx}", "write_file",
                                   {"path": f"junk_{idx}.txt", "content": "still wrong\n"})],
        prompt_tokens=20, completion_tokens=5,
    )


PLAN_DICT = {
    "project": {"name": "infinite-fail", "workspace": "{WS}"},
    "constraints": {"max_iterations_per_goal": 3},  # tight
    "goals": [{
        "id": "impossible",
        "title": "always-failing acceptance",
        "description": "the agent can never satisfy this",
        "acceptance": [
            {"kind": "file_exists", "path": "never_created.txt"},
        ],
    }],
}


def test_e2e4_max_iter_terminates(run_controller, make_workspace):
    ws = make_workspace("e2e4")
    plan = dict(PLAN_DICT)
    plan["project"] = {**plan["project"], "workspace": str(ws)}

    scripted = [_useless(i) for i in range(1, 10)]
    controller, store, client, outcomes = run_controller(plan, scripted, max_iter=3)

    assert len(outcomes) == 1
    assert outcomes[0].goal_id == "impossible"
    assert outcomes[0].status == "failed"
    # Hit the cap exactly
    assert outcomes[0].iterations == 3
    # Persisted state agrees
    goals = store.goals_for(controller.session_id)
    assert goals[0]["status"] == "failed"
    assert goals[0]["iterations"] == 3
    # Token usage was recorded
    sess = store.session_status(controller.session_id)
    assert sess["total_prompt_tokens"] >= 60   # 3 * 20
    # The fact-of-life file was NEVER created (verifier kept failing)
    assert not (ws / "never_created.txt").exists()
