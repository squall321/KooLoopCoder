"""LoopCoder CLI entrypoint."""

from __future__ import annotations

import json
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import click

from loopcoder import __version__
from loopcoder.config import (
    load_install_config,
    load_loopcoder_config,
    load_vllm_config,
    merged_view,
)
from loopcoder.logsetup import configure_logging, get_logger


log = get_logger("loopcoder.cli")


@click.group(invoke_without_command=False)
@click.version_option(version=__version__, prog_name="loopcoder")
@click.option("--log-level", default="INFO", show_default=True,
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False))
@click.option("--log-dir", default=None, type=click.Path(),
              help="If set, also write JSON log lines under this directory.")
@click.pass_context
def main(ctx: click.Context, log_level: str, log_dir: str | None) -> None:
    """LoopCoder — iterative coding agent."""
    configure_logging(level=log_level, log_dir=log_dir)
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level


@main.command()
@click.option("--plan", "plan_path", type=click.Path(exists=True), required=True)
@click.option("--goal", "goal_id", default=None, help="Run only this goal id")
@click.option("--resume", is_flag=True, default=False, help="Resume the most recent session")
@click.option("--config", "config_path", default=None, help="Override loopcoder.yaml path")
@click.option("--dry-run", is_flag=True, default=False,
              help="Validate plan + run acceptance checks against the current workspace; no LLM calls.")
