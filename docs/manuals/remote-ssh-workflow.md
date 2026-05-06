# LoopCoder via VS Code Remote-SSH

> **You**: any machine with VS Code (or Cursor / Windsurf / VSCodium) installed.
> **Server**: GPU host (B300 etc.) where LoopCoder + vLLM run inside Apptainer.
> **Network**: only port 22 (SSH) needs to be reachable on the server.

LoopCoder bundles **no GUI on the server**. The user's IDE connects via
SSH; everything else (HTTP API at `:8765`, MCP at `:8766`, vLLM at
`:8000`) is loopback-only and reached through the SSH tunnel.

---

## 1. Server-side prerequisites

The GPU host must have:

```
NVIDIA Driver (≥ 580 for Blackwell)
Apptainer       (≥ 1.3)
sshd            (port 22)
```

Plus the LoopCoder install artifacts (created by `setup.sh`):

```
/opt/apptainers/                            # SIF store
└── current/{vllm,loopcoder-suite,loopcoder-sandbox}.sif
/etc/loopcoder/{install,vllm,loopcoder}.yaml
/etc/systemd/system/{vllm,loopcoder}.service
/scratch/models/<model_id>/                 # weights (separate, never in SIF)
/scratch/workspaces/                        # per-user / per-project workdirs
useradd -r loopcoder                        # systemd unit user
```

Sanity:

```bash
systemctl status vllm        # should be active
systemctl status loopcoder   # should be active
curl -sf http://127.0.0.1:8765/v1/health
```

---

## 2. Client-side: one-time setup

### 2.1 SSH config

`~/.ssh/config` on your laptop:

```
Host b300
    HostName b300.example.org
    User you
    IdentityFile ~/.ssh/id_ed25519
    # Forward LoopCoder API + MCP locally so the VS Code extension reaches them
    LocalForward 8765 127.0.0.1:8765
    LocalForward 8766 127.0.0.1:8766
```

Test:

```bash
ssh b300 'curl -sf http://127.0.0.1:8765/v1/health'
```

### 2.2 VS Code extensions

In your local VS Code:

```
Code → Extensions
  • Microsoft → "Remote - SSH"
  • LoopCoder (this repo's vscode-extension/dist/*.vsix; or future Marketplace)
```

To install LoopCoder's extension:

```bash
# from the repo:
cd vscode-extension
npm install
npm run package          # produces dist/loopcoder-vscode-0.1.0.vsix
code --install-extension dist/loopcoder-vscode-0.1.0.vsix
```

### 2.3 Open a workspace remotely

```
F1 → "Remote-SSH: Connect to Host…" → b300
File → Open Folder…  → /scratch/workspaces/myproject
```

VS Code will prompt to install LoopCoder extension on the **remote** —
say yes. Once that's done the LoopCoder activity bar shows up on the
remote, and the API URL `http://127.0.0.1:8765` already points at the
server's loopcoder service (since you're running on the server side
from VS Code's perspective).

---

## 3. Daily workflow

```
┌─────────────────────────┐
│ Your laptop / dev box   │
│                         │
│ VS Code                 │
│  └── Remote-SSH ────────┼───── ssh b300 ─────┐
│                         │                    │
└─────────────────────────┘                    ▼
                                ┌──────────────────────────────┐
                                │ b300                          │
                                │   sshd :22                    │
                                │                               │
                                │   apptainer run               │
                                │     └─ vllm.sif :8000         │
                                │     └─ loopcoder-suite.sif    │
                                │            :8765 API          │
                                │            :8766 MCP-SSE      │
                                │                               │
                                │   /scratch/models/.../        │
                                │   /scratch/workspaces/myproj/ │
                                └──────────────────────────────┘
```

In the VS Code remote window:

1. **Open** `/scratch/workspaces/myproj/`
2. **Author** `plan.yaml`
3. **Right-click → "LoopCoder: Run Plan from Active Editor"**, or via
   command palette
