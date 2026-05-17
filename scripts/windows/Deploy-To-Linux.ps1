# =============================================================================
#  LoopCoder — Windows-mediated deploy (SIF-only, NO WSL2)
# =============================================================================
#
# Scenario:
#   - The SIF bundle was already built on a Linux box with apptainer
#     (scripts/build-sif-bundle.sh) and copied to this Windows PC. It
#     contains: containers/*.sif, source/LoopCoder/, win-tools/ (cwRsync),
#     manifest.sha256. NO apt/, NO wheels/ — the B300 already has
#     apptainer and everything else lives inside the SIFs.
#   - This Windows PC has the internet; it downloads the HF model.
#   - The B300 GPU server (Ubuntu 24.04, offline, apptainer installed) is
#     reachable via passwordless SSH from this PC.
#   - Windows has NO WSL2 and NO rsync. We use the cwRsync shipped inside
#     the bundle (win-tools/) for resumable transfers, falling back to
#     the built-in OpenSSH scp if needed.
#
# What this does, in order:
#   1. SSH preflight to the target.
#   2. Pick the model (catalog) or use -ModelId / -ModelDir.
#   3. Download the model from HuggingFace (Download-Model.ps1).
#   4. Transfer the SIF bundle + model to the target (bundled cwRsync).
#   5. Run setup.sh on the target (fully offline; packs the model into a
#      SIF and brings up the systemd units).
#
# It does NOT build anything on Windows. The bundle is pre-built.
#
# USAGE:
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -BundleDir D:\loopcoder-bundle
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -BundleDir D:\b -Profile b300x8
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -BundleDir D:\b `
#       -ModelDir D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8 -SkipModelDownload
#   .\Deploy-To-Linux.ps1 -Target ubuntu@b300 -BundleDir D:\b -DryRun
#
# REQUIREMENTS:
#   - Windows 10/11 with built-in OpenSSH client (verify: ssh -V)
#   - Python 3.10+ on PATH (for the HF download)
#   - SSH key already authorized on $Target (test: ssh $Target 'echo ok')
#   - A pre-built SIF bundle directory (-BundleDir)
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Target,                  # ssh user@host

    [Parameter(Mandatory = $true)]
    [string]$BundleDir,               # pre-built SIF bundle (from build-sif-bundle.sh)

    [string]$Profile = "b300x8",      # hardware profile (config/model-catalog.yaml)
    [string]$ModelId = "",            # override catalog recommendation
    [string]$ModelDir = "",           # already-downloaded model dir; skip HF
    [string]$ConfigYaml = "",         # deploy.yaml with models[] -> multi-model
    [string]$WorkDir  = "D:\loopcoder-work",
    [string]$RemoteBundle = "/models",
    [string]$RemoteModelDir = "",     # defaults to /scratch/models/<leaf>
    [string]$HFToken = "",
    [switch]$SkipModelDownload,
    [switch]$SkipTransfer,
    [switch]$SkipRemoteSetup,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Section($msg) { Write-Host ""; Write-Host "==== $msg ====" -ForegroundColor Cyan }
function Write-Step($msg)    { Write-Host "  ->" -NoNewline -ForegroundColor Yellow; Write-Host " $msg" }
function Fail($msg)          { Write-Host "FAIL: $msg" -ForegroundColor Red; exit 1 }

# ----------------- 0. resolve + validate the pre-built bundle -----------------

if (-not (Test-Path $BundleDir)) { Fail "BundleDir not found: $BundleDir" }
$containers = Join-Path $BundleDir "containers"
if (-not (Test-Path (Join-Path $containers "vllm.sif"))) {
    Fail "No containers/vllm.sif under $BundleDir. Build it first on a Linux host with: scripts/build-sif-bundle.sh"
}
$repoInBundle = Join-Path $BundleDir "source\LoopCoder"
if (-not (Test-Path (Join-Path $repoInBundle "config\model-catalog.yaml"))) {
    Fail "Bundle is missing source/LoopCoder/config/model-catalog.yaml — rebuild the bundle."
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$ModelsRoot = Join-Path $WorkDir "models"
New-Item -ItemType Directory -Force -Path $ModelsRoot | Out-Null

# Locate the shipped cwRsync (no WSL2, no system rsync on Windows).
$RsyncExe = ""
$cw = Get-ChildItem -Path (Join-Path $BundleDir "win-tools") -Recurse -Filter "rsync.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cw) {
    $RsyncExe = $cw.FullName
    Write-Host "  cwRsync: $RsyncExe"
} else {
    Write-Host "  cwRsync not found in bundle; will use scp fallback (no resume)." -ForegroundColor Yellow
}

# ----------------- 1. SSH connectivity check -----------------

Write-Section "SSH preflight"
Write-Step "ssh $Target 'apptainer --version; cat /etc/os-release | head -2'"
if (-not $DryRun) {
    $probe = ssh -o BatchMode=yes -o ConnectTimeout=10 $Target 'apptainer --version 2>/dev/null; . /etc/os-release; echo $PRETTY_NAME'
    if ($LASTEXITCODE -ne 0) { Fail "SSH probe failed. Ensure passwordless key auth to $Target." }
    Write-Host "  remote: $probe"
    if ($probe -notmatch "apptainer") {
        Write-Host "  WARNING: 'apptainer' not detected on target. setup.sh assumes it is installed." -ForegroundColor Yellow
    }
}

# ----------------- 2. pick model(s) -----------------

# Multi-model path: -ConfigYaml deploy.yaml with a models[] list. Each
# entry is downloaded and shipped to /scratch/models/<leaf>; setup.sh
# (reading install.yaml's models[]) packs one model-<key>.sif each and
# brings up vllm@<key>. $MultiModels = list of @{key;id;leaf;dir}.
$MultiModels = @()
if ($ConfigYaml -ne "") {
    if (-not (Test-Path $ConfigYaml)) { Fail "ConfigYaml not found: $ConfigYaml" }
    Write-Section "Multi-model selection ($ConfigYaml)"
    $env:PYTHONPATH = "$repoInBundle\agent;$env:PYTHONPATH"
    $py = @"
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
for m in (cfg.get('models') or []):
    k,i,p = m.get('key'), m.get('id'), m.get('port')
    if k and i: print(f"{k}\t{i}")
"@
    $lines = & python -c $py $ConfigYaml 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $lines) {
        Fail "no models[] in $ConfigYaml (raw: $lines)"
    }
    foreach ($ln in @($lines)) {
        $parts = $ln -split "`t"
        if ($parts.Count -lt 2) { continue }
        $k = $parts[0]; $mid = $parts[1]; $leaf = ($mid -split "/")[-1]
        $MultiModels += @{ key = $k; id = $mid; leaf = $leaf;
                           dir = (Join-Path $ModelsRoot $leaf) }
        Write-Host "  [$k] $mid" -ForegroundColor Green
    }
}

