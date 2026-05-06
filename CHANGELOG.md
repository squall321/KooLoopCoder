# Changelog

All notable changes to LoopCoder will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-05-06

Initial public release. Everything below was built in a single design+implementation push;
PLAN.md revisions 1 → 6 record the evolving scope.

### Added

#### Core agent (`agent/loopcoder/`)
- Python package: `config`, `plan`, `llm`, `tools`, `sandbox`, `state`, `loop`, `ui`,
  `api`, `mcp`, `events`, `logsetup` — 50 files / ~5,400 lines.
- 23 LLM tools registered, with 2 pre-hooks + 5 post-hooks enforcing
  read-before-write (CC3), auto git-add, output capture.
- 14 Claude-Code-derived patterns integrated (CC1..CC14): unique-match Edit,
  cat-n Read with offset/limit, dynamic system reminders, TodoWrite,
  pre/post tool hooks, Sub-agent (`spawn_agent`), Bash background jobs,
  convention auto-loader (`CLAUDE.md`/`AGENTS.md`/`.loopcoderrc`), etc.
- ContextBuilder with explicit "never truncate" policy for system prompt,
  current goal, pinned files, and verify logs.
- Verifier executes acceptance checks **outside** the LLM (5 kinds:
  `shell`, `file_exists`, `file_contains`, `file_not_contains`, `http`).
- Snapshot manager (git tags per goal) + SQLite session store + Markdown
  reporter + replay support.
- Strategy decision: consecutive-failure threshold for prompt nudges and
  hard rollback to the last good snapshot.

#### CLI
- `run / dry-run / list / status / report / tokens / export / config / serve / mcp`.
- `--log-level`, `--log-dir` (rotating JSON file handler).

#### HTTP API (FastAPI, 16 routes, `loopcoder serve`)
- `/v1/health`, `/v1/tools`, `/v1/sessions` (start / list / get / iters /
  stop / report / export), `/v1/sessions/{id}/events` (SSE live stream).
- Optional Bearer auth via `LOOPCODER_API_KEY`.

#### MCP server (`loopcoder mcp`)
- stdio transport (Claude Desktop compatible) + SSE (HTTP).
- All 23 tools mirrored automatically from the registry.

#### VS Code extension (`vscode-extension/`)
- TypeScript: api.ts (HTTP+SSE client), sessionsTreeView.ts,
  toolsTreeView.ts, extension.ts. Activity bar with sessions/tools
  views, 7 commands, status bar, live Output channel.
- Compiles cleanly with Node 20 + TypeScript 5.4.

#### Bundle / setup pipeline
- `bundle.sh` (host orchestrator), `bundle/vm/*` (Bundle VM lifecycle),
  `bundle/in_vm/*` (apt / wheels / vLLM .sif / sandbox .sif / model
  collectors with manifest.sha256), `bundle/test_vm/*` (Test VM with
  isolated libvirt network — no internet).
- `test_setup.sh` runs `setup.sh --skip-gpu-stages` end-to-end inside a
  no-internet, no-GPU 24.04 VM and asserts post-conditions.
- `setup.sh` (B300 offline installer): 14 stages, idempotent, resumable,
  systemd-managed vLLM via Apptainer.

#### Configuration
- 3 + 2 YAML examples: `install.yaml`, `vllm.yaml`, `loopcoder.yaml`,
  plus `install.yaml.tiny` and `vllm.yaml.tiny` for tiny-model dev.
- All knobs are Pydantic-validated; environment variables override
  defaults at call time.

#### Apptainer
- `containers/vllm.def` (from `vllm/vllm-openai:latest` Docker image),
  `containers/loopcoder-sandbox.def` (Python 3.12-slim + tools).

#### Helpers
- `scripts/healthcheck.sh`, `benchmark.sh`, `uninstall.sh`,
  `make_apptainer_images.sh`.
- `scripts/windows/`: `Download-Model.ps1` + `.bat` (3 modes: default,
  tiny, custom) + `Verify-Model.ps1` for downloading large models on
  Windows hosts.

#### Tests
- 123 unit tests across 19 files (config / plan / topo / tokens / fs /
  hooks / todo / shell+bg / conventions / reminders / context /
  state_store / sandbox / verifier / snapshot / controller integration
  / cli / api / mcp). All passing on CPython 3.12.

#### Examples
- `plan_simple.yaml` (2 goals, calculator),
  `plan_refactor.yaml` (4 goals, tokenizer/parser split),
  `plan_fastapi_hello.yaml` (3 goals, FastAPI + TestClient),
  `tiny-end-to-end.sh` (full smoke test on the dev host).

#### Docs
- `PLAN.md` (rev. 6, ~1200 lines — architecture, decisions, risks).
- `PROGRESS.md` (live status board, append-only worklog).
- `HANDOFF.md` (single-file onboarding for a fresh AI/engineer).
- `INSTALL.md` (end-user install flow).
- `README.md` (project overview).
- `docs/manuals/model-download-windows.md` (480 GB Windows playbook).
- `vscode-extension/README.md` (extension features + settings).
- `scripts/windows/README.md`.

### Verified

- 123/123 unit tests pass.
- 24 shell scripts pass `bash -n`.
- HTTP API live-tested (`/v1/health`, `/v1/tools` returns 23 tools).
- MCP server builds and answers `tools/list` + `tools/call`.
- VS Code extension TypeScript compiles with 0 errors.
- 3 YAML configs + 3 example plans round-trip through Pydantic.
- `Qwen/Qwen2.5-Coder-0.5B-Instruct` (tiny dev model) downloaded and
  validated.

### Pending live verification

- Bundle VM end-to-end (qcow2 + cloud-init + virtiofs + collect_*.sh).
- Test VM end-to-end (`test_setup.sh`).
- B300 deployment (offline `setup.sh` 14 stages on real hardware).
- Full Qwen3-Coder-480B-FP8 model bundling.
- E2E scenario suite against a real vLLM.

[0.1.0]: https://github.com/squall321/KooLoopCoder/releases/tag/v0.1.0
