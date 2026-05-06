# LoopCoder — Handoff Document for the Next AI Agent

> **Audience**: a fresh AI agent (or any engineer) inheriting this project.
> **Purpose**: read THIS one file and you can resume work without re-discovering anything.
> **Last updated**: 2026-05-06
> **Project root**: `/home/koopark/claude/KooDynaOptimizer/LoopCoder/`
> **Plan / progress canon**: `PLAN.md` and `PROGRESS.md` (this file is a snapshot summary; those two are the authoritative sources)

---

## 0. One-paragraph summary

The user is building **LoopCoder**: an iterative coding agent that runs on a self-hosted vLLM (target hardware: NVIDIA B300 × 8, 2,304 GB HBM, Ubuntu 24.04, **no internet**). The agent reads a `plan.yaml` of goals + acceptance checks, calls tools, and iterates until every goal's acceptance check passes. Verification runs **outside** the LLM so the model cannot fake completion. The whole stack (vLLM container, Apptainer sandbox, Python wheels, .deb packages, model weights) is bundled on a separate Ubuntu 22.04 host (with internet) inside a 24.04 VM, transferred to the B300, and installed offline. The user's bigger plan also includes a future LSDyna debugger using the same loop, but only the generic agent (#1) is in scope right now.

Everything is written; the remaining work is **VM/B300 live verification**.

---

## 1. Where am I, what is built, what is not

### 1.1 Filesystem at a glance

```
LoopCoder/                                    ← repo root (this project)
├── PLAN.md                                   ← authoritative plan (rev.6, ~1200 lines)
├── PROGRESS.md                               ← live status board (~340 lines, append-only log)
├── HANDOFF.md                                ← this file
├── README.md
├── INSTALL.md
├── pyproject.toml
├── .gitignore
├── .venv/                                    ← Python 3.12 venv (loopcoder editable-installed; gitignored)
├── setup.sh                                  ← B300 offline installer (14 stages)
├── bundle.sh                                 ← host orchestrator (Ubuntu 22.04)
├── test_setup.sh                             ← Test VM auto-validator
├── agent/loopcoder/                          ← Python package (50 files, 5,411 lines)
├── agent/tests/unit/                         ← 19 test files, 123 tests, all PASS
├── bundle/{vm,in_vm,test_vm}/                ← VM provisioning + collectors (19 shell scripts)
├── config/                                   ← 5 YAML examples (install/vllm/loopcoder + .tiny)
├── containers/{vllm.def,loopcoder-sandbox.def}
├── systemd/vllm.service.template
├── scripts/                                  ← healthcheck/benchmark/uninstall/make_apptainer_images
│   └── windows/                              ← Download-Model.ps1 + .bat + Verify-Model.ps1
├── docs/manuals/model-download-windows.md
├── examples/                                 ← plan_simple/plan_refactor/plan_fastapi_hello + tiny-end-to-end.sh
├── vscode-extension/                         ← TypeScript VS Code extension (4 src files, compiles)
└── output/                                   ← gitignored; build artifacts go here
    └── tiny-test/models/Qwen2.5-Coder-0.5B-Instruct/   ← 954MB downloaded (verified, not committed)
```

### 1.2 Built and verified (host-level)

- **Python package** (`loopcoder/`): config/plan/llm/tools/sandbox/state/loop/ui + api + mcp + logsetup + events. 50 files, 5,411 lines. **All imports OK.**
- **23 LLM tools** registered, with **2 pre-hooks + 5 post-hooks** wiring Claude-Code-style guarantees (read-before-write, auto git-add, etc.).
- **123 / 123 unit tests pass** — see `agent/tests/unit/`. Includes mock-LLM controller integration (1-iter pass, retry-then-pass, max-iter clean fail, token persistence).
- **HTTP API** (FastAPI, 16 routes): live-tested with `curl`, returns 23 tools, sessions, etc. Bearer token optional via `LOOPCODER_API_KEY`.
- **MCP server**: stdio + SSE transports. Tool list and call_tool both verified via unit tests.
- **CLI**: `loopcoder run / list / status / report / tokens / export / config / serve / mcp`. Supports `--dry-run` (no LLM, runs acceptance only) and `--log-dir` (JSON rotating handler).
- **VS Code extension**: TypeScript compiles with 0 errors via `npx tsc -p .`. Sessions/Tools tree views, 7 commands, SSE log streaming.
- **Windows scripts**: PowerShell + .bat for downloading models, with sleep override, Defender exclusion, resume support.
- **Tiny model downloaded**: `Qwen/Qwen2.5-Coder-0.5B-Instruct` is sitting at `output/tiny-test/models/` (954 MB, 1m17s).
- **All 24 shell scripts** pass `bash -n`.
- **3 YAML configs** (install/vllm/loopcoder) round-trip through Pydantic.
- **3 plan examples** (`plan_simple.yaml`, `plan_refactor.yaml`, `plan_fastapi_hello.yaml`) all validate.

### 1.3 Built but not yet exercised (`△ untested`)

- Bundle VM lifecycle (setup_vm.sh / start_vm.sh / collect_*.sh) — the VM has not been booted yet.
- Test VM lifecycle (similar) — not booted.
- `setup.sh` end-to-end on a real Ubuntu 24.04 system.
- Apptainer image builds (`vllm.sif`, `loopcoder-sandbox.sif`).
- `examples/tiny-end-to-end.sh` (would install vLLM into venv ~5–10 min then run the agent against the tiny model).
- VS Code extension `.vsix` package + actual installation in VS Code.

### 1.4 Blocked on external resources (`⏸`)

- E2E-1..7 (real coding scenarios) — need a running vLLM (target on B300, or smaller on dev host).
- B300 deployment — needs the actual server, network-isolated.
- 480 GB Qwen3-Coder-480B FP8 download — long, large.
- 24-hour stability test — long.

---

## 2. Architecture (one diagram)

```
   plan.yaml (goals + acceptance)                     /etc/loopcoder/{install,vllm,loopcoder}.yaml
              │                                                       │
              ▼                                                       ▼
   ┌─────────────────────────────────────────────────────────────────────────────────┐
   │  loopcoder.cli  (click)                                                         │
   │      run / serve / mcp / list / status / report / export / config / dry-run    │
   └─────────────────────────────────────────────────────────────────────────────────┘
              │                       │                       │
              ▼                       ▼                       ▼
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │ LoopController     │    │  HTTP API          │    │  MCP server        │
   │  (events.EventBus) │◄───│  FastAPI 16 routes │    │  stdio | SSE       │
   │  ContextBuilder    │    │  + SSE /events     │    │  ToolRegistry→Tool │
   │  Verifier          │    │                    │    │                    │
   │  SnapshotManager   │    └────────────────────┘    └────────────────────┘
   │  ToolRegistry+Hooks│             │                          ▲
   │  TodoList / BgJobs │             ▼                          │
   └─────────┬──────────┘    ┌────────────────────┐               │
             │               │ VS Code extension  │       Claude Desktop / Code /
             │ OpenAI client │ tree views + SSE   │       custom MCP clients
             ▼               └────────────────────┘
   ┌────────────────────┐
   │ vLLM (Apptainer)   │  Apptainer + systemd on B300; or pip-installed for dev tiny-test
   │ Qwen3-Coder-480B   │
   │ (or Qwen2.5-       │
   │  Coder-0.5B for    │
   │  tiny tests)       │
   └────────────────────┘
```

Key decoupling: every tool the agent uses is also exposed via HTTP and via MCP, **with the same JSON schemas**. Sub-agents (`spawn_agent` tool) and external MCP clients can both reach into LoopCoder without touching the controller.

---

## 3. The 25 decisions you don't need to re-litigate (PLAN §2)

| ID | Decision |
|---|---|
| D1 | Default model = `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` |
| D2 | Inference engine = vLLM ≥ 0.7, packaged as Apptainer .sif |
| D3 | Quantization = FP8 |
| D4 | Model source path on B300 = `/models/<id>/` |
| D5 | Model runtime path = `/scratch/models/<id>/` |
| D6 | Agent built from scratch in Python (no OpenHands/Aider) |
| D7 | Sandbox = Apptainer (NOT Docker — HPC-friendly) |
| D8 | systemd manages vLLM service |
| D9 | No HuggingFace token (public models only) |
| D10 | B300 is **offline** — bundle workflow required |
| D11 | Context length = 256K (`max_model_len`) |
| D12 | Single-user, `max_num_seqs=8` |
| D13 | All knobs externalized to 3 YAML files |
| D14 | Context preservation policy: verify logs and pinned files **never truncated/summarized** |
| D15 | Project codename = **LoopCoder** (Python pkg/CLI = `loopcoder`) |
| D16 | Model swap = edit `install.yaml`'s `model.id` and rebuild bundle |
| D17 | Bundle host = this dev machine (Ubuntu 22.04, virt-manager already installed) |
| D18 | Bundle build environment = Ubuntu 24.04 VM (KVM/libvirt) — host/target OS mismatch fix |
| D19 | VM ↔ host data exchange = virtiofs |
| D20 | Setup correctness verified by Test VM (24.04 + no-internet + no-GPU) |
| D21 | VM disks = `LoopCoder/output/vm-disks/` |
| D22 | Bundle output = `LoopCoder/output/bundle/` |
| D23 | Test scratch = `LoopCoder/output/test-scratch/` |
| D24 | Test results = `LoopCoder/output/test-results/` |
| D25 | Tiny dev model = `Qwen/Qwen2.5-Coder-0.5B-Instruct` |

If the user changes any of these, **PLAN.md must be updated first**, then PROGRESS.md, then code.

---

## 4. Code-level layout

### 4.1 `loopcoder/` package

```
loopcoder/
├── __init__.py / __main__.py / cli.py / config.py / logsetup.py / events.py
├── plan/         schema.py, parser.py, topo.py
├── llm/          client.py, prompts.py, tokens.py, context.py
├── tools/        base.py, registry.py, hooks.py, fs.py, shell.py, git.py,
│                 tests.py, meta.py, todo.py, spawn_agent.py
├── sandbox/      base.py, host.py, apptainer.py
├── state/        store.py (SQLite), snapshot.py (git tags), replay.py
├── loop/         controller.py, verifier.py, critic.py, strategy.py,
│                 reminders.py, conventions.py
├── ui/           tty.py (rich), report.py
├── api/          server.py (FastAPI), runner.py (threaded sessions), models.py
└── mcp/          server.py (stdio + SSE bridge)
```

**Hot files when you need to change behavior:**

| Want to change… | Edit… |
|---|---|
| What the LLM sees in its system prompt | `llm/prompts.py` |
| The "always remind the model of these rules" reminder injected each iter | `loop/reminders.py` |
| The PDCA loop (when to retry, rollback, strategy-change) | `loop/controller.py`, `loop/strategy.py` |
| Acceptance check kinds | `plan/schema.py` (Pydantic discriminated union) + `loop/verifier.py` |
| What budgets/policies are exposed to users | `config.py` (Pydantic models) and `config/loopcoder.yaml.example` together |
| Add/modify a tool | `tools/<x>.py` + register in `tools/registry.py:default_registry()` |
| Add a hook (e.g., audit trail on every shell call) | `tools/hooks.py:default_hooks()` |
| API surface | `api/server.py` (route fns) + `api/models.py` (Pydantic) |
| MCP tool exposure | `mcp/server.py` (auto-mirrors registry; usually nothing to do) |
| VS Code UI | `vscode-extension/src/*.ts`, then `npx tsc -p .` |

### 4.2 The 23 tools the agent has

`read_file`, `read_files`, `write_file`, `edit_file`, `apply_patch`,
`list_dir`, `grep`, `find_files`,
`run_shell`, `run_shell_background`, `read_background_output`, `kill_background_job`, `list_background_jobs`,
`run_tests`,
`git_status`, `git_diff`, `git_log`, `revert_to_snapshot`,
`record_thought`, `submit_goal`,
`todo_write`, `todo_read`,
`spawn_agent`.

Hooks attached:
- **pre** `write_file`, `edit_file` → require prior `read_file` of the path (CC3).
- **post** `read_file`, `read_files` → record into `ctx.read_files`.
- **post** `write_file`, `edit_file`, `apply_patch` → record into `ctx.written_files` + `git add`.

### 4.3 Plan schema (what the user authors)

```yaml
project: { name, workspace, language? }
constraints: { max_iterations_per_goal, max_total_minutes, max_tokens_per_iter,
                forbidden_paths[], allowed_shell_commands[], network_allowed }
context:    { description, files_to_read_first[], reference_docs[], pin_in_context[] }
goals:
  - id: <uniq>
    title: <str>
    description: <str>
    depends_on: [...]
    priority: <int>
    acceptance:                        # ← this is the contract; agent cannot fake it
      - { kind: shell, run, cwd?, timeout, expect: { exit_code, stdout_contains?, stderr_not_contains?, stdout_matches? } }
      - { kind: file_exists, path }
      - { kind: file_contains, path, pattern }       # regex
      - { kind: file_not_contains, path, pattern }
      - { kind: http, prepare?, request: {method,url,headers?,body?}, expect: {status, body_contains?} }
llm:        { model?, temperature?, top_p?, max_completion_tokens? }   # plan-level overrides
```

---

## 5. Operational context the user cares about

Excerpted from the user's actual instructions to me:

1. **No hardcoding, no fake "done"**: every component must be real, not stubbed. Don't claim done unless verification proves it.
2. **Real-time PROGRESS.md updates** (PLAN §0.1): every meaningful action — file written, test run, decision changed — must be reflected in `PROGRESS.md` immediately, not in batches.
3. **Plan first, code second**: changes to scope or interfaces go to PLAN.md before they go to code. PLAN.md and PROGRESS.md must stay consistent.
4. **Don't add features the user didn't ask for** (CC10). The CC patterns from Claude Code's leaked system prompt have been incorporated; review `agent/loopcoder/llm/prompts.py` for tone.
5. **Verification is external** — the entire PDCA loop is built around the principle that the LLM's "I'm done" claim is meaningless until acceptance checks pass.
6. **Context preservation > context compression**: verify logs and pinned files are never summarized (`llm/context.py:NEVER_TRUNCATE`).
7. **The user works in Korean** — answer in Korean; technical identifiers stay English.
8. **bkit Feature Usage block** at the end of every assistant response — this is a project-wide reporting convention; keep it concise.

---

## 6. Environments

### 6.1 Dev host (this machine)

- Ubuntu **22.04.5** LTS, kernel 6.8 family, GPU = RTX 5070 Ti (Blackwell sm_120, 16 GB)
- `/data/` has 6 TB free
- `virt-manager` already installed (the user confirmed)
- Python 3.10/3.11/3.12 available; **the project uses 3.12** in `LoopCoder/.venv/`
- Node 20 + npm 10 available (for VS Code extension)
- This is the bundle host AND the dev/test host. Bundle VM (24.04) is launched here; Test VM (24.04, no-internet, no-GPU) likewise.

### 6.2 Target host

- B300 × 8, Ubuntu 24.04, kernel 6.8.0-107-generic
- CUDA ≥ 12.8 + Blackwell driver pre-installed
- **No internet**. Reads bundle from `/models` (NFS), stages model to `/scratch/models/`, runs vLLM via Apptainer + systemd.

### 6.3 Python venv state

```bash
cd /home/koopark/claude/KooDynaOptimizer/LoopCoder
.venv/bin/loopcoder --version            # → loopcoder, version 0.1.0
.venv/bin/pytest agent/tests/unit/ -q    # → 123 passed
```

`pyproject.toml` declares `fastapi, uvicorn[standard], sse-starlette, mcp, pydantic, pyyaml, jinja2, openai, tiktoken, rich, click, GitPython, sqlalchemy, tenacity, platformdirs, httpx`. Plus `huggingface_hub + hf_transfer` were installed manually for the tiny-model download.

---

## 7. Daily-driver command cheatsheet

```bash
# Run unit tests
cd /home/koopark/claude/KooDynaOptimizer/LoopCoder
.venv/bin/pytest agent/tests/unit/ -q

# Validate every YAML config
.venv/bin/loopcoder config validate \
    --install   config/install.yaml.example \
    --vllm      config/vllm.yaml.example \
    --loopcoder config/loopcoder.yaml.example

# Validate a plan + run its acceptance checks against the workspace WITHOUT calling the LLM
LOOPCODER_YAML=/tmp/lc-dev/loopcoder.yaml \
.venv/bin/loopcoder run --plan examples/plan_simple.yaml --dry-run

# Start the HTTP API (background)
LOOPCODER_YAML=/tmp/lc-dev/loopcoder.yaml \
.venv/bin/loopcoder serve --host 127.0.0.1 --port 8765

# Hit the API
curl -s http://127.0.0.1:8765/v1/health
curl -s http://127.0.0.1:8765/v1/tools | jq '.[].name' | head

# Run as MCP stdio server (Claude Desktop config: command="loopcoder", args=["mcp"])
.venv/bin/loopcoder mcp --transport stdio
# Or HTTP+SSE:
.venv/bin/loopcoder mcp --transport sse --port 8766

# Compile + package the VS Code extension
cd vscode-extension
npm install --silent
npx tsc -p .
npx --yes @vscode/vsce package --out dist/   # → loopcoder-vscode-0.1.0.vsix
code --install-extension dist/loopcoder-vscode-0.1.0.vsix

# Build the bundle VM (24.04) inside this 22.04 host
sudo bash bundle/vm/setup_vm.sh \
     loopcoder-bundle-vm \
     /home/koopark/claude/KooDynaOptimizer/LoopCoder/output/vm-disks \
     /home/koopark/claude/KooDynaOptimizer/LoopCoder/output/bundle \
     loopcoder
sudo bash bundle/vm/start_vm.sh loopcoder-bundle-vm \
     /home/koopark/claude/KooDynaOptimizer/LoopCoder/output/vm-disks loopcoder

# OR just run the orchestrator (it does both):
bash bundle.sh                    # full bundle (~hours, downloads 480GB model)
bash bundle.sh --tiny-model       # tiny model variant — fast
bash bundle.sh --skip-model       # use already-downloaded model in output/bundle/models/
bash bundle.sh --dry-run          # show plan only

# Validate the bundle by running setup.sh in a Test VM (no internet, no GPU)
bash test_setup.sh

# Tiny end-to-end (smoke test the whole loop on this host with the tiny model)
bash examples/tiny-end-to-end.sh   # installs vLLM into venv first time (~5-10 min)
```

---

## 8. The next 5 things to do (priority order)

1. **`bash examples/tiny-end-to-end.sh`** — installs vLLM into `.venv`, downloads tiny model if missing (already there: `output/tiny-test/models/Qwen2.5-Coder-0.5B-Instruct/`), boots vLLM on `:18000`, runs `loopcoder run --plan` against a hello-world plan, generates a session report. **This is the fastest end-to-end signal that the entire stack works.** Expected runtime: 10–15 min (mostly the vLLM install).

2. **Bundle VM dry-run**: `bash bundle.sh --tiny-model --skip-model --dry-run`, then drop `--dry-run`. Validates the libvirt/cloud-init/virtiofs path. Failure modes are usually around `apt-rdepends` not being available pre-bootstrap or virtiofs not enabled in the host kernel — both are diagnosed in `bundle/vm/setup_vm.sh` output.

3. **Test VM run**: `bash test_setup.sh --bundle output/bundle`. With even a partial bundle (apt + wheels + sandbox.sif but maybe no vllm.sif yet), this exercises stages 0/2-9/12-13 of `setup.sh` and produces a markdown verdict in `output/test-results/`.

4. **VS Code extension manual smoke**: `cd vscode-extension && npm install && npm run package && code --install-extension dist/*.vsix`. Open VS Code, see the LoopCoder activity bar, click "Show API Health". Requires `loopcoder serve` running.

5. **Real Qwen3-Coder-480B-FP8 download**. The Windows-side scripts are in `scripts/windows/`; the Linux-side equivalent is `bundle/in_vm/collect_model.sh`. After this you can do a real B300 deployment.

---

## 9. Things that will trip up the next agent

- **Markdown lint warnings (MD060/MD032/etc.)** are produced after every Edit on .md files; **ignore them**, they are non-blocking style hints from the IDE.
- **`loopcoder` CLI requires `LOOPCODER_YAML`** to point at a user-writable config when running outside `sudo`, or it tries to create `/var/lib/loopcoder` and fails. Tests handle this with `monkeypatch.setenv`.
- **The `loopcoder` package previously had a `logging.py` file** that shadowed stdlib — it was renamed to `logsetup.py`. If you see references to `loopcoder.logging`, those are stale and wrong.
- **`tests/conftest.py` puts `agent/` on `sys.path`** before tests run, so the package resolves without installation. But the editable install in `.venv` is the real mechanism; tests usually go through that.
- **`bash test_setup.sh` requires Test VM cloud image already cached at `output/vm-disks/ubuntu-24.04-server-cloudimg-amd64.img`** (the Bundle VM's `setup_vm.sh` downloads it). If you run `test_setup.sh` standalone before the Bundle VM has run once, edit `bundle/test_vm/setup_test_vm.sh` to also download.
- **The MCP SDK API surface differs by version**. Tests use `Server.request_handlers` lookups via `mcp.types.ListToolsRequest` / `CallToolRequest` — if `mcp` is upgraded and these names change, fix `agent/tests/unit/test_mcp.py` (and `agent/loopcoder/mcp/server.py` if needed).
- **Apptainer's `--net --network=none`** requires user namespaces to be enabled OR fakeroot. On B300 this should work; on dev hosts you may need to set `--no-net` instead. The sandbox falls back to env-only restriction if namespacing fails — review `loopcoder/sandbox/apptainer.py` if Test VM checks fail.
- **`prompts.py` was rewritten in Claude-Code style (CC8/CC10)** — direct, imperative, "do/do not". Don't soften the tone; that's an explicit decision from the user.
- **`controller.py` emits 9 event types** (`session.started/ended`, `goal.started/ended`, `iter.started/ended`, `tool.called`, `verify.failed`, plus heartbeats from the SSE handler). The VS Code extension's Output channel parses these; if you add a new emit, also handle it in `vscode-extension/src/extension.ts:streamLog`.

---

## 10. Working norms (PLAN §0)

> These are non-negotiable per the user.

- **0.1** Update PROGRESS.md immediately after every file create/modify/delete and every verification run. Don't batch.
- **0.2** Don't claim "done" without a verification command and its result.
- **0.3** No hardcoding or fake-completion. If a function is a stub, mark it `▣ wip` in PROGRESS, not `■ done`.
- **0.4** Stage gating: don't begin step N+1 until step N is `■ done`.
- **0.5** Keep PLAN.md and PROGRESS.md consistent. Adding an artifact that PLAN doesn't anticipate? Update PLAN first.
- **0.6** PROGRESS.md changelog is **append-only**.

---

## 11. Pointers for specific tasks

| Task | Read first | Then edit |
|---|---|---|
| Add a new tool | `tools/base.py`, an existing tool like `tools/git.py` | new file under `tools/`, then `tools/registry.py:default_registry()` |
| Add a new acceptance kind | `plan/schema.py` (the discriminated union), `loop/verifier.py` | both files |
| Tighten / loosen the system prompt | `llm/prompts.py` | only that file |
| Change context preservation policy | `llm/context.py` (`PRIORITY`, `NEVER_TRUNCATE`) | that file + `loop/controller.py:_run_goal` if you add a new section kind |
| Add an HTTP endpoint | `api/server.py`, `api/models.py` | both |
| Wire the VS Code extension to a new endpoint | `vscode-extension/src/api.ts` | api.ts + `extension.ts` (add a command) |
| Change a default path | `config.py` AND `config/*.yaml.example` AND `setup.sh` env defaults |  |
| Add an auto-loaded convention file | `loop/conventions.py:CONVENTION_FILE_NAMES` | one line |
| Add a CC pattern | PLAN.md §12-α first, then PROGRESS Phase 2D row, then code |  |

---

## 12. Test inventory (123 tests today)

| File | Tests | Validates |
|---|---|---|
| `test_config.py` | 12 | YAML load/merge/env-expansion, three configs |
| `test_plan_schema.py` | 11 | discriminated acceptance union, dep validation |
| `test_plan_topo.py` | 4 | topo sort + priority tie-break |
| `test_tokens.py` | 3 | TokenCounter approximation |
| `test_tools_fs.py` | 16 | read/edit/write/grep/find + forbidden-path glob + read-before-write hook |
| `test_hooks.py` | 4 | HookRegistry + default hooks behavior |
| `test_todo.py` | 5 | TodoWrite invariants + persistence |
| `test_shell_bg.py` | 7 | run_shell allowlist/timeout/output cap + background lifecycle |
| `test_conventions.py` | 4 | CLAUDE.md/AGENTS.md auto-loader |
| `test_reminders.py` | 2 | ReminderState rendering |
| `test_context.py` | 4 | priority drop ordering, NEVER_TRUNCATE enforcement |
| `test_state_store.py` | 6 | sessions + goals + iterations + todos + tool_calls CRUD |
| `test_sandbox.py` | 5 | host backend, apptainer argv rendering, dispatch |
| `test_verifier.py` | 10 | every acceptance kind, timeout, regex, combined |
| `test_snapshot.py` | 5 | git init + tag + revert + diff |
| `test_controller_integration.py` | 4 | mock-LLM PDCA: 1-iter pass, retry-then-pass, max-iter clean fail, token persistence |
| `test_cli.py` | 7 | version, help, list, dry-run pass/fail, export, env override |
| `test_api.py` | 10 | health, tools list/call/unknown, sessions list/get/iters, auth, report, export |
| `test_mcp.py` | 4 | server build, list_tools, call_tool, unknown tool |

---

## 13. If you have only 5 minutes

```bash
cd /home/koopark/claude/KooDynaOptimizer/LoopCoder
cat PLAN.md | head -200          # decisions and architecture
cat PROGRESS.md | tail -80       # what was done, in order
.venv/bin/pytest agent/tests/unit/ -q
.venv/bin/loopcoder --help
ls output/                          # see what build artifacts exist locally
```

That gives you the entire mental model.

---

## 14. Authoritative documents

| File | What to use it for |
|---|---|
| `PLAN.md` | Architecture, decisions, deliverables, risks. **Source of truth for scope.** |
| `PROGRESS.md` | Per-artifact status + append-only worklog. **Source of truth for "where are we now".** |
| `LoopCoder/INSTALL.md` | End-user installation flow (bundle host → B300). |
| `LoopCoder/README.md` | Project overview / quickstart. |
| `LoopCoder/docs/manuals/model-download-windows.md` | The 480GB-on-Windows playbook. |
| `LoopCoder/scripts/windows/README.md` | Windows download script reference. |
| `LoopCoder/vscode-extension/README.md` | VS Code extension features + settings. |
| **THIS FILE** | One-shot context for resuming. |

---

## 15. Final note to the next agent

Three things the user has been very clear about:

1. **"Do everything carefully and don't fake completion"** — pretend you're being audited.
2. **"Update progress in real time, every step"** — `PROGRESS.md` is your ledger.
3. **"Plan thoroughly before building"** — `PLAN.md` is the contract; if you change scope, update it first.

The user trusts the loop to be honest. Keep it honest.
