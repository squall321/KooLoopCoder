# Windows-mediated deploy to an offline Linux GPU server (SIF-only, no WSL2)

> Scenario:
> - The **build machine** (any Linux with apptainer + internet) builds a
>   **SIF-only bundle** once.
> - That bundle is backed up to a **Windows PC** (internet OK, **no
>   WSL2**, **no rsync**).
> - Windows downloads the **HuggingFace model**.
> - Windows pushes bundle + model to the **offline B300 GPU server**
>   (Ubuntu 24.04, **apptainer already installed**) over SSH.
> - The B300 runs `setup.sh` fully offline; it packs the model into a
>   `model.sif` and brings up the systemd units.

Nothing is built on Windows. Windows only downloads the model and ferries
files. No WSL2, no Docker, no apt on any offline machine.

---

## 1. Big picture

```
[Linux build host, apptainer+internet]   [Windows PC, internet, NO WSL2]   [B300, 24.04, offline, apptainer installed]
  scripts/build-sif-bundle.sh                  ① keep the SIF bundle
   → containers/vllm.sif                       ② Download-Model.ps1 (HF)
   → containers/loopcoder-suite.sif            ③ bundled cwRsync ──────────► /models  (bundle)
   → containers/loopcoder-sandbox.sif              transfers bundle+model    /scratch/models/<id> (unpacked)
   → source/LoopCoder/  (setup.sh + helpers)   ④ ssh → setup.sh ───────────► setup.sh (offline):
   → win-tools/  (cwRsync, so Windows                                         - pack model dir → model.sif
     can rsync without WSL2)                                                  - install SIFs to /opt/apptainers
   → manifest.sha256                                                          - systemd: vllm + loopcoder
  → back up this directory to Windows ───────►
```

Why SIF-only: the B300 already has apptainer, so there are **no apt
`.deb`** and **no Python wheels** to ship — everything the target runs
lives inside three self-contained SIFs. SIFs carry their own root
filesystem, so the build host's OS version is irrelevant.

---

## 2. Build the bundle (once, on a Linux box with apptainer + internet)

```bash
cd LoopCoder
bash scripts/build-sif-bundle.sh            # default output: output/sif-bundle/
# or choose a location:
bash scripts/build-sif-bundle.sh --output /data/loopcoder-bundle
```

This produces:

```
<output>/
  containers/vllm.sif                 (~7 GB)
  containers/loopcoder-suite.sif      (agent + API + MCP)
  containers/loopcoder-sandbox.sif    (tool sandbox)
  source/LoopCoder/                   (setup.sh + all helpers)
  win-tools/                          (cwRsync for the Windows step)
  manifest.sha256
```

No VM. No WSL2. `apptainer` pulls base images directly from the registry
(no Docker daemon needed). Back this directory up to the Windows PC by
any means (USB, SMB, scp).

---

## 3. Prerequisites on the Windows PC

| | Why |
|---|---|
| **Python 3.10+** on PATH | catalog selector + HF model download |
| **OpenSSH client** | ships with Win10+; verify `ssh -V` |
| **The pre-built bundle** | from step 2 (`-BundleDir`) |
| **Disk space** | depends on model; B300 default (480B-FP8): ~600 GB |

**No WSL2. No rsync install.** The bundle ships cwRsync in `win-tools/`.

Set up passwordless SSH to the B300 first:

```powershell
ssh-copy-id ubuntu@b300            # or paste the key manually
ssh ubuntu@b300 'apptainer --version'   # must succeed without a password
```

---

## 4. The one command (on Windows)

```powershell
cd LoopCoder
.\scripts\windows\Deploy-To-Linux.ps1 `
    -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle `
    -Profile b300x8
```

That walks through:

1. **SSH preflight** — confirms the target is reachable and apptainer
   is present.
2. **Model selection** — reads the catalog from the bundled source
   (`Qwen3-Coder-480B-A35B-Instruct-FP8` for `b300x8`).
3. **HF download** — `Download-Model.ps1` (hf_transfer, resumable) into
   an unpacked directory.
