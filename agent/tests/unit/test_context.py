"""Tests for ContextBuilder (★ CC critical preservation)."""

import pytest

from loopcoder.llm.context import (
    ContextBuilder,
    ContextOverflowError,
    ContextSection,
)
from loopcoder.llm.tokens import TokenCounter


def _section(kind, role, content):
    return ContextSection(kind=kind, role=role, content=content)


def test_pack_within_budget_keeps_all():
    cb = ContextBuilder(TokenCounter(), total_budget_tokens=10_000, reserve_for_completion=2000)
    cb.add(_section("system", "system", "you are an agent"))
    cb.add(_section("goal", "user", "do a thing"))
    cb.add(_section("verify_log", "user", "PASS"))
    msgs, report = cb.pack()
    assert len(msgs) == 3
    assert report.dropped == []


def test_pack_drops_lowest_priority_first():
    cb = ContextBuilder(TokenCounter(), total_budget_tokens=200, reserve_for_completion=20)
    cb.add(_section("system", "system", "sys"))
    cb.add(_section("goal", "user", "g"))
    cb.add(_section("verify_log", "user", "log"))
    cb.add(_section("attempt", "tool", "x" * 4000))  # very large -> low priority
    msgs, report = cb.pack()
    dropped_kinds = [s.kind for s in report.dropped]
    assert "attempt" in dropped_kinds
    kinds = [m.get("role") for m in msgs]
    # system, goal (user), verify_log (user) -> 3 messages
    assert "system" in kinds
    assert kinds.count("user") >= 2


def test_never_truncate_overflow_raises():
    cb = ContextBuilder(TokenCounter(), total_budget_tokens=20, reserve_for_completion=10)
    cb.add(_section("system", "system", "s" * 4000))
    cb.add(_section("goal", "user", "g" * 4000))
    cb.add(_section("verify_log", "user", "v" * 4000))
    # All non-droppable; budget too small
    with pytest.raises(ContextOverflowError):
        cb.pack()


def test_verify_log_preserved_over_attempts():
    cb = ContextBuilder(TokenCounter(), total_budget_tokens=400, reserve_for_completion=20)
    cb.add(_section("system", "system", "s"))
    cb.add(_section("goal", "user", "g"))
    big_attempt = _section("attempt", "user", "noise " * 200)
    cb.add(big_attempt)
    important_log = _section("verify_log", "user", "FAIL critical info")
    cb.add(important_log)
    msgs, report = cb.pack()
    contents = " ".join(m["content"] for m in msgs)
    assert "FAIL critical info" in contents
