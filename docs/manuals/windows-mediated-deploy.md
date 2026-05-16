# Windows-mediated deploy to an offline Linux GPU server

> Scenario: the **Windows PC has internet**; the **Linux GPU server
> (B300, Ubuntu 24.04) has none**. SSH passwordless from Windows → Linux
> is set up. We use Windows as the bridge to pull source + model from
> the internet, then push to the offline server.

End result: one PowerShell command does everything from git clone
through vLLM running on the GPU server.

---

## 1. Big picture

```
[Windows PC, internet OK]                       [Linux GPU box, no internet]
   ┌─────────────────────────────┐                 ┌──────────────────────────┐
   │ 1. git clone LoopCoder       │                 │                          │
   │ 2. select-model (catalog)   │                 │                          │
   │ 3. HF download (hf_transfer)│                 │                          │
   │ 4. WSL2 builds bundle:      │                 │                          │
   │      apt/, wheels/,         │                 │                          │
   │      vllm.sif, suite.sif    │                 │                          │
   │ 5. ssh / rsync ─────────────┼────────────────►│ /models    (bundle)      │
   │      to target               │                 │ /scratch/models/<id>     │
   │ 6. ssh → bash setup.sh ─────┼────────────────►│ /opt/apptainers/         │
   │                              │                 │ systemd vllm + loopcoder │
   └─────────────────────────────┘                 └──────────────────────────┘
```

The Windows box never installs anything on the Linux server beyond what
the bundle carries — no `apt` from the internet on the target side.

---

## 2. Prerequisites on the Windows PC

| | Why |
|---|---|
| **Python 3.10+** on PATH | catalog selector, HF download |
| **OpenSSH client** | shipped with Win10+; verify `ssh -V` |
| **WSL2 + Ubuntu 24.04** (recommended) | builds the `.deb` / `.sif` bundle inside the target's OS |
| **Disk space** | depends on model. For B300 default (480B-FP8): ~600 GB free |

Set up SSH keys to the Linux target first:

```powershell
# In PowerShell:
ssh-copy-id ubuntu@b300.example.org   # (or generate + paste manually)
ssh ubuntu@b300 'echo ok'             # must succeed without password
```

Install WSL2 (one-time):

```powershell
wsl --install -d Ubuntu-24.04
# reboot when prompted; create your WSL user account on first launch
```

---

## 3. The one command

```powershell
git clone https://github.com/squall321/KooLoopCoder
cd KooLoopCoder

# Recommended model auto-picked from config\model-catalog.yaml for B300x8
# (= Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 today)
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8
```

That walks through:

1. **SSH preflight** — pings the target.
2. **Model selection** — `loopcoder select-model b300x8 --json` →
   `Qwen3-Coder-480B-A35B-Instruct-FP8` (≈480 GB / 1900 GB budget).
3. **HF download** — `Download-Model.ps1` with `hf_transfer`,
   `--local-dir-use-symlinks False`, resume.
4. **Bundle build (WSL2 Ubuntu 24.04)** — runs
   `bundle/in_vm/collect_apt.sh`, `collect_wheels.sh`,
   `collect_sandbox_image.sh`, `collect_loopcoder_suite.sh`,
   `collect_vllm_image.sh` inside the WSL distro. Outputs to
   `D:\loopcoder-work\bundle\`.
5. **Transfer** — `rsync` (via WSL) of bundle → `/models` and
   model → `/scratch/models/<id>` on the target.
6. **Remote install** — `ssh ubuntu@b300 'sudo bash /models/source/LoopCoder/setup.sh ...'`.
7. **Verify** — `systemctl status` on the remote.

---

## 4. Common variations

### 4.1 Start small (sample first → big model later)

```powershell
# First pass: 3 GB Qwen2.5-Coder-1.5B-Instruct
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -ModelId Qwen/Qwen2.5-Coder-1.5B-Instruct

# Verify the whole stack works against the small model.
# Then re-run with the big model:
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8
```

`-SkipBundleBuild` on the second run reuses the bundle from the first.

### 4.2 Already have the model on disk

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
    -ModelDir D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8 `
    -SkipModelDownload
```

### 4.3 Pre-built bundle (no WSL2 available)

If you can't install WSL2:

1. Build the bundle on **any** Ubuntu 24.04 with internet (run
   `bundle.sh` or our `bundle/in_vm/collect_*.sh` chain there).
2. Copy the resulting bundle directory to Windows (or mount via SMB).
3. `.\Deploy-To-Linux.ps1 -Target ubuntu@b300 -BundleDir D:\bundle -SkipBundleBuild`.

### 4.4 What's in the bundle?

After step 4 you have, in `D:\loopcoder-work\bundle\`:

```
apt/                 ← .deb files (apptainer + deps) — 115 files / ~90 MB
wheels/              ← Python wheels for the loopcoder agent — ~40 MB
containers/
  vllm.sif           ← ~7 GB
  loopcoder-suite.sif       ← ~150 MB
  loopcoder-sandbox.sif     ← ~180 MB
source/LoopCoder/    ← exact source tree (so setup.sh & all helpers are on the target)
manifest.sha256      ← integrity check
```

Plus the model directory: `D:\loopcoder-work\models\<model-leaf>\`.

### 4.5 Dry run

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8 -DryRun
```

Prints every step it would take; transfers nothing.

---

## 5. Idempotency / re-runs

Everything is idempotent:

| Step | How |
|---|---|
| HF download | `--resume-download` |
| Bundle build | `apt-get download` and `pip download` skip what's already there |
| Transfer | `rsync` only ships changed files |
| Remote `setup.sh` | per-stage marker files |

If something fails partway, just rerun the same command.

---

## 6. Upgrading the agent without re-transferring the model

After the first deploy, you only need to push the new source +
containers when LoopCoder code changes:

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8 `
    -SkipModelDownload `
    -ModelDir D:\loopcoder-work\models\Qwen3-Coder-480B-A35B-Instruct-FP8
```

Or, the Linux-native faster path:

```bash
# After rsync'ing the new bundle to /models:
sudo bash /models/source/LoopCoder/scripts/upgrade-suite.sh \
    /models/containers/loopcoder-suite.sif loopcoder-suite.sif
```

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `wsl: distribution not found` | `wsl --install -d Ubuntu-24.04` (reboot) |
| `ssh: connect to host b300 port 22: connection refused` | Wrong host / SSH disabled / firewall. Verify with `ssh -v` |
| `Permission denied (publickey)` | Run `ssh-copy-id user@host` first |
| Catalog says "no models fit" | Pick a smaller hardware profile or pass `-ModelId` explicitly |
| `apptainer build` fails inside WSL | Ensure WSL kernel ≥ 5.x; some old WSL kernels don't support overlayfs |
| `apt-get install` fails on the target with "Unable to fetch ..." | Means setup.sh tried online apt. Use `--bundle /models` (the script does this by default) |
| Transfer is slow (< 10 MB/s) | Likely SSH-over-WAN. Try direct rsync to a local rsyncd, or ship a single tar via `scp` (script's fallback path) |

---

## 8. Reusing the bundle on multiple servers

The bundle is self-contained. After building once on Windows:

```powershell
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300-1 -Profile b300x8 -SkipBundleBuild
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300-2 -Profile b300x8 -SkipBundleBuild
.\scripts\windows\Deploy-To-Linux.ps1 -Target ubuntu@b300-3 -Profile b300x8 -SkipBundleBuild
```

Each target only re-rsyncs what's missing on its side.
