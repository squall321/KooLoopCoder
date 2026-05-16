# =============================================================================
#  LoopCoder — Windows-mediated end-to-end deploy
# =============================================================================
#
# Scenario:
#   - Windows PC has the internet
#   - Linux GPU server (B300, 24.04, no internet) is reachable via passwordless
#     SSH from this Windows PC
#   - This script does, in order:
#       1. Pull (or refresh) git clone of LoopCoder
#       2. Pick the largest model the target GPU can hold (via the catalog)
#          or use one the user named
#       3. Download the model from HuggingFace (uses hf_transfer)
#       4. (optional) Build the offline bundle in WSL2 Ubuntu 24.04
#                     OR download a pre-built bundle release
#       5. Transfer bundle + model to the Linux host via SSH
#       6. Run bundle/scripts/deploy.sh on the Linux side
#
# USAGE:
#
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8
#
#   # custom model id (skip catalog):
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
#       -ModelId Qwen/Qwen2.5-Coder-1.5B-Instruct
#
#   # already-downloaded model:
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 `
#       -ModelDir D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8 `
#       -SkipModelDownload
#
#   # only test the path — show plan, don't transfer anything:
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -Profile b300x8 -DryRun
#
# REQUIREMENTS:
#   - Windows 10/11
#   - OpenSSH client (built-in for Win10+; verify with ssh -V)
#   - Python 3.10+ on PATH
#   - SSH key already authorized on $Target (test: ssh $Target 'echo ok')
#   - One of (a) WSL2 + Ubuntu 24.04 to build the bundle locally, OR
#     (b) a pre-built bundle directory passed via -BundleDir
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Target,                  # ssh user@host

    [string]$Profile = "b300x8",      # hardware profile (see config/model-catalog.yaml)
    [string]$ModelId = "",            # override catalog recommendation
    [string]$ModelDir = "",           # use an already-downloaded model dir; skip HF
    [string]$RepoRoot = "",           # default = this script's grandparent
    [string]$WorkDir  = "D:\loopcoder-work",
    [string]$BundleDir = "",          # pre-built bundle; skip WSL build
    [string]$RemoteBundle = "/models",
    [string]$RemoteModelDir = "",     # defaults to /scratch/models/<leaf>
    [string]$HFToken = "",
    [string]$WSLDistro = "Ubuntu-24.04",
    [switch]$SkipModelDownload,
    [switch]$SkipBundleBuild,
    [switch]$SkipTransfer,
    [switch]$SkipRemoteSetup,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Section($msg) { Write-Host ""; Write-Host "==== $msg ====" -ForegroundColor Cyan }
function Write-Step($msg)    { Write-Host "  ->" -NoNewline -ForegroundColor Yellow; Write-Host " $msg" }
function Fail($msg)          { Write-Host "FAIL: $msg" -ForegroundColor Red; exit 1 }

function Run([string]$cmd) {
    Write-Step $cmd
    if (-not $DryRun) {
        Invoke-Expression $cmd
        if ($LASTEXITCODE -ne 0) { throw "command failed: $cmd" }
    }
}

# ----------------- 0. resolve paths -----------------

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
}
if (-not (Test-Path "$RepoRoot\config\model-catalog.yaml")) {
    Fail "Could not find LoopCoder repo at $RepoRoot (no config/model-catalog.yaml). Pass -RepoRoot."
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$ModelsRoot = Join-Path $WorkDir "models"
$BundleStage = Join-Path $WorkDir "bundle"
New-Item -ItemType Directory -Force -Path $ModelsRoot,$BundleStage | Out-Null

# ----------------- 1. SSH connectivity check -----------------

Write-Section "SSH preflight"
Write-Step "ssh $Target 'lsb_release -d 2>/dev/null || cat /etc/os-release | head -3'"
if (-not $DryRun) {
    $probe = ssh -o BatchMode=yes -o ConnectTimeout=10 $Target 'lsb_release -d 2>/dev/null || cat /etc/os-release | head -3'
    if ($LASTEXITCODE -ne 0) { Fail "SSH probe failed. Ensure passwordless key auth to $Target." }
    Write-Host "  remote: $probe"
}

# ----------------- 2. pick model -----------------

Write-Section "Model selection"
if ($ModelId -eq "" -and $ModelDir -eq "") {
    Write-Step "loopcoder select-model $Profile --json"
    $py = "python"
    # Use the agent's own catalog from the source repo (no install needed)
    $env:PYTHONPATH = "$RepoRoot\agent;$env:PYTHONPATH"
    $jsonOut = & $py -m loopcoder.catalog $Profile --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        # Try the CLI 'select-model' via subprocess as a fallback
        $jsonOut = & $py -c "from loopcoder.catalog import recommend_cli; import sys; sys.argv=['x','$Profile','--json']; sys.exit(recommend_cli())" 2>&1
    }
    try {
        $obj = $jsonOut | ConvertFrom-Json
        $ModelId = $obj.models[0].id
        Write-Host "  catalog recommends:" -NoNewline
        Write-Host " $ModelId" -ForegroundColor Green
        Write-Host "    $($obj.models[0].approx_vram_gb) GiB weights, tp_default=$($obj.models[0].tp_default)"
    } catch {
        Fail "couldn't parse catalog output. Pass -ModelId explicitly. raw: $jsonOut"
    }
}

