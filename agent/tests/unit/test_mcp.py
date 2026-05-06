"""Tests for the MCP server adapter."""

import asyncio
from pathlib import Path

import pytest

from loopcoder.mcp.server import build_mcp_server


def _setup_dev_config(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "loopcoder.yaml"
    cfg.write_text(f"""
storage:
  state_db: {tmp_path}/state.db
  log_dir: {tmp_path}/logs
  workspaces_root: {tmp_path}/ws
sandbox:
  backend: host
""")
    monkeypatch.setenv("LOOPCODER_YAML", str(cfg))
    monkeypatch.setenv("LOOPCODER_MCP_WORKSPACE", str(tmp_path))


def test_build_mcp_server(tmp_path, monkeypatch):
    _setup_dev_config(tmp_path, monkeypatch)
    server = build_mcp_server()
    assert server.name == "loopcoder"


def test_mcp_list_tools(tmp_path, monkeypatch):
    _setup_dev_config(tmp_path, monkeypatch)
    server = build_mcp_server()
    # The decorator-stored handler is held in server's request_handlers
    handlers = server.request_handlers
    # Find ListToolsRequest handler
    from mcp.types import ListToolsRequest
    handler = handlers.get(ListToolsRequest)
    assert handler is not None
    # Build a minimal ListToolsRequest. SDK uses pydantic models.
    req = ListToolsRequest(method="tools/list", params=None)
    result = asyncio.run(handler(req))
    # ServerResult union — extract tools from the inner ListToolsResult
    inner = result.root
    assert hasattr(inner, "tools")
    names = [t.name for t in inner.tools]
    for required in ["read_file", "write_file", "edit_file", "run_shell", "todo_write"]:
        assert required in names


def test_mcp_call_tool_read_file(tmp_path, monkeypatch):
    (tmp_path / "demo.txt").write_text("MCP works\n")
    _setup_dev_config(tmp_path, monkeypatch)
    server = build_mcp_server()
    handlers = server.request_handlers
    from mcp.types import CallToolRequest, CallToolRequestParams
    handler = handlers.get(CallToolRequest)
    assert handler is not None
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="read_file", arguments={"path": "demo.txt"}),
    )
    result = asyncio.run(handler(req))
    inner = result.root
    assert hasattr(inner, "content")
    text = inner.content[0].text
    assert "MCP works" in text


def test_mcp_call_unknown_tool(tmp_path, monkeypatch):
    _setup_dev_config(tmp_path, monkeypatch)
    server = build_mcp_server()
    from mcp.types import CallToolRequest, CallToolRequestParams
    handler = server.request_handlers.get(CallToolRequest)
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="this_does_not_exist", arguments={}),
    )
    result = asyncio.run(handler(req))
    text = result.root.content[0].text
    assert "unknown tool" in text
