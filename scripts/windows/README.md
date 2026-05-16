# LoopCoder — Windows scripts

PowerShell + .bat helpers for the **Windows-mediated deploy workflow**
(SIF-only, **no WSL2**): the Windows PC has internet, the Linux GPU
server (B300) does not. The SIF bundle is built once on a Linux host
(`scripts/build-sif-bundle.sh`) and backed up to Windows. Windows
downloads the HF model and pushes bundle + model to the offline B300
using the cwRsync shipped inside the bundle, then runs setup.sh
remotely. **Nothing is built on Windows.**

End-to-end: see `docs/manuals/windows-mediated-deploy.md`.

| File | Purpose |
|---|---|
| **`Deploy-To-Linux.ps1`** ★ | **One-command deploy of a pre-built SIF bundle.** Picks model from catalog, downloads from HF, transfers bundle+model with the bundled cwRsync (no WSL2), runs setup.sh remotely. Requires `-BundleDir`. |
| `Download-Model.ps1` | Downloads a HuggingFace model with `huggingface_hub` + `hf_transfer`. NTFS-friendly. Resumable. Used internally by Deploy-To-Linux.ps1 or standalone. |
| `Download-Model.bat` | Double-clickable launcher around `Download-Model.ps1`. Modes: default (B300 model), `tiny`, `custom`. |
| `Verify-Model.ps1` | Standalone model directory verifier. Checks `config.json`, tokenizer, shard count/size, optionally SHA256. |

## Quick start

```powershell
# 1) Default: download the full Qwen3-Coder-480B-FP8 (~480 GB)
cd LoopCoder\scripts\windows
.\Download-Model.ps1 -OutDir D:\loopcoder\models

# 2) Tiny model for dev / testing (~1 GB)
.\Download-Model.ps1 -ModelId Qwen/Qwen2.5-Coder-0.5B-Instruct -OutDir D:\loopcoder\models

# 3) Custom (any HF repo, any output)
.\Download-Model.ps1 -ModelId meta-llama/Llama-3.3-70B-Instruct -OutDir E:\models -HFToken hf_xxx

# 4) Validate an existing download (no redownload)
.\Verify-Model.ps1 -ModelDir D:\loopcoder\models\Qwen3-Coder-480B-A35B-Instruct-FP8

# 5) With SHA256 (slower, optional)
.\Verify-Model.ps1 -ModelDir D:\... -ComputeHashes -OutHashFile D:\sha256.txt
```

## Or via the .bat launcher (no PowerShell knowledge needed)

```cmd
Download-Model.bat                           :: full model
Download-Model.bat tiny                      :: tiny model
Download-Model.bat custom Qwen/Qwen2.5-Coder-1.5B-Instruct D:\loopcoder\models
```

## How the model reaches the B300

You normally don't move the model by hand — `Deploy-To-Linux.ps1`
transfers the downloaded directory to the target with the bundled
cwRsync, and `setup.sh` on the B300 packs it into a single read-only
`model.sif`. The standalone `Download-Model.ps1` is only needed when you
want to pre-fetch the model separately (then pass `-ModelDir ... -SkipModelDownload`).

The full end-to-end procedure is in
`LoopCoder/docs/manuals/windows-mediated-deploy.md`.

## What the script protects against

| Concern | Mitigation |
|---|---|
| FAT32 4 GB limit (large shards) | the script does not format anything; you must point `-OutDir` at NTFS or exFAT |
| Symlink permission on NTFS | `local_dir_use_symlinks=False` (always) |
| Slow CDN / throttling | `HF_HUB_ENABLE_HF_TRANSFER=1` (5-10x faster) |
| Sleep mid-download | `powercfg /change standby-timeout-ac 0` while running |
| Defender real-time slowdown | `Add-MpPreference -ExclusionPath` for OutDir + HF cache |
| Half-finished download | `resume_download=True`; just rerun the same command |

## Requirements

- Windows 10/11
- Python 3.10+ on PATH (`winget install Python.Python.3.12`)
- ~600 GB free on the target drive (full B300 model)
- (Optional) Run PowerShell **as Administrator** to allow Defender exclusion.
  Without admin, the script still works but skips that optimization with a warning.

## Troubleshooting

- **`huggingface-cli: command not found`** → the script auto-prepends Python's
  `Scripts` dir to PATH for the session, so this should not happen.
- **`OSError: A required privilege is not held by the client`** → caused by
  HF's symlink mode; the script always passes `--local-dir-use-symlinks False`
  to prevent this.
- **Defender exclusion fails with non-admin** → harmless; download still works,
  just slower while the file is scanned. Add the exclusion manually via
  Settings → Windows Security → Virus & threat protection → Manage settings →
  Add or remove exclusions.
- **Disk full mid-download** → free space then rerun; HF resumes from the last
  fully written file.
- **Download hangs at small percent** → some networks throttle HF CDN. Try a
  VPN, or set `$env:HF_HUB_ENABLE_HF_TRANSFER = "0"` and rerun (slower but
  more compatible).
