"""Live terminal progress (rich).

Falls back to plain print when rich is unavailable or `tty: plain` is set.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    _RICH = True
except ImportError:  # pragma: no cover
    _RICH = False
    Console = None  # type: ignore[assignment]


class TtyProgress:
    def __init__(self, mode: str = "rich") -> None:
        self.mode = mode if _RICH else "plain"
        self.console = Console() if _RICH and self.mode == "rich" else None
        self._progress = None
        self._task_ids: dict[str, int] = {}

    @contextmanager
    def session(self, label: str) -> Iterator["TtyProgress"]:
        if self.mode == "plain":
            print(f"=== {label} ===")
            yield self
            return
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            self._progress = progress
            yield self
            self._progress = None
            self._task_ids = {}

    def add_goal(self, goal_id: str, total_iters: int) -> None:
        if self._progress is None:
            print(f"-- goal {goal_id} (max {total_iters} iters)")
            return
        tid = self._progress.add_task(f"goal {goal_id}", total=total_iters)
        self._task_ids[goal_id] = tid

    def update_goal(self, goal_id: str, iter_: int, status: str) -> None:
        if self._progress is None:
            print(f"   [{goal_id}] iter {iter_} -> {status}")
            return
        if goal_id in self._task_ids:
            self._progress.update(self._task_ids[goal_id], completed=iter_, description=f"{goal_id} ({status})")

    def info(self, msg: str) -> None:
        if self.console:
            self.console.print(msg)
        else:
            print(msg, file=sys.stderr)
