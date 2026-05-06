"""SessionRunner — the bridge between FastAPI and the synchronous controller.

The controller blocks; we run it on a worker thread per session and expose
the EventBus to /events/* SSE endpoints.

This module deliberately keeps state in memory (a dict of active runners).
Persistence is handled by SessionStore. If the API process restarts, in-flight
sessions die with it — but the SQLite log up to the last iter is preserved,
which matches the rest of LoopCoder's "everything is checkpointed" philosophy.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from loopcoder.config import LoopCoderConfig
from loopcoder.events import EventBus
from loopcoder.llm.client import LlmClient
from loopcoder.loop.controller import LoopController
from loopcoder.plan.parser import load_plan
from loopcoder.plan.schema import Plan
from loopcoder.sandbox import make_sandbox
from loopcoder.state.store import SessionStore


@dataclass
class ActiveRunner:
    session_id: str
    controller: LoopController
    thread: threading.Thread
    started_at: float
    plan_name: str


class SessionRunnerRegistry:
    def __init__(self) -> None:
        self._runners: dict[str, ActiveRunner] = {}
        self._lock = threading.Lock()

    def list(self) -> list[ActiveRunner]:
        with self._lock:
            return list(self._runners.values())

    def get(self, session_id: str) -> ActiveRunner | None:
        with self._lock:
            return self._runners.get(session_id)

    def register(self, runner: ActiveRunner) -> None:
        with self._lock:
            self._runners[runner.session_id] = runner

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._runners.pop(session_id, None)


# ---------- factory ----------


def start_session(
    plan: Plan,
    cfg: LoopCoderConfig,
    plan_path: str | None,
    only_goal: str | None,
    registry_runners: SessionRunnerRegistry,
) -> ActiveRunner:
    """Spin up a controller in a thread and return its runner record."""

    client = LlmClient(
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key,
        model=plan.llm.model or cfg.llm.model,
        timeout_sec=cfg.llm.request_timeout_sec,
        max_attempts=cfg.llm.retry.max_attempts,
        backoff_initial_sec=cfg.llm.retry.backoff_initial_sec,
        backoff_max_sec=cfg.llm.retry.backoff_max_sec,
    )
    sandbox = (
        make_sandbox(
            cfg.sandbox.backend,
            image=cfg.sandbox.image,
            bind_mounts=[bm.model_dump() for bm in cfg.sandbox.bind_mounts],
            network=cfg.sandbox.network or plan.constraints.network_allowed,
            read_only_paths=cfg.sandbox.read_only_paths,
            default_cwd=cfg.sandbox.default_cwd,
        )
        if cfg.sandbox.backend == "apptainer"
        else make_sandbox(cfg.sandbox.backend, workspace=plan.project.workspace)
    )
    store = SessionStore(cfg.storage.state_db)
    bus = EventBus()
    controller = LoopController(plan, cfg, client, sandbox, store, events=bus)

    placeholder_id = ""

    def _run() -> None:
        try:
            controller.run(only_goal=only_goal, plan_path=plan_path)
        finally:
            registry_runners.remove(controller.session_id or placeholder_id)

    th = threading.Thread(target=_run, daemon=True, name=f"loopcoder-runner")
    th.start()

    # Wait briefly for session_id to be assigned by controller.run()
    deadline = time.time() + 5.0
    while not controller.session_id and time.time() < deadline:
        time.sleep(0.02)
    if not controller.session_id:
        raise RuntimeError("session_id not assigned in time")

    runner = ActiveRunner(
        session_id=controller.session_id,
        controller=controller,
        thread=th,
        started_at=time.time(),
        plan_name=plan.project.name,
    )
    registry_runners.register(runner)
    return runner


def load_plan_dict(plan_dict: dict[str, Any]) -> Plan:
    return Plan.model_validate(plan_dict)


def load_plan_from_path(path: str) -> Plan:
    return load_plan(path)
