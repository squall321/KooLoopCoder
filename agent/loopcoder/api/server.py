"""FastAPI server for LoopCoder.

Endpoints (versioned under /v1):

  GET    /v1/health                         — process / version info
  GET    /v1/tools                          — full ToolMeta list
  POST   /v1/tools/{name}                   — direct tool invocation (debug)

  POST   /v1/sessions                       — start session from inline plan
  POST   /v1/sessions:from-path             — start session from a plan.yaml path
  GET    /v1/sessions                       — list sessions (DB)
  GET    /v1/sessions/{id}                  — session detail (status + goals)
  GET    /v1/sessions/{id}/iterations/{gid} — per-goal iteration list
  POST   /v1/sessions/{id}:stop             — soft stop request
  GET    /v1/sessions/{id}/events           — SSE live event stream
  GET    /v1/sessions/{id}/report           — Markdown report (text/markdown)
  GET    /v1/sessions/{id}/export.tar.gz    — tarball of session artifacts

Authentication: optional bearer token via env LOOPCODER_API_KEY. If unset,
the server runs unauthenticated (loopback only by default).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import time
from typing import Any

import httpx  # noqa: F401  (used by httpx-related types via fastapi sometimes)
from fastapi import Depends, FastAPI, HTTPException, Header, Path
from fastapi.responses import PlainTextResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from loopcoder import __version__
from loopcoder.api import models as M
from loopcoder.api.runner import (
    SessionRunnerRegistry,
    load_plan_dict,
    load_plan_from_path,
    start_session,
)
from loopcoder.config import LoopCoderConfig, load_loopcoder_config
from loopcoder.logsetup import get_logger
from loopcoder.state.store import SessionStore
from loopcoder.tools.base import ToolContext
from loopcoder.tools.registry import default_registry
from loopcoder.ui.report import generate_report

log = get_logger("loopcoder.api")


def _check_auth(authorization: str | None) -> None:
    expected = os.environ.get("LOOPCODER_API_KEY", "").strip()
    if not expected:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(401, "bad bearer token")


def _auth(authorization: str | None = Header(default=None)) -> None:
    _check_auth(authorization)


def build_app(config_path: str | None = None) -> FastAPI:
    cfg: LoopCoderConfig = load_loopcoder_config(config_path)
    runners = SessionRunnerRegistry()
    store = SessionStore(cfg.storage.state_db)
    registry = default_registry()

    app = FastAPI(
        title="LoopCoder API",
        version=__version__,
        description="HTTP API for the LoopCoder iterative coding agent.",
    )

    # ---------- health ----------

    @app.get("/v1/health", response_model=M.HealthResponse)
    def health() -> M.HealthResponse:
        return M.HealthResponse(version=__version__, sessions_active=len(runners.list()))

    # ---------- tools ----------

    @app.get("/v1/tools", response_model=list[M.ToolMeta], dependencies=[Depends(_auth)])
    def list_tools() -> list[M.ToolMeta]:
        out: list[M.ToolMeta] = []
        for t in registry:
            out.append(
                M.ToolMeta(
                    name=t.name,
                    description=t.description,
                    parameters=t.ParamsModel.model_json_schema(),
                )
            )
        return out

    @app.post("/v1/tools/{name}", response_model=M.ToolCallResponse, dependencies=[Depends(_auth)])
    def call_tool(name: str, body: M.ToolCallRequest) -> M.ToolCallResponse:
        if name != body.name:
            raise HTTPException(400, "name mismatch between URL and body")
        if name not in registry:
            raise HTTPException(404, f"unknown tool: {name}")
        # Build a minimal ToolContext for ad-hoc calls
        ctx = ToolContext(
            workspace_root=body.workspace,
            forbidden_paths=list(cfg.tools.fs.forbidden_paths),
            allowed_shell_patterns=list(cfg.tools.shell.allowed_patterns),
            extra={
                "fs_max_read_bytes": cfg.tools.fs.max_read_bytes,
                "shell_output_max_kb": cfg.tools.shell.output_max_kb,
            },
        )
        result = registry.call(name, body.arguments, ctx)
        return M.ToolCallResponse(
            ok=result.ok,
            output=result.output,
            truncated=result.truncated,
            duration_ms=result.duration_ms,
            data=result.data,
        )

    # ---------- sessions ----------

    def _row_to_ref(s: dict[str, Any]) -> M.SessionRef:
        return M.SessionRef(
            id=s["id"],
            status=s.get("status") or "unknown",
            plan_path=s.get("plan_path"),
            started_at=s.get("started_at"),
            ended_at=s.get("ended_at"),
            total_prompt_tokens=s.get("total_prompt_tokens", 0) or 0,
            total_completion_tokens=s.get("total_completion_tokens", 0) or 0,
        )

    @app.post("/v1/sessions", response_model=M.SessionRef, dependencies=[Depends(_auth)])
    def post_session(body: M.SessionStartRequest) -> M.SessionRef:
        plan = load_plan_dict(body.plan)
        eff_cfg = (
            LoopCoderConfig.model_validate(
                {**cfg.model_dump(), **(body.config_overrides or {})}
            )
            if body.config_overrides
            else cfg
        )
        runner = start_session(plan, eff_cfg, plan_path=None, only_goal=body.only_goal,
                               registry_runners=runners)
        s = store.session_status(runner.session_id) or {"id": runner.session_id, "status": "running"}
        return _row_to_ref({**s, "id": runner.session_id})

    @app.post("/v1/sessions:from-path", response_model=M.SessionRef, dependencies=[Depends(_auth)])
    def post_session_from_path(body: M.SessionStartFromPathRequest) -> M.SessionRef:
        plan = load_plan_from_path(body.plan_path)
        runner = start_session(plan, cfg, plan_path=body.plan_path, only_goal=body.only_goal,
                               registry_runners=runners)
        s = store.session_status(runner.session_id) or {"id": runner.session_id, "status": "running"}
        return _row_to_ref({**s, "id": runner.session_id})

    @app.get("/v1/sessions", response_model=list[M.SessionRef], dependencies=[Depends(_auth)])
    def list_sessions() -> list[M.SessionRef]:
        return [_row_to_ref(s) for s in store.list_sessions()]

    @app.get("/v1/sessions/{sid}", dependencies=[Depends(_auth)])
    def get_session(sid: str = Path(...)) -> dict[str, Any]:
        s = store.session_status(sid)
        if s is None:
            raise HTTPException(404, "no such session")
        goals = store.goals_for(sid)
        return {"session": s, "goals": goals}

    @app.get("/v1/sessions/{sid}/iterations/{gid}",
             response_model=list[M.IterationView],
             dependencies=[Depends(_auth)])
    def list_iterations(sid: str, gid: str) -> list[M.IterationView]:
        rows = store.iterations_for(sid, gid)
        return [
            M.IterationView(
                iter=r["iter"],
                prompt_tokens=r.get("prompt_tokens") or 0,
                completion_tokens=r.get("completion_tokens") or 0,
                verify_passed=bool(r["verify_passed"]) if r.get("verify_passed") is not None else None,
                verify_log=r.get("verify_log"),
                started_at=r.get("started_at"),
                ended_at=r.get("ended_at"),
            )
            for r in rows
        ]

    @app.post("/v1/sessions/{sid}:stop", dependencies=[Depends(_auth)])
    def stop_session(sid: str, body: M.StopRequest) -> dict[str, Any]:
        runner = runners.get(sid)
        if runner is None:
            raise HTTPException(404, "session not active in this server")
        runner.controller.request_stop()
        return {"requested": True, "session_id": sid, "reason": body.reason}

    @app.get("/v1/sessions/{sid}/events", dependencies=[Depends(_auth)])
    async def session_events(sid: str):
        runner = runners.get(sid)
        if runner is None:
            raise HTTPException(404, "session not active in this server (events only stream live runs)")
        bus = runner.controller.events
        queue = await bus.subscribe(replay_history=True)

        async def gen():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield {
                            "event": event.type,
                            "data": json.dumps(event.to_dict(), default=str),
                        }
                        if event.type == "session.ended":
                            break
                    except asyncio.TimeoutError:
                        # heartbeat so proxies don't kill the stream
                        yield {"event": "heartbeat", "data": json.dumps({"ts": time.time()})}
            finally:
                await bus.unsubscribe(queue)

        return EventSourceResponse(gen())

    @app.get("/v1/sessions/{sid}/report", response_class=PlainTextResponse,
             dependencies=[Depends(_auth)])
    def session_report(sid: str) -> str:
        if store.session_status(sid) is None:
            raise HTTPException(404, "no such session")
        return generate_report(store, sid)

    @app.get("/v1/sessions/{sid}/export.tar.gz", dependencies=[Depends(_auth)])
    def session_export(sid: str):
        if store.session_status(sid) is None:
            raise HTTPException(404, "no such session")
        report_md = generate_report(store, sid).encode()
        summary = json.dumps(
            {"session": store.session_status(sid), "goals": store.goals_for(sid)},
            default=str, indent=2,
        ).encode()
        iterations: list[Any] = []
        for g in store.goals_for(sid):
            iterations.extend(store.iterations_for(sid, g["goal_id"]))
        iters_json = json.dumps(iterations, default=str, indent=2).encode()

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in [
                (f"{sid}/report.md", report_md),
                (f"{sid}/summary.json", summary),
                (f"{sid}/iterations.json", iters_json),
            ]:
                ti = tarfile.TarInfo(name=name)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        buf.seek(0)
        return StreamingResponse(
            iter([buf.read()]),
            media_type="application/gzip",
            headers={"Content-Disposition": f"attachment; filename={sid}.tar.gz"},
        )

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, config_path: str | None = None) -> None:
    """Blocking helper: build app + run uvicorn."""
    import uvicorn  # local import keeps top-level light

    app = build_app(config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