$modelLeaf = if ($ModelDir) { Split-Path -Leaf $ModelDir } else { ($ModelId -split "/")[-1] }
if (-not $RemoteModelDir) {
    $RemoteModelDir = "/scratch/models/$modelLeaf"
}

# ----------------- 3. download model -----------------

if ($ModelDir -eq "" -and -not $SkipModelDownload) {
    $ModelDir = Join-Path $ModelsRoot $modelLeaf
    Write-Section "Model download"
    Write-Step "Download-Model.ps1 -ModelId $ModelId -OutDir $ModelsRoot"
    if (-not $DryRun) {
        $dl = Join-Path $PSScriptRoot "Download-Model.ps1"
        $params = @("-ModelId", $ModelId, "-OutDir", $ModelsRoot)
        if ($HFToken) { $params += "-HFToken"; $params += $HFToken }
        & $dl @params
        if ($LASTEXITCODE -ne 0) { Fail "model download failed" }
    }
}
if ($ModelDir -and -not (Test-Path $ModelDir) -and -not $DryRun) {
    Fail "Model directory not present after step: $ModelDir"
}

# ----------------- 4. build / locate bundle -----------------

if (-not $BundleDir) {
    $BundleDir = $BundleStage
}

if (-not $SkipBundleBuild) {
    Write-Section "Bundle build (WSL2 Ubuntu 24.04)"

    # Detect WSL
    $wslOk = $false
    try {
        $distros = wsl --list --quiet 2>$null
        if ($LASTEXITCODE -eq 0 -and $distros -match $WSLDistro) {
            $wslOk = $true
        }
    } catch { $wslOk = $false }

    if ($wslOk) {
        Write-Step "Running bundle/in_vm/collect_*.sh inside WSL2 $WSLDistro"
        $wslRepo  = (wsl -d $WSLDistro wslpath -a "$RepoRoot").Trim()
        $wslOut   = (wsl -d $WSLDistro wslpath -a "$BundleDir").Trim()
        $wslCmds  = @(
            "set -e",
            "cd '$wslRepo'",
            "mkdir -p '$wslOut/apt' '$wslOut/wheels' '$wslOut/containers' '$wslOut/source'",
            "bash bundle/in_vm/bootstrap.sh || true",
            "bash bundle/in_vm/collect_apt.sh '$wslOut/apt'",
            "bash bundle/in_vm/collect_wheels.sh '$wslOut/wheels' '$wslRepo'",
            "bash bundle/in_vm/collect_sandbox_image.sh '$wslOut/containers' '$wslRepo/containers'",
            "bash bundle/in_vm/collect_loopcoder_suite.sh '$wslOut/containers' '$wslRepo'",
            "bash bundle/in_vm/collect_vllm_image.sh '$wslOut/containers' '$wslRepo/containers'",
            "rsync -a --delete --exclude='.venv' --exclude='.git' --exclude='output' '$wslRepo/' '$wslOut/source/LoopCoder/'",
            "cd '$wslOut' && find apt wheels containers source -type f -print0 | xargs -0 sha256sum > manifest.sha256",
            "echo 'BUNDLE READY at $wslOut'"
        ) -join " && "
        if (-not $DryRun) {
            wsl -d $WSLDistro bash -lc $wslCmds
            if ($LASTEXITCODE -ne 0) { Fail "WSL bundle build failed" }
        }
    } else {
        Write-Host "  WSL2 $WSLDistro not found." -ForegroundColor Yellow
        Write-Host "  Options:" -ForegroundColor Yellow
        Write-Host "    (a) Install WSL2: wsl --install -d Ubuntu-24.04 (reboot, then rerun)"
        Write-Host "    (b) Pass -BundleDir <path> pointing at a pre-built bundle"
        Write-Host "    (c) Pass -SkipBundleBuild and ensure the target already has setup.sh"
        Fail "no way to obtain the bundle on this Windows box"
    }
}

