"""Tests for system-reminder builder (CC4)."""

from loopcoder.loop.reminders import ReminderState, build_reminder


def test_reminder_includes_state_summary():
    state = ReminderState(
        goal_id="g1",
        acceptance_count=3,
        consecutive_failures=2,
        iteration=4,
        max_iter=50,
        used_tokens=120000,
        budget_tokens=240000,
        in_progress_todo="Adding JWT decode helper",
        written_files_unread=["src/auth.py"],
        background_running=1,
    )
    sec = build_reminder(state)
    assert sec.role == "system"
    text = sec.content
    assert "g1" in text
    assert "4/50" in text
    assert "consecutive failures: 2" in text
    assert "120000/240000" in text
    assert "JWT decode helper" in text
    assert "src/auth.py" in text
    assert "background jobs running: 1" in text
    assert "Verification runs OUTSIDE" in text  # rule preserved


def test_reminder_minimal_state():
    state = ReminderState(goal_id="g1", acceptance_count=1, consecutive_failures=0,
                          iteration=1, max_iter=0)
    sec = build_reminder(state)
    assert "g1" in sec.content
    # Optional fields not displayed when absent
    assert "consecutive failures" not in sec.content
    assert "context:" not in sec.content