4. **Output channel** "LoopCoder" streams SSE events live
5. **Sessions tree** in the activity bar shows iter status, tokens,
   verify pass/fail
6. **Open Session Report** → opens Markdown report in a new tab

No port forwarding to the public internet; no certificates; no separate
auth daemon. Just SSH.

---

## 4. Multi-user / shared server

If multiple developers use the same GPU server, they each get their own
SSH account, but they share **one** vllm + loopcoder service. To keep
sessions separate:

* Each user's projects live under `/scratch/workspaces/<user>/<project>/`
* The HTTP API serializes session list/start by SQLite at
  `/var/lib/loopcoder/sessions.db` — currently single-tenant. For
  proper multi-tenancy you'd want one `loopcoder.service` per user
  (template unit `loopcoder@<user>.service`) — out of scope for v0.1.

A pragmatic interim: the API supports a `LOOPCODER_API_KEY`. Run the
service with a token, share it only with trusted users, and rely on
SSH ACL for everything else.

---

## 5. Working from a different IDE

Anything that supports SSH and OpenAI-compatible API works:

| Client | Notes |
|---|---|
| **VS Code Remote-SSH** | Native, full feature set |
| **Cursor / Windsurf** (VS Code forks) | Same .vsix works (some policies differ) |
| **JetBrains Remote** | Use the SSH terminal + raw curl/`loopcoder` CLI |
| **Vim / Neovim over SSH** | Same; the `loopcoder` CLI is the surface |
| **Claude Desktop** | LoopCoder MCP server: add `~/.config/Claude/claude_desktop_config.json`:<br>`{ "mcpServers": { "loopcoder": { "command": "ssh", "args": ["b300", "loopcoder", "mcp", "--transport", "stdio"] } } }`<br>This pipes MCP stdio over SSH directly. |

---

## 6. Diagnostics

```bash
# from the laptop after Remote-SSH connect:
curl -sf http://127.0.0.1:8765/v1/health    # ok if loopcoder.service is up
curl -sf http://127.0.0.1:8765/v1/tools | jq '. | length'
curl -sf http://127.0.0.1:8000/v1/models   # vLLM (note: usually loopback only)
ssh b300 'sudo journalctl -u loopcoder -n 80'
ssh b300 'sudo journalctl -u vllm     -n 80'
```

Common issues:

| Symptom | Likely cause |
|---|---|
| `Connection refused` on :8765 | `loopcoder.service` not active. `systemctl restart loopcoder`. |
| `bind: address already in use` | Another LocalForward instance still alive; close other SSH session. |
| Extension says "API offline" but curl works | Extension's `loopcoder.apiUrl` or `apiKey` is wrong. Check VS Code Settings (remote scope, not local). |
| vLLM 503 | Driver mismatch or sm_120 unsupported by current vllm.sif. Check `journalctl -u vllm`. |

---

## 7. Why no code-server?

We considered shipping browser-based VS Code (`code-server`) inside
loopcoder-suite.sif. We didn't, because:

* **Remote-SSH gives the user full native VS Code** (debugger, Live
  Share, settings sync, etc.) — strictly better than `code-server` web
  for serious work.
* **One less moving part** — no extra TLS / passwords / port mapping
  to reason about; SSH already provides authn + transport security.
* **Smaller SIF** (~250 MB vs ~1 GB).
* If you ever want a browser client (kiosk machines etc.), wrap a
  `code-server` SIF separately and bind into the same workspace — both
  models can coexist.

---

## 8. Adding extensions per-project (optional)

You can pin VS Code extensions to be auto-installed on the remote when
the workspace opens. In `/scratch/workspaces/myproj/.vscode/extensions.json`:

```json
{
  "recommendations": [
    "ms-vscode-remote.remote-ssh",
    "koopark.loopcoder-vscode",
    "ms-python.python"
  ]
}
```

VS Code prompts the user once and installs them in the remote profile.
