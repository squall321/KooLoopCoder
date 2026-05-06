"""LoopCoder ↔ MCP bridge.

Wraps every entry in our ToolRegistry as an MCP tool. The MCP client (e.g.
Claude Desktop) sees identical names + JSON Schemas; the call is dispatched
back through our registry with a synthesized ToolContext (forbidden_paths,
shell allowlist, etc., are loaded from loopcoder.yaml).

For now, MCP calls run with `workspace_root = $LOOPCODER_MCP_WORKSPACE`
(env), or the current working directory if unset. This is intentionally
simple — the use case is "another LLM wants to use LoopCoder's tools",
not full session orchestration.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from loopcoder.config import load_loopcoder_config
from loopcoder.logsetup import get_logger
from loopcoder.tools.base import ToolContext
from loopcoder.tools.registry import default_registry

log = get_logger("loopcoder.mcp")


def build_mcp_server() -> Server:
    cfg = load_loopcoder_config()
    registry = default_registry()

    def _ctx() -> ToolContext:
        ws = os.environ.get("LOOPCODER_MCP_WORKSPACE", os.getcwd())
        return ToolContext(
            workspace_root=ws,
            forbidden_paths=list(cfg.tools.fs.forbidden_paths),
            allowed_shell_patterns=list(cfg.tools.shell.allowed_patterns),
            extra={
                "fs_max_read_bytes": cfg.tools.fs.max_read_bytes,
                "shell_output_max_kb": cfg.tools.shell.output_max_kb,
            },
        )

    server: Server = Server("loopcoder")

    @server.list_tools()  # type: ignore[no-untyped-call]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.ParamsModel.model_json_schema(),
            )
            for t in registry
        ]

    @server.call_tool()  # type: ignore[no-untyped-call]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        log.info("mcp tool call", extra={"tool": name})
        if name not in registry:
            return [TextContent(type="text", text=f"unknown tool: {name}")]
        result = registry.call(name, arguments or {}, _ctx())
        body = result.output
        if result.data is not None and isinstance(result.data, (dict, list)):
            body += "\n\n[data]\n" + json.dumps(result.data, default=str, indent=2)
        # MCP convention: prefix non-ok results so the model can detect failure
        prefix = "" if result.ok else "TOOL_ERROR: "
        return [TextContent(type="text", text=prefix + body)]

    return server


def run_mcp_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8766) -> None:
    """Block running the MCP server on the chosen transport."""

    server = build_mcp_server()

    async def _run_stdio() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    if transport == "stdio":
        asyncio.run(_run_stdio())
        return

    if transport == "sse":
        # mcp.server.sse provides a Starlette app; we run it via uvicorn.
        try:
            from mcp.server.sse import SseServerTransport
        except ImportError as e:
            raise RuntimeError("mcp[sse] transport not available in this version") from e

        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        import uvicorn

        sse = SseServerTransport("/messages/")

        async def handle_sse(scope, receive, send):  # type: ignore[no-untyped-def]
            async with sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )
        uvicorn.run(app, host=host, port=port, log_level="info")
        return

    raise ValueError(f"unknown transport: {transport}")
