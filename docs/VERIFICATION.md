# Verification status — what's proven vs. scripted-only

Honest matrix. "Verified" = actually executed end-to-end and observed
to work. "Logic-verified" = the unit/parsing logic was executed and
checked, but the full real-hardware flow was not. "Scripted only" =
code written + syntax/unit checks pass, but never run on the real
target.

Last updated: 2026-05-27. Hardware available for verification: one
**RTX 5070 Ti (16 GB, Blackwell sm_120)** dev box. No B300, no Windows
host, no Slurm cluster available here.

## 2026-05-27 — B300 readiness gap fixes

These were missing/broken when "ready for B300 (24.04, driver
580.159.04 + CUDA 13, offline)" was reviewed and are now fixed:

| Gap | Fix | Status |
|---|---|---|
| SIF-only bundle had no apt closure for apptainer (B300 has driver only) | `build-sif-bundle.sh` collects `apt/` on 24.04 hosts; on other hosts auto-skips with an explicit docker one-liner to get the .deb closure | ✅ Logic-verified (dry-run on 22.04 prints the guide; collect_apt.sh itself is the same one used by the legacy VM path) |
| setup.sh stage 4 just failed if apptainer absent | Now points operators at the exact bundle/build path to fix it | ✅ Unit test |
| Blackwell sm_100/120 vLLM crash (FlashInfer arch bug) | `vllm.def` strips `flashinfer-python` at build time; `setup.sh` auto-detects compute_cap via nvidia-smi and writes `TORCH_CUDA_ARCH_LIST` + `VLLM_USE_FLASHINFER_SAMPLER=0` for Blackwell; both systemd templates `--env` propagate them into the container | ✅ Verified on RTX 5070 Ti (auto-detected `12.0`); applies identically to B300 sm_100 |
| Manual env required per host | Fully automated from `nvidia-smi compute_cap` | ✅ Unit + live |
| `apt-get install ./*.deb` instead of standard `apt install <pkg>` | bundle/apt/ ships with `Packages.gz`; setup.sh + deploy.sh register a `file://` source, call `apt-get install -y apptainer ...` by name, then tear the source down. Operators get normal `apt list --installed` / `apt remove` behavior | ✅ Unit-verified |
| `setup.sh` read `/etc/loopcoder/*.yaml` but nothing copied them — fresh B300 would fail at stage 7 / stage 9 | Stage 0 fails up front with the exact `cp` + `$EDITOR install.yaml` commands the operator needs. We **do not auto-seed** the examples — that would silently set the box up for the example's model (480B-FP8) rather than what the operator intends. deploy.sh runs the same check after rsync so the guidance shows there too | ✅ Unit-verified |
| `models_list` / `default_model_key` swallowed YAML parse errors with `2>/dev/null \|\| true` → silent single-model fallback | Errors now surface to stderr; early-return only when install.yaml is genuinely absent | ✅ Unit-verified |
| `deploy.sh` skipped stage 7 whenever it had staged the model itself → systemd had no `model.sif` | When `model.mode != none`, deploy.sh now passes `--model-src $MODEL_REMOTE` to setup.sh so stage 7 packs the SIF | ✅ Logic-verified |
| Stage 9 called `/usr/local/bin/loopcoder catalog-resolve`, created only in stage 12 (latent bug, would fail on first install) | Falls back to `apptainer exec $current/loopcoder-suite.sif loopcoder catalog-resolve` when the wrapper isn't there yet | ✅ Unit-verified |

---

## Core agent loop

| Item | Status | Evidence |
|---|---|---|
| LoopCoder loop (LLM ↔ tools ↔ external verify) | ✅ Verified | tiny e2e + 7B-AWQ runs: plan → hi.py written → `verify PASS (2/2)` → rc 0 |
| External verification cannot be faked | ✅ Verified | 0.5B (too weak) → `verify FAIL 0/2`, honest fail; 7B → PASS only when file really created |
| Content-fallback tool parsing | ✅ Verified | `recovered N tool call(s) from content fallback` → goal passed (commit d26fb5a) |
| Unit + mock-E2E suite | ✅ Verified | 202/202 pass, ruff clean (every commit) |