def run(plan_path: str, goal_id: str | None, resume: bool, config_path: str | None, dry_run: bool) -> None:
    """Run an agent session against a plan.yaml."""
    from loopcoder.plan.parser import load_plan
    from loopcoder.plan.topo import topological_order
    from loopcoder.state.store import SessionStore
    from loopcoder.loop.verifier import Verifier

    cfg = load_loopcoder_config(config_path)
    plan = load_plan(plan_path)
    log.info("plan loaded", extra={"goals": len(plan.goals), "project": plan.project.name})

    if dry_run:
        click.echo("# Dry-run: validating plan + running acceptance checks (no LLM)")
        verifier = Verifier(plan.project.workspace, sandbox=None)
        ordered = topological_order(plan)
        if goal_id:
            ordered = [g for g in ordered if g.id == goal_id]
        any_fail = False
        for g in ordered:
            click.echo(f"\n## {g.id} — {g.title}")
            r = verifier.run(g.acceptance)
            click.echo(r.short_summary())
            click.echo(r.log)
            if not r.passed:
                any_fail = True
        sys.exit(0 if not any_fail else 2)

    from loopcoder.llm.client import LlmClient
    from loopcoder.sandbox import make_sandbox
    from loopcoder.loop.controller import LoopController

    # plan.llm.model may be a multi-model key (routes to that vllm@<key>
    # instance) or a literal served name; resolve_endpoint handles both.
    _base_url, _model, _api_key = cfg.llm.resolve_endpoint(plan.llm.model)
    client = LlmClient(
        base_url=_base_url,
        api_key=_api_key,
        model=_model,
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

    controller = LoopController(plan, cfg, client, sandbox, store)
    outcomes = controller.run(only_goal=goal_id, plan_path=plan_path)
    for o in outcomes:
        click.echo(f"goal {o.goal_id}: {o.status} ({o.iterations} iter)")
    failed = [o for o in outcomes if o.status != "passed"]
    sys.exit(0 if not failed else 2)


@main.command(name="list")
def list_sessions() -> None:
    """List sessions."""
    from loopcoder.state.store import SessionStore

    cfg = load_loopcoder_config()
    store = SessionStore(cfg.storage.state_db)
    sessions = store.list_sessions()
    if not sessions:
        click.echo("(no sessions)")
        return
    for s in sessions:
        click.echo(f"{s['id']}  {s['status']:>10}  {s['plan_path']}")


@main.command()
@click.argument("session_id", required=False)
def status(session_id: str | None) -> None:
    """Show status of a session (or the most recent)."""
    from loopcoder.state.store import SessionStore

    cfg = load_loopcoder_config()
    store = SessionStore(cfg.storage.state_db)
    if session_id is None:
        sessions = store.list_sessions()
        if not sessions:
            click.echo("no sessions")
            return
        session_id = sessions[0]["id"]
    s = store.session_status(session_id)
    if s is None:
        click.echo(f"session not found: {session_id}", err=True)
        sys.exit(1)
    click.echo(json.dumps(s, indent=2, default=str))
    click.echo("--- goals ---")
    for g in store.goals_for(session_id):
        click.echo(json.dumps(g, default=str))


@main.command()
@click.argument("session_id")
@click.option("--out", "out_path", default=None, help="Write to file instead of stdout")
def report(session_id: str, out_path: str | None) -> None:
    """Generate a Markdown report for a session."""
    from loopcoder.state.store import SessionStore
    from loopcoder.ui.report import generate_report

    cfg = load_loopcoder_config()
    store = SessionStore(cfg.storage.state_db)
    md = generate_report(store, session_id)
    if out_path:
        Path(out_path).write_text(md)
        click.echo(f"wrote {out_path}")
    else:
        click.echo(md)


@main.command()
@click.argument("session_id")
def tokens(session_id: str) -> None:
    """Token usage stats for a session."""
    from loopcoder.state.store import SessionStore

    cfg = load_loopcoder_config()
    store = SessionStore(cfg.storage.state_db)
    s = store.session_status(session_id)
    if s is None:
        click.echo(f"session not found: {session_id}", err=True)
        sys.exit(1)
    click.echo(
        f"session {session_id}: prompt={s.get('total_prompt_tokens', 0)} "
        f"completion={s.get('total_completion_tokens', 0)}"
    )


@main.command()
@click.option("--host", default=None,
              help="Bind host. Default: $LOOPCODER_API_HOST or 127.0.0.1.")
@click.option("--port", default=None, type=int,
              help="Bind port. Default: $LOOPCODER_API_PORT or 8765.")
@click.option("--config", "config_path", default=None,
              help="Override loopcoder.yaml path (else env LOOPCODER_YAML).")
def serve(host: str | None, port: int | None, config_path: str | None) -> None:
    """Start the HTTP API server (FastAPI/uvicorn).

    Precedence: explicit flag > env (LOOPCODER_API_HOST/PORT) > default
    (127.0.0.1:8765). The suite SIF passes the env, so systemd controls
    the bind via loopcoder.env without rebuilding.
    """
    import os
    eff_host = host or os.environ.get("LOOPCODER_API_HOST") or "127.0.0.1"
    eff_port = port or int(os.environ.get("LOOPCODER_API_PORT") or 8765)
    from loopcoder.api import run_server
    run_server(host=eff_host, port=eff_port, config_path=config_path)


@main.command(name="select-model")
@click.argument("profile")
@click.option("--catalog", "catalog_path", default=None, help="Override catalog YAML.")
@click.option("--list", "list_all", is_flag=True, default=False, help="List every fitting model.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Machine-readable output.")
def select_model(profile: str, catalog_path: str | None, list_all: bool, as_json: bool) -> None:
    """Recommend a model for a hardware profile (e.g. b300x8)."""
    import sys
    from loopcoder.catalog import recommend_cli
    # delegate to the standalone recommender (it parses argv)
    argv = [profile]
    if catalog_path:
        argv += ["--catalog", catalog_path]
    if list_all:
        argv += ["--list"]
    if as_json:
        argv += ["--json"]
    sys.argv = ["loopcoder-select-model"] + argv
    rc = recommend_cli()
    sys.exit(rc)


@main.command(name="catalog-resolve")
@click.argument("model_id")
@click.option("--catalog", "catalog_path", default=None, help="Override catalog YAML.")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON instead of KEY=VALUE.")
def catalog_resolve(model_id: str, catalog_path: str | None, as_json: bool) -> None:
    """Resolve a model id to vLLM serving flags (used by setup.sh)."""
    import sys
    from loopcoder.catalog import resolve_cli
    argv = [model_id]
    if catalog_path:
        argv += ["--catalog", catalog_path]
    if as_json:
        argv += ["--json"]
    sys.argv = ["loopcoder-catalog-resolve"] + argv
    sys.exit(resolve_cli())


@main.command(name="mcp")
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio", show_default=True)
@click.option("--host", default=None,
              help="SSE bind host. Default: $LOOPCODER_MCP_HOST or 127.0.0.1.")
@click.option("--port", default=None, type=int,
              help="SSE bind port. Default: $LOOPCODER_MCP_PORT or 8766.")
def mcp_serve(transport: str, host: str | None, port: int | None) -> None:
    """Run LoopCoder as an MCP server (stdio for Claude Desktop, sse for HTTP)."""
    import os
    eff_host = host or os.environ.get("LOOPCODER_MCP_HOST") or "127.0.0.1"
    eff_port = port or int(os.environ.get("LOOPCODER_MCP_PORT") or 8766)
    from loopcoder.mcp import run_mcp_server
    run_mcp_server(transport=transport, host=eff_host, port=eff_port)


@main.command()
@click.argument("session_id")
@click.option("--out", "out_path", required=True, help="Output .tar.gz file.")
def export(session_id: str, out_path: str) -> None:
    """Export a session (DB rows + report) as a tar.gz for sharing/debug."""
    from loopcoder.state.store import SessionStore
    from loopcoder.ui.report import generate_report

    cfg = load_loopcoder_config()
    store = SessionStore(cfg.storage.state_db)
    if store.session_status(session_id) is None:
        click.echo(f"session not found: {session_id}", err=True)
        sys.exit(1)

    report_md = generate_report(store, session_id).encode()
    summary = json.dumps(
        {
            "session": store.session_status(session_id),
            "goals": store.goals_for(session_id),
        },
        default=str,
        indent=2,
    ).encode()

    iterations = []
    for g in store.goals_for(session_id):
        iterations.extend(store.iterations_for(session_id, g["goal_id"]))
    iters_json = json.dumps(iterations, default=str, indent=2).encode()

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_p, "w:gz") as tar:
        for name, data in [
            (f"{session_id}/report.md", report_md),
            (f"{session_id}/summary.json", summary),
            (f"{session_id}/iterations.json", iters_json),
        ]:
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            tar.addfile(ti, BytesIO(data))
    click.echo(f"wrote {out_p} ({out_p.stat().st_size} bytes)")