if (-not (Test-Path "$BundleDir\apt") -and -not $DryRun -and -not $SkipBundleBuild) {
    Fail "Bundle directory looks empty: $BundleDir"
}

# ----------------- 5. transfer to Linux -----------------

if (-not $SkipTransfer) {
    Write-Section "Transfer to $Target"
    Write-Step "ssh $Target 'sudo mkdir -p $RemoteBundle && sudo chown -R \$USER:\$USER $RemoteBundle'"
    if (-not $DryRun) {
        ssh $Target "sudo mkdir -p $RemoteBundle && sudo chown -R `$USER:`$USER $RemoteBundle"
        if ($LASTEXITCODE -ne 0) { Fail "remote mkdir failed" }
    }

    # 5a. bundle
    Write-Step "rsync bundle -> $Target:$RemoteBundle"
    if (-not $DryRun) {
        # rsync via WSL or via Git-for-Windows rsync; OpenSSH on Windows
        # doesn't ship rsync, so we try wsl rsync first.
        $useWsl = $wslOk
        if ($useWsl) {
            $wslBundle = (wsl -d $WSLDistro wslpath -a "$BundleDir").Trim()
            wsl -d $WSLDistro rsync -a --info=progress2 -e "ssh -o BatchMode=yes" "$wslBundle/" "${Target}:$RemoteBundle/"
        } else {
            # Fallback: tar over ssh — slower but works without rsync
            Write-Host "  (no WSL rsync; tar-over-ssh fallback)"
            $tarFile = Join-Path $WorkDir "bundle.tar"
            tar -cf "$tarFile" -C "$BundleDir" .
            scp "$tarFile" "${Target}:$RemoteBundle/bundle.tar"
            ssh $Target "cd $RemoteBundle && tar -xf bundle.tar && rm bundle.tar"
        }
        if ($LASTEXITCODE -ne 0) { Fail "bundle transfer failed" }
    }

    # 5b. model
    if ($ModelDir) {
        Write-Step "rsync model -> $Target:$RemoteModelDir"
        if (-not $DryRun) {
            ssh $Target "sudo mkdir -p $RemoteModelDir && sudo chown -R `$USER:`$USER $RemoteModelDir"
            if ($wslOk) {
                $wslModel = (wsl -d $WSLDistro wslpath -a "$ModelDir").Trim()
                wsl -d $WSLDistro rsync -a --info=progress2 -e "ssh -o BatchMode=yes" "$wslModel/" "${Target}:$RemoteModelDir/"
            } else {
                # Per-file scp -r as a portable fallback (slower than rsync)
                scp -r "$ModelDir\*" "${Target}:$RemoteModelDir/"
            }
            if ($LASTEXITCODE -ne 0) { Fail "model transfer failed" }
        }
    }
}

# ----------------- 6. remote install -----------------

if (-not $SkipRemoteSetup) {
    Write-Section "Remote install (sudo bash setup.sh)"
    $remoteCmd = "sudo bash $RemoteBundle/source/LoopCoder/setup.sh --bundle $RemoteBundle --skip-model-stage"
    Write-Step "ssh $Target '$remoteCmd'"
    if (-not $DryRun) {
        ssh -t $Target $remoteCmd
        if ($LASTEXITCODE -ne 0) { Fail "remote setup.sh failed" }
    }
}

# ----------------- 7. verify -----------------

Write-Section "Verify"
Write-Step "ssh $Target 'systemctl status vllm loopcoder --no-pager | head -20'"
if (-not $DryRun) {
    ssh $Target 'systemctl status vllm loopcoder --no-pager 2>&1 | head -40'
}

Write-Section "Done"
Write-Host @"
Linux side (on $Target):
  systemctl status vllm loopcoder
  loopcoder --version
  curl -sf http://127.0.0.1:8765/v1/health

Local IDE (this Windows PC):
  ssh -L 8765:127.0.0.1:8765 $Target
  Open VS Code → Remote-SSH: Connect to Host → $Target
  Install LoopCoder extension on the remote (one-time)
"@ -ForegroundColor Green
