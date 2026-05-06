"""Optional self-critique: ask the LLM whether the diff really solves the goal.

This is OFF by default (config.loop.use_critic=false). When enabled it adds an
extra LLM call after a goal verifies, asking the model to argue against itself.
If the critic identifies a real defect, the goal is *not* marked done and we
continue iterating.
"""

from __future__ import annotations

from dataclasses import dataclass

from loopcoder.llm.client import LlmClient


@dataclass
class CriticResult:
    accept: bool
    rationale: str


_CRITIC_SYSTEM = (
    "You are a strict code reviewer. Given a goal description, the recent diff, "
    "and the verification log, decide if the change truly satisfies the goal "
    "or if it merely makes the test pass superficially. Reply with a JSON object "
    "{\"accept\": true|false, \"rationale\": \"...\"}. Be conservative: if in "
    "doubt, set accept=false."
)


def review(client: LlmClient, goal_description: str, diff: str, verify_log: str) -> CriticResult:
    user = (
        f"## Goal\n{goal_description}\n\n"
        f"## Diff\n```diff\n{diff[:50000]}\n```\n\n"
        f"## Verification log\n```\n{verify_log[:20000]}\n```"
    )
    resp = client.chat(
        messages=[
            {"role": "system", "content": _CRITIC_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_completion_tokens=512,
    )
    text = resp.content or ""
    return _parse(text)


def _parse(text: str) -> CriticResult:
    import json
    import re
    # Try direct JSON
    try:
        obj = json.loads(text.strip())
        return CriticResult(accept=bool(obj.get("accept", False)), rationale=str(obj.get("rationale", "")))
    except Exception:
        pass
    # Try to find a JSON object in the text
    m = re.search(r"\{.*?\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return CriticResult(accept=bool(obj.get("accept", False)), rationale=str(obj.get("rationale", "")))
        except Exception:
            pass
    # Default conservative
    return CriticResult(accept=False, rationale=f"could not parse critic reply: {text[:200]}")