4. **Transfer** — bundled **cwRsync** pushes the SIF bundle → `/models`
   and the unpacked model → `/scratch/models/<id>`. Falls back to `scp`
   only if cwRsync is somehow missing.
5. **Remote install** — `ssh ... sudo bash setup.sh --bundle /models
   --model-src /scratch/models/<id>`. setup.sh packs the model into
   `model.sif`, installs all SIFs under `/opt/apptainers/`, and enables
   the `vllm` + `loopcoder` systemd units. Fully offline.

---

## 5. Common variations

### 5.0 Multi-model (several sizes side by side)

List every model in a deploy config and pass it with `-ConfigYaml`.
Each entry becomes its own `vllm@<key>` instance on its own port; a
plan picks one via `llm.model: <key>` (else `default_model`).

```yaml
# deploy.yaml  (and the same models[] block in install.yaml)
models:
  - key: fast
    id: Qwen/Qwen2.5-Coder-7B-Instruct-AWQ
    port: 8001
  - key: big
    id: Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8
    port: 8002
default_model: fast
```

```powershell
# Windows: downloads every model in models[], ships each to
# /scratch/models/<leaf>, runs setup.sh (one model-<key>.sif +
# vllm@<key> per entry).
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle -ConfigYaml D:\deploy.yaml
```

```bash
# Linux build/operator host: one-touch fetch of all models first.
bash scripts/fetch-models.sh --config deploy.yaml --dest /data/models
```

Per-plan model selection:

```yaml
# plan.yaml
llm:
  model: big        # routes to the vllm@big instance; omit -> default_model
```

### 5.1 Start small (sample first → big model later)

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle `
    -ModelId Qwen/Qwen2.5-Coder-1.5B-Instruct

# Verify the whole stack against the small model, then re-run with the
# big one (the SIF bundle is unchanged, so it re-transfers nothing new):
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle -Profile b300x8
```

### 5.2 Already have the model on disk

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle `
    -ModelDir D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8 `
    -SkipModelDownload
```

### 5.3 Dry run

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle -DryRun
```

Prints every step; transfers nothing.

---

## 6. Idempotency / re-runs

| Step | How |
|---|---|
| HF download | `--resume-download` |
| Transfer | cwRsync `--partial` — only changed files |
| Remote `setup.sh` | per-stage marker files; model.sif rebuild skipped if present |

If something fails partway, rerun the same command.

---

## 7. Upgrading the agent without re-transferring the model

After the first deploy, only the SIF bundle needs re-pushing when
LoopCoder code changes (the big model.sif stays on the B300):

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -BundleDir D:\loopcoder-bundle -SkipModelDownload -ModelDir <existing>
```

Or, the Linux-native fast path on the B300 itself:

```bash
sudo bash /models/source/LoopCoder/scripts/upgrade-suite.sh \
    /models/containers/loopcoder-suite.sif loopcoder-suite.sif
```

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No containers/vllm.sif under <BundleDir>` | Build the bundle first on a Linux host: `scripts/build-sif-bundle.sh` |
| `cwRsync not found in bundle` | Rebuild with cwRsync staged, or accept the slower `scp` fallback |
| `WARNING: 'apptainer' not detected on target` | B300 must have apptainer preinstalled (SIF-only deploy assumes it) |
| `Permission denied (publickey)` | Run `ssh-copy-id user@host` first |
| Catalog says "no models fit" | Pick a smaller profile or pass `-ModelId` |
| Transfer slow (< 10 MB/s) | SSH-over-WAN; cwRsync `--partial` lets you resume — just rerun |
| `config.json missing in <dir>` | The HF download is incomplete; rerun Download-Model.ps1 |

---

## 9. Reusing the bundle on multiple servers

The SIF bundle is self-contained. Build once on Linux, back up to
Windows, then:

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300-1 -BundleDir D:\b -SkipModelDownload -ModelDir D:\m
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300-2 -BundleDir D:\b -SkipModelDownload -ModelDir D:\m
```

Each target only re-transfers what it is missing.