## Model selection automation

| Item | Status | Evidence |
|---|---|---|
| `catalog-resolve` model id → quant/tp/max-len/parser | ✅ Verified | 480B→fp8/tp8/262144, 7B-AWQ→awq_marlin/tp1, unknown→heuristic — executed live |
| `install.yaml` single `model.id` drives serving | ✅ Logic-verified | config parse + resolve executed; full setup.sh stage 9 render needs root |
| `models[]` multi-model parse + validation | ✅ Verified | config.py loads install/deploy.yaml.example; dup key/port rejected |
| `LlmConfig.resolve_endpoint` routing precedence | ✅ Verified | real loopcoder_mm.yaml: fast→:18001, big→:18002, unknown→flat (5 unit tests + live) |

## Model download automation

| Item | Status | Evidence |
|---|---|---|
| `fetch-models.sh` parses deploy.yaml models[] + resolves | ✅ Verified | dry-run lists 2 models with correct catalog params |
| HF `snapshot_download` actually pulls a model | ✅ Verified | 7B-AWQ 5.2 GB downloaded successfully this session |
| 480B (~480 GB) download | ❌ Not verified | size/time + no need on this box; logic identical to 7B path |
| Windows `Download-Model.ps1` | ⚠️ Scripted only | PowerShell — cannot run on this Linux box |

## Multi-model serving

| Item | Status | Evidence |
|---|---|---|
| Two vLLM instances side by side | ✅ Verified | 0.5B :18001 (4.3 GB) + 7B-AWQ :18002 (9.0 GB) = 13.6/16 GB, both `startup complete`, independent responses |
| Plan `llm.model: <key>` routes to that instance | ✅ Verified | `model: big` → only :18002 saw traffic (381 tok/s, Running:1), :18001 idle; verify PASS |
| `setup.sh` per-model `vllm@<key>` + model-<key>.sif | ✅ Logic-verified | models_list parse + per-model catalog-resolve executed; full stage 7/9/10 needs root + B300 |
| RTX 5070 Ti / Blackwell sm_120 vLLM | ✅ Verified | flashinfer removed + `VLLM_USE_FLASHINFER_SAMPLER=0` → serves + infers (1+1→"2") |

## Deployment modes

| Mode | Status | Evidence / gap |
|---|---|---|
| Step 0 — `build-sif-bundle.sh` | ⚠️ Scripted only | syntax + dry-run OK; full SIF build (vllm.sif ~7 GB) not run here |
| Mode A — `deploy.sh` (Linux→Linux) | ⚠️ Scripted only | syntax + arg checks OK; no real SSH B300 deploy executed |
| Mode B — Windows `Deploy-To-Linux.ps1` | ⚠️ Scripted only | PowerShell; structure reviewed; never run on Windows |
| Mode C — HPC `loopcoder-hpc.sh` | ⚠️ Scripted only | `init` + sbatch render verified (7 smoke tests); no real Slurm submit |
| `setup.sh` full stage 0–13 on real B300 | ❌ Not verified | needs root + 8× B300; only helper logic + single-7B path exercised |
| 480B model end-to-end serving | ❌ Not verified | needs B300; catalog values correct but vLLM 8-GPU tp not exercised |

---

## Summary

**Proven on real hardware (RTX 5070 Ti):** the agent loop, external
verification integrity, content-fallback parsing, model-selection
automation (catalog-resolve), multi-model concurrent serving, and
per-plan model routing — all executed end-to-end and observed.

**Logic-verified but not full-flow:** `setup.sh` multi-model staging
(helpers run; stages need root + B300), single-`model.id` serving
render.

**Scripted only — never run on the real target:** all three deploy
mode entrypoints (deploy.sh / Windows PS1 / HPC sbatch), the full SIF
bundle build, 480B-scale download and serving. These pass syntax/unit
checks but require B300 / Windows / Slurm access to claim "verified".

There is **no automated proof** that a fresh B300 / HPC run succeeds
end-to-end. Treat the deploy modes as "ready to test on real
hardware", not "guaranteed working".
