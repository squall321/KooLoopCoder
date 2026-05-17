# LoopCoder

[![tests](https://github.com/squall321/KooLoopCoder/actions/workflows/test.yml/badge.svg)](https://github.com/squall321/KooLoopCoder/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Self-hosted iterative coding agent.** Drives a local vLLM (default:
Qwen3-Coder-480B-FP8) through a PDCA loop until every goal's
acceptance check passes. Verification runs OUTSIDE the LLM so the
model cannot fake completion. Runs offline on air-gapped GPU servers.

The whole runtime is packaged as Apptainer SIFs (`vllm.sif`,
`loopcoder-suite.sif`, `loopcoder-sandbox.sif`). The host needs only
the NVIDIA driver and Apptainer.

---

## What's in the box

```
GPU host
 ├─ /opt/apptainers/                          ← versioned SIFs (atomic upgrades)
 │   └─ current/{vllm,loopcoder-suite,loopcoder-sandbox}.sif
 │
 ├─ /scratch/models/<model_id>/                ← weights, persistent (never inside SIF)
 ├─ /scratch/workspaces/                       ← per-project workspaces
 │
 ├─ systemd: vllm.service                     :8000 (loopback)
 ├─ systemd: loopcoder.service                :8765 (HTTP API), :8766 (MCP-SSE)
 │
 └─ sshd                                      :22  (only externally exposed port)
```

User connects from any machine via **VS Code Remote-SSH** + the
LoopCoder VS Code extension. No browser GUI on the server, no TLS
plumbing, no passwords beyond SSH keys.

---

## Quickstart (host already has driver + apptainer)

```bash
# On the GPU server (one-time, as root):
sudo bash setup.sh

# Verify
sudo systemctl status vllm
sudo systemctl status loopcoder
curl -sf http://127.0.0.1:8765/v1/health      # → {"status":"ok",...}

# From your laptop:
ssh -L 8765:127.0.0.1:8765 b300                # tunnel API
# In a separate VS Code window: F1 → Remote-SSH: Connect to Host → b300
# Open /scratch/workspaces/<your-project>
# Author plan.yaml → command: "LoopCoder: Run Plan from Active Editor"
```

Detailed onboarding: see [`HANDOFF.md`](HANDOFF.md), planning at
[`PLAN.md`](PLAN.md), live status at [`PROGRESS.md`](PROGRESS.md).

---

## Repository layout

| Path | Purpose |
|---|---|
| `agent/loopcoder/` | Python package: agent core, HTTP API, MCP server, CLI |
| `agent/tests/` | 139 unit + mock-E2E tests (run with `pytest agent/tests/`) |
| `containers/*.def` | Apptainer recipes for vllm / suite / sandbox SIFs |
| `scripts/build-sif-bundle.sh` ★ | **SIF-only bundle builder — no VM, no WSL2.** Builds the 3 SIFs + source + cwRsync directly on a Linux host with apptainer |
| `bundle.sh` + `bundle/` | Legacy bundle builder using a 24.04 KVM VM (ships apt/.deb + wheels) |
| `setup.sh` | Offline staged installer for the GPU host (SIF-only or legacy bundle) |
| `test_setup.sh` + `bundle/test_vm/` | No-internet, no-GPU Test VM that re-runs setup.sh and asserts post-conditions |
| `scripts/pack-model.sh` | Pack an unpacked HF model dir into a single read-only `model.sif` |
| `scripts/fetch-models.sh` | One-touch fetch of every model in a deploy.yaml `models[]` + catalog resolve |
| `systemd/vllm@.service.template` | Instanced unit: one `vllm@<key>` per model (multi-model serving) |
| `scripts/hpc/` | HPC mode: Slurm `sbatch` job wrappers, no sudo/systemd, runs from `$LOOPCODER_HOME` |
| `scripts/upgrade-suite.sh` | Atomic SIF upgrade: `cp` + `ln -sfn` + `systemctl restart` |
| `scripts/windows/` | PowerShell deploy (SIF-only, no WSL2) + HF model download |
| `vscode-extension/` | TypeScript VS Code / Cursor / Windsurf extension |
| `config/*.yaml.example` | Pydantic-validated config templates |
| `examples/` | Demo plans: `plan_simple`, `plan_refactor`, `plan_fastapi_hello`, `tiny-end-to-end.sh` |
| `docs/manuals/` | `windows-mediated-deploy.md` (SIF-only), `remote-ssh-workflow.md`, `model-download-windows.md` |

---

## Why this exists

1. **Goal-driven loop with external verification.** The agent cannot lie
   about completion. Acceptance commands run in the host (or
   sandbox), and only their real-world result counts.
2. **Context preservation.** Verify logs and pinned files are NEVER
   truncated, even under tight token budgets.
3. **Offline-first.** Designed for air-gapped GPU servers. All
   dependencies (apt, Python wheels, Apptainer SIFs) are bundled on a
   build host and shipped as one tree.
4. **Apptainer-native.** Every component is a self-contained SIF.
   Upgrade = swap a file. No Docker on the GPU host.

---

## Status

- 50 Python files / ~5,500 lines, **139 / 139 unit + mock-E2E tests
  pass**.
- HTTP API verified live (16 routes), MCP server (stdio + SSE)
  verified, VS Code extension TypeScript compiles + .vsix builds (22
  KB).
- Demo plans validate; tiny model
  (`Qwen2.5-Coder-0.5B-Instruct`, ~954 MB) downloaded & smoke-validated
  on dev hardware.
- Full Bundle/Test VM lifecycle and B300 deployment are scripted but
  end-to-end run on real hardware is left to the operator.

See [`CHANGELOG.md`](CHANGELOG.md) for the 0.1.0 release notes.

## License

[MIT](LICENSE)
