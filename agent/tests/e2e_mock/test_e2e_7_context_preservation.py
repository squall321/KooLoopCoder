"""E2E-7 — verify logs are preserved across iterations (no truncation).

Verification logs are the agent's primary feedback signal. Per PLAN
§5.7, they must NEVER be summarized or dropped, even when context is
tight. We verify this by:
  1. Crafting an acceptance check whose failure produces a *long* log
     (a unique sentinel string buried at the very end).
  2. Letting the loop run a few iters.
  3. Asserting the sentinel is present in the iteration record stored
     in SQLite, and that the ContextBuilder priority puts verify_log
     among NEVER_TRUNCATE.
"""

from loopcoder.llm.client import LlmResponse
from loopcoder.llm.context import NEVER_TRUNCATE, PRIORITY
from .conftest import make_tool_call


def test_e2e7_verify_log_priority():
    """Static invariant: the priority table must keep verify_log non-droppable."""
    assert "verify_log" in NEVER_TRUNCATE
    # Among non-system sections, verify_log has higher priority than attempts.
    assert PRIORITY["verify_log"] < PRIORITY["attempt"]
    assert PRIORITY["verify_log"] < PRIORITY["git_diff"]


PLAN = {
    "project": {"name": "ctx-pres", "workspace": "{WS}"},
    "constraints": {"max_iterations_per_goal": 2},
    "goals": [{
        "id": "g1",
        "title": "prints sentinel",
        "description": "the script must print the sentinel string",
        "acceptance": [
            {
                "kind": "shell",
                # Long script whose tail contains a unique sentinel; the verifier
                # captures the full stdout/stderr so the sentinel ends up in
                # the verify log even though it sits AFTER lots of noise.
                "run": (
                    "python3 -c \"import sys; "
                    "[print('noise '*40) for _ in range(20)]; "
                    "sys.stderr.write('ZZZ_SENTINEL_END\\n'); "
                    "sys.exit(2)\""
                ),
                "expect": {"exit_code": 0},  # always fail (rc=2) → log is captured
            },
        ],
    }],
}


def _useless() -> LlmResponse:
    return LlmResponse(
        content="trying",
        tool_calls=[make_tool_call("c1", "record_thought",
                                    {"text": "looking at the failure"})],
        prompt_tokens=10, completion_tokens=3,
    )


def test_e2e7_long_failure_log_kept_intact(run_controller, make_workspace):
    ws = make_workspace("e2e7")
    plan = dict(PLAN)
    plan["project"] = {**plan["project"], "workspace": str(ws)}

    scripted = [_useless(), _useless()]
    controller, store, client, outcomes = run_controller(plan, scripted, max_iter=2)

    assert outcomes[0].status == "failed"
    iters = store.iterations_for(controller.session_id, "g1")
    assert len(iters) == 2
    # The sentinel made it into the persisted verify_log of every iteration.
    for it in iters:
        log_text = it["verify_log"] or ""
        assert "ZZZ_SENTINEL_END" in log_text, \
            f"verify log was truncated; tail: {log_text[-200:]}"