@main.group()
def config() -> None:
    """Configuration utilities."""


@config.command("validate")
@click.option("--install", "install_file", default=None)
@click.option("--vllm", "vllm_file", default=None)
@click.option("--loopcoder", "loopcoder_file", default=None)
def config_validate(install_file: str | None, vllm_file: str | None, loopcoder_file: str | None) -> None:
    """Validate the three YAML configs."""
    errors: list[str] = []
    inst = vll = lc = None
    try:
        inst = load_install_config(install_file)
    except FileNotFoundError as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(f"install.yaml: {e}")
    try:
        vll = load_vllm_config(vllm_file)
    except Exception as e:
        errors.append(f"vllm.yaml: {e}")
    try:
        lc = load_loopcoder_config(loopcoder_file)
    except Exception as e:
        errors.append(f"loopcoder.yaml: {e}")
    if errors:
        for e in errors:
            click.echo(f"FAIL  {e}", err=True)
        sys.exit(1)
    click.echo("OK")
    _ = (inst, vll, lc)


@config.command("show")
def config_show() -> None:
    """Print the merged configuration as JSON."""
    inst = vll = lc = None
    try:
        inst = load_install_config()
    except FileNotFoundError:
        pass
    try:
        vll = load_vllm_config()
    except Exception:
        pass
    try:
        lc = load_loopcoder_config()
    except Exception:
        pass
    click.echo(json.dumps(merged_view(inst, vll, lc), indent=2, default=str))


if __name__ == "__main__":
    main()
