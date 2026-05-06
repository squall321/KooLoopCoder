"""Strategy intervention helpers.

When the agent fails verification N times in a row on the same goal, we
inject a "step back" message to nudge it toward a different approach.
After M failures, we hard-revert to the last good snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyAction:
    inject_strategy_change: bool
    revert_to_tag: str | None


def decide(
    consecutive_failures: int,
    *,
    strategy_change_after: int,
    rollback_after: int,
    last_good_tag: str | None,
) -> StrategyAction:
    if rollback_after > 0 and consecutive_failures >= rollback_after and last_good_tag is not None:
        return StrategyAction(inject_strategy_change=True, revert_to_tag=last_good_tag)
    if strategy_change_after > 0 and consecutive_failures >= strategy_change_after:
        return StrategyAction(inject_strategy_change=True, revert_to_tag=None)
    return StrategyAction(inject_strategy_change=False, revert_to_tag=None)