Write-Section "Model selection"
if ($MultiModels.Count -gt 0) {
    Write-Step "multi-model: $($MultiModels.Count) model(s) from $ConfigYaml"
}
elseif ($ModelId -eq "" -and $ModelDir -eq "") {
    Write-Step "loopcoder.catalog $Profile --json (from bundled source)"
    if (-not $DryRun) {
        $env:PYTHONPATH = "$repoInBundle\agent;$env:PYTHONPATH"
        $jsonOut = & python -m loopcoder.catalog $Profile --json 2>&1
        try {
            $obj = $jsonOut | ConvertFrom-Json
            $ModelId = $obj.models[0].id
            Write-Host "  catalog recommends:" -NoNewline
            Write-Host " $ModelId" -ForegroundColor Green
        } catch {
            Fail "couldn't parse catalog output. Pass -ModelId explicitly. raw: $jsonOut"
        }
    }
}

$modelLeaf = if ($ModelDir) { Split-Path -Leaf $ModelDir } else { ($ModelId -split "/")[-1] }
if (-not $RemoteModelDir) {
    $RemoteModelDir = "/scratch/models/$modelLeaf"
}

# ----------------- 3. download model(s) -----------------

if ($MultiModels.Count -gt 0) {
    Write-Section "Model download (multi)"
    $dl = Join-Path $PSScriptRoot "Download-Model.ps1"
    foreach ($m in $MultiModels) {
        if (Test-Path (Join-Path $m.dir "config.json")) {
            Write-Step "[$($m.key)] already present: $($m.dir) (skip)"
            continue
        }
        Write-Step "[$($m.key)] Download-Model.ps1 -ModelId $($m.id)"
        if (-not $DryRun) {
            $params = @("-ModelId", $m.id, "-OutDir", $ModelsRoot)
            if ($HFToken) { $params += "-HFToken"; $params += $HFToken }
            & $dl @params
            if ($LASTEXITCODE -ne 0) { Fail "[$($m.key)] model download failed" }
        }
    }
}
elseif ($ModelDir -eq "" -and -not $SkipModelDownload) {
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

# ----------------- 4. transfer to the target -----------------

# Helper: rsync via cwRsync if present, else scp. cwRsync needs Windows
# paths translated to its Cygwin form (C:\x -> /cygdrive/c/x).
function To-Cygpath([string]$p) {
    $full = (Resolve-Path $p).Path
    $drive = $full.Substring(0,1).ToLower()
    $rest  = $full.Substring(2) -replace '\\','/'
    return "/cygdrive/$drive$rest"
}

function Send-Tree([string]$localDir, [string]$remoteDir, [string]$label) {
    Write-Step "transfer $label -> $Target`:$remoteDir"
    if ($DryRun) { return }
    ssh $Target "sudo mkdir -p '$remoteDir' && sudo chown -R `$USER`:`$USER '$remoteDir'"
    if ($LASTEXITCODE -ne 0) { Fail "remote mkdir failed for $remoteDir" }

    if ($RsyncExe) {
        $src = (To-Cygpath $localDir) + "/"
        & $RsyncExe -a --info=progress2 --partial -e "ssh -o BatchMode=yes" `
            "$src" "$Target`:$remoteDir/"
        if ($LASTEXITCODE -ne 0) { Fail "$label rsync failed" }
    } else {
        # scp fallback: tar the bundle (small-ish), per-file scp the model.
        if ($label -eq "bundle") {
            $tarFile = Join-Path $WorkDir "bundle.tar"
            tar -cf "$tarFile" -C "$localDir" .
            scp "$tarFile" "$Target`:$remoteDir/bundle.tar"
            ssh $Target "cd '$remoteDir' && tar -xf bundle.tar && rm -f bundle.tar"
        } else {
            scp -r "$localDir\*" "$Target`:$remoteDir/"
        }
        if ($LASTEXITCODE -ne 0) { Fail "$label scp failed" }
    }
}

if (-not $SkipTransfer) {
    Write-Section "Transfer to $Target"
    Send-Tree $BundleDir $RemoteBundle "bundle"
    if ($MultiModels.Count -gt 0) {
        foreach ($m in $MultiModels) {
            Send-Tree $m.dir "/scratch/models/$($m.leaf)" "model:$($m.key)"
        }
    }
    elseif ($ModelDir) {
        Send-Tree $ModelDir $RemoteModelDir "model"
    }
}

# ----------------- 5. remote install (fully offline) -----------------

if (-not $SkipRemoteSetup) {
    Write-Section "Remote install (sudo bash setup.sh)"
    # The bundle has no apt/wheels; setup.sh detects that and assumes
    # apptainer is preinstalled. Multi-model: pass the models parent dir;
    # setup.sh reads install.yaml's models[] and packs one SIF per key.
    # Single-model: pass the specific model dir.
    if ($MultiModels.Count -gt 0) {
        $remoteCmd = "sudo bash $RemoteBundle/source/LoopCoder/setup.sh --bundle $RemoteBundle --model-src /scratch/models"
    } else {
        $remoteCmd = "sudo bash $RemoteBundle/source/LoopCoder/setup.sh --bundle $RemoteBundle --model-src $RemoteModelDir"
    }
    Write-Step "ssh -t $Target '$remoteCmd'"
    if (-not $DryRun) {
        ssh -t $Target $remoteCmd
        if ($LASTEXITCODE -ne 0) { Fail "remote setup.sh failed" }
    }
}

# ----------------- 6. verify -----------------

Write-Section "Verify"
Write-Step "ssh $Target 'systemctl status vllm loopcoder --no-pager | head -20'"
if (-not $DryRun) {
    ssh $Target 'systemctl status vllm loopcoder --no-pager 2>&1 | head -40'
}

Write-Section "Done"
Write-Host @"
Linux side (on $Target):
  systemctl status vllm loopcoder
  curl -sf http://127.0.0.1:8765/v1/health

Local IDE (this Windows PC):
  ssh -L 8765:127.0.0.1:8765 $Target
  Open VS Code -> Remote-SSH: Connect to Host -> $Target
"@ -ForegroundColor Green
