"""Async event bus for live streaming session activity to subscribers.

The controller emits events via ``emit(...)`` (synchronous, non-blocking).
HTTP API / VS Code clients subscribe with ``subscribe()`` and receive an
asyncio.Queue of events. Multiple subscribers are supported.

Events are append-only and small (JSON-serializable). The store remains
the source of truth — events are *notifications*, not the data itself.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass
class Event:
    """A single broadcast event."""

    type: str  # e.g. "iter.started", "tool.called", "verify.failed"
    session_id: str
    goal_id: str | None = None
    iter: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """In-process pub/sub. Bound to a controller; survives one session."""

    def __init__(self, max_queue: int = 10_000) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue
        # History buffer for late subscribers ("replay last N events")
        self._history: list[Event] = []

    def emit(self, event: Event) -> None:
        """Synchronous emit; safe to call from controller threads.

        Subscribers receive a copy via their queue. We push history under
        a small lock to avoid races.
        """
        self._history.append(event)
        if len(self._history) > self._max_queue:
            self._history = self._history[-self._max_queue :]
        # Best-effort delivery; full queues drop oldest
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(event)
                except Exception:
                    pass

    async def subscribe(self, replay_history: bool = True) -> asyncio.Queue[Event]:
        """Return a fresh queue. If replay_history, prepend stored events."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.append(q)
            if replay_history:
                for ev in self._history:
                    await q.put(ev)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def history(self) -> Iterable[Event]:
        return tuple(self._history)
