# LoopCoder — operating procedures (pick a mode, follow the steps)

Every mode shares **Step 0: build the SIF bundle once** on a Linux host
that has `apptainer` + internet. After that the modes differ only in
*how the bundle + model reach the GPU machine* and *how it runs there*.

```
                         ┌─ Step 0 ─────────────────────────────┐
                         │ scripts/build-sif-bundle.sh           │
                         │  → containers/{vllm,suite,sandbox}.sif │
                         │  → source/LoopCoder/  win-tools/        │
                         └───────────────┬───────────────────────┘
                                          │
        ┌─────────────────────────────────┼─────────────────────────────────┐
        ▼                                 ▼                                 ▼
  Mode A: Linux→Linux            Mode B: Windows-mediated          Mode C: HPC / Slurm
  one command (deploy.sh)        (offline B300, no WSL2)           (no root, job-based)
```

## Which mode?

| Your situation | Mode |
|---|---|
| Build host can SSH to the GPU box, both Linux, GPU box may reach internet or not | **A** — `deploy.sh` |
| GPU box (B300) is air-gapped; only a Windows PC has internet | **B** — Windows-mediated |
| Shared cluster, no sudo/systemd, jobs via Slurm | **C** — HPC |

---

## Step 0 — Build the SIF bundle (all modes)

On a Linux host with `apptainer ≥ 1.3` + internet (no VM, no WSL2, no
root needed beyond what `apptainer build` asks):

```bash
git clone https://github.com/squall321/KooLoopCoder
cd KooLoopCoder
bash scripts/build-sif-bundle.sh            # → output/sif-bundle/
# options: --output DIR  --skip-vllm  --skip-wheels  --no-win-tools  --dry-run
```

Produces `output/sif-bundle/`:

```
containers/vllm.sif  loopcoder-suite.sif  loopcoder-sandbox.sif
source/LoopCoder/       setup.sh + all helpers
win-tools/              cwRsync (only used by Mode B)
manifest.sha256
```

The model is **not** in the bundle — it travels separately per mode.

---

## Mode A — Linux → Linux, one command

Build host (or any host with the bundle) SSHes to the GPU server.

**Prerequisites on the GPU server:** SSH key access, `sudo`, Ubuntu
24.04, `apptainer` installed (or let the legacy apt bundle install it).

```bash
# 1. (done) Step 0 produced output/sif-bundle/
# 2. one command — rsync bundle + run setup.sh remotely
sudo bash scripts/deploy.sh user@gpuhost \
    --bundle output/sif-bundle --remote-bundle /models

#   variants:
#     --apt-only        just stage + apt, skip setup.sh
#     --setup-only      bundle already on the remote
#     --skip-gpu-stages non-GPU / Test VM target
```

Then on the GPU server the model is staged and `setup.sh` runs all
stages (pack model → install SIFs to `/opt/apptainers` → render +
enable systemd `vllm` / `loopcoder`).

**Model:** put the model where `deploy.sh`/`setup.sh` expects it
(`--model-src DIR`, or `deploy.yaml` `model.mode: rsync|hf|none`, or
multi-model `models[]` — see Mode B step 4 for the model layout, it is
identical).

**Verify:**

```bash
ssh user@gpuhost 'systemctl status vllm loopcoder --no-pager | head'
ssh -L 8765:127.0.0.1:8765 user@gpuhost      # then curl :8765/v1/health
```

Full reference: [`one-command-deploy.md`](one-command-deploy.md).

---

## Mode B — Windows-mediated (air-gapped B300, no WSL2)

The B300 has no internet; a Windows PC does. The bundle is built on
Linux (Step 0), carried to Windows, which downloads the model and
ferries everything to the B300 over SSH.

```
1. (Linux, Step 0)  build-sif-bundle.sh  → output/sif-bundle/
2. copy output/sif-bundle/  →  Windows  (USB / SMB / scp)
3. (Windows) deploy in one command:
     .\scripts\windows\Deploy-To-Linux.ps1 `
        -Target user@b300 -BundleDir D:\loopcoder-bundle `
        -Profile b300x8                 # or -ModelId / -ConfigYaml
4. (B300, automatic) setup.sh: pack model → SIFs → systemd up
```

- Single model: `-Profile b300x8` (catalog picks it) or `-ModelId <hf>`
  or `-ModelDir <local> -SkipModelDownload`.
- **Multi-model:** `-ConfigYaml deploy.yaml` with a `models[]` list →
  one `vllm@<key>` per model. Same `models[]` block goes in
  `install.yaml`.
- Transfer uses the bundled cwRsync (no WSL2, no rsync install);
  falls back to scp.

**Model layout on the target:** `/scratch/models/<leaf>/` per model;
`setup.sh` packs each into `model-<key>.sif`.

Prereqs: Windows 10/11, Python 3.10+, OpenSSH client, passwordless SSH
key to the B300, the B300 already has `apptainer`.

Full reference: [`windows-mediated-deploy.md`](windows-mediated-deploy.md).

---

## Mode C — HPC / Slurm (no root, job-based)

Shared cluster: no sudo, no systemd, no writable `/opt /var /etc`.
SIFs built off-cluster (Step 0); the cluster only *runs* them in
Slurm jobs. All state under `$LOOPCODER_HOME`.

```bash
# 1. (Linux build host, Step 0)  build-sif-bundle.sh
#    + fetch the model(s):
bash scripts/fetch-models.sh --config deploy.yaml --dest /data/models

# 2. (HPC login node) lay out + see what to copy
export LOOPCODER_HOME=$SCRATCH/loopcoder
bash scripts/hpc/loopcoder-hpc.sh init

# 3. copy in (scp/rsync/Globus):
#    $LOOPCODER_HOME/sif/{vllm,loopcoder-suite,loopcoder-sandbox}.sif
#    $LOOPCODER_HOME/models/<leaf>/         (or model-<leaf>.sif)
#    $LOOPCODER_HOME/etc/install.yaml       (models[] or model.id)
#    $LOOPCODER_HOME/etc/loopcoder.yaml     (llm.base_url=:8000/v1)

# 4. run — all-in-one job (vLLM up → run plan → exit)
bash scripts/hpc/loopcoder-hpc.sh submit-allinone plan.yaml \
    --model fast --partition gpu --gpus 1 --time 02:00:00

#    or a long-lived serving job:
bash scripts/hpc/loopcoder-hpc.sh submit-serve --model big --gpus 8
```

Multi-model and the Blackwell/sm_120 + tool-parser handling carry over
automatically (resolved from the catalog by model id).

Full reference: [`hpc-slurm.md`](hpc-slurm.md).

---

## Common cross-cutting facts

- **Model selection:** a plan picks a model via `llm.model: <key>`
  (multi-model) or it's the single configured model. `default_model`
  is used when a plan doesn't name one.
- **Quantization / tensor-parallel / max-len / tool-parser:** never
  hand-set — resolved from `config/model-catalog.yaml` by model id
  (unknown ids fall back to fp8/awq/gptq name heuristics).
- **API reachability:** systemd modes serve `0.0.0.0:8765` by default
  (tighten via `/etc/loopcoder/loopcoder.env`); HPC prints the compute
  node IP:port. Use a bearer token (`LOOPCODER_API_KEY`) or SSH tunnel
  when exposing it.
- **Upgrades (systemd modes):** re-push the bundle, then
  `scripts/upgrade-suite.sh` — model SIF stays put.
