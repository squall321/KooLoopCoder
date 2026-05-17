# LoopCoder — Install Guide

LoopCoder runs from three self-contained Apptainer SIFs. You build the
bundle once on a Linux host with internet, then deploy it one of three
ways depending on your target.

> **Full step-by-step for every mode:**
> [`docs/manuals/PROCEDURES.md`](docs/manuals/PROCEDURES.md). This page
> is the quick map.

## Step 0 — Build the SIF bundle (always, on a Linux host)

Requires `apptainer ≥ 1.3` + internet. **No VM, no WSL2, no root**
(beyond what `apptainer build` itself needs).

```bash
git clone https://github.com/squall321/KooLoopCoder
cd KooLoopCoder
bash scripts/build-sif-bundle.sh        # → output/sif-bundle/
```

Output: `containers/{vllm,loopcoder-suite,loopcoder-sandbox}.sif`,
`source/LoopCoder/`, `win-tools/`, `manifest.sha256`. The model is
fetched separately (it never goes inside the bundle).

## Pick a deployment mode

| Target | Mode | Command |
|---|---|---|
| Linux GPU box reachable by SSH (you have sudo there) | **A** | `sudo bash scripts/deploy.sh user@host --bundle output/sif-bundle` |
| Air-gapped B300, only a Windows PC has internet | **B** | `Deploy-To-Linux.ps1 -Target user@b300 -BundleDir D:\bundle` |
| Shared HPC cluster, no root, Slurm jobs | **C** | `scripts/hpc/loopcoder-hpc.sh submit-allinone plan.yaml` |

Each mode's exact prerequisites, model layout, multi-model setup and
verification steps are in:

- Mode A: [`docs/manuals/one-command-deploy.md`](docs/manuals/one-command-deploy.md)
- Mode B: [`docs/manuals/windows-mediated-deploy.md`](docs/manuals/windows-mediated-deploy.md)
- Mode C: [`docs/manuals/hpc-slurm.md`](docs/manuals/hpc-slurm.md)
- Overview + decision guide: [`docs/manuals/PROCEDURES.md`](docs/manuals/PROCEDURES.md)

## Target prerequisites (common)

- NVIDIA driver + CUDA suitable for your GPU (Blackwell/sm_120: set
  `TORCH_CUDA_ARCH_LIST=12.0` if vLLM mis-detects the arch).
- `apptainer` installed on the GPU machine (Modes A/B/C all assume it;
  the legacy apt path can install it in Mode A only).
- Disk for the model under `/scratch/models` (systemd modes) or
  `$LOOPCODER_HOME/models` (HPC).

## Config (all modes)

Three YAMLs, copied from `config/*.example`:

- `install.yaml` — the **only thing you normally edit**: `model.id`
  (single) or `models[]` + `default_model` (multi). Quantization,
  tensor-parallel, max-len and the tool-call parser are resolved
  automatically from `config/model-catalog.yaml`.
- `vllm.yaml` — model-independent throughput/memory knobs only.
- `loopcoder.yaml` — agent loop, sandbox, `llm.base_url`.

Validate after edits: `loopcoder config validate`.

## Day-to-day commands

```bash
loopcoder list                       # past sessions
loopcoder status [SESSION_ID]        # progress / state
loopcoder report SESSION_ID > out.md
loopcoder config validate
loopcoder config show                # merged config
loopcoder catalog-resolve <model_id> # what serving params a model gets
```

## Resume / re-run (systemd modes)

```bash
sudo bash setup.sh                   # idempotent: skips completed stages
sudo bash setup.sh --stage 7         # force from a specific stage
sudo bash setup.sh --reinstall       # wipe markers, redo all
sudo bash setup.sh --uninstall       # remove install (keeps model cache)
```

## Upgrading the agent (systemd modes)

Re-push the bundle, then swap the SIF without touching the model:

```bash
sudo bash scripts/upgrade-suite.sh \
    /models/containers/loopcoder-suite.sif loopcoder-suite.sif
```

## Troubleshooting

| Symptom | Check |
|---|---|
| vLLM not coming up (systemd) | `journalctl -u vllm -n 200` (or `vllm@<key>` for multi-model) |
| vLLM dies on Blackwell GPUs | `export TORCH_CUDA_ARCH_LIST=12.0` before deploy/submit |
| "must read_file before edit" | Guardrail, not a bug — the agent must read a file first |
| Model wraps tool calls in markdown | Handled — the suite SIF's content-fallback parser recovers them |
| Legacy VM bundle (`bundle.sh`) | Still present but superseded by `build-sif-bundle.sh`; see PROCEDURES.md |

> The old virt-manager VM bundle pipeline (`bundle.sh` + `bundle/vm/`)
> still exists for reference but is **superseded** by the SIF-only flow
> above. New deployments should use Step 0 + a mode.
