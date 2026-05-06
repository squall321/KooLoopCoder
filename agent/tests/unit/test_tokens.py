"""Tests for TokenCounter."""

from loopcoder.llm.tokens import TokenCounter


def test_empty_string_zero():
    tc = TokenCounter()
    assert tc.count("") == 0


def test_count_short_string_positive():
    tc = TokenCounter()
    n = tc.count("hello world")
    assert n > 0
    assert n < 20  # generous upper bound


def test_count_messages_includes_overhead():
    tc = TokenCounter()
    msgs = [{"role": "user", "content": "hi"}]
    assert tc.count_messages(msgs) > tc.count("hi")
