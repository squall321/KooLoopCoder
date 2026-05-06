# LoopCoder VS Code Extension

VS Code integration for the LoopCoder iterative coding agent. Talks to the
LoopCoder HTTP API (FastAPI) — no special daemon required.

## Features

- **Activity bar** with two views:
  - **Sessions** — live tree of sessions → goals → iterations, with status
    icons and tokens used. Auto-refreshes every 3 s.
  - **Tools** — read-only catalog of every tool the agent exposes (the same
    set MCP clients see).
- **Commands** (Cmd/Ctrl-Shift-P, prefixed `LoopCoder:`):
  - `Run Plan from Active Editor` — start a session for the open `.yaml`
    file.
  - `Run Plan…` — file picker.
  - `Stop Active Session` — soft-stop a running session.
  - `Open Session Report` — load the markdown report into a new editor.
  - `Export Session…` — opens the tarball download URL.
  - `Show API Health` — quick status notification.
  - `Refresh Sessions`.
- **Output channel** ("LoopCoder") shows live SSE events
  (iter started/ended, tool calls, verify pass/fail).
- **Status bar** indicator (green when API is reachable; warning when not).

## Settings

| Key | Default | Purpose |
|---|---|---|
| `loopcoder.apiUrl` | `http://127.0.0.1:8765` | Base URL of `loopcoder serve`. |
| `loopcoder.apiKey` | (empty) | Bearer token; matches `LOOPCODER_API_KEY` on the server. |
| `loopcoder.autoOpenLogPanel` | `true` | Opens Output channel automatically when a run starts. |

## Setup

```bash
# 1) On the box where vLLM + LoopCoder run:
loopcoder serve --host 0.0.0.0 --port 8765

# 2) (optional) Set a token if exposing on the network:
LOOPCODER_API_KEY=secret loopcoder serve --host 0.0.0.0 --port 8765

# 3) In VS Code, set the apiUrl + apiKey under Settings → LoopCoder.
```

If LoopCoder is on a remote host, point `apiUrl` at it directly or use an SSH
local-forward (`ssh -L 8765:localhost:8765 b300`).

## Building locally

```bash
cd vscode-extension
npm install
npm run compile
npm run package        # produces dist/loopcoder-vscode-0.1.0.vsix
code --install-extension dist/loopcoder-vscode-0.1.0.vsix
```

(For real publishing to the marketplace you'll need `vsce login` and a
publisher account — out of scope for this repo.)

## How it talks to LoopCoder

Just HTTP + Server-Sent Events:

| VS Code action | API endpoint |
|---|---|
| Tree view: list sessions | `GET /v1/sessions` |
| Expand a session | `GET /v1/sessions/{id}` |
| Expand a goal | `GET /v1/sessions/{id}/iterations/{gid}` |
| Run plan | `POST /v1/sessions:from-path` |
| Stop | `POST /v1/sessions/{id}:stop` |
| Live log stream | `GET /v1/sessions/{id}/events` (SSE) |
| Report | `GET /v1/sessions/{id}/report` |
| Export tarball | `GET /v1/sessions/{id}/export.tar.gz` |
| Tool catalog | `GET /v1/tools` |
