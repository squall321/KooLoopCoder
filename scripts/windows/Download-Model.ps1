# =============================================================================
#  LoopCoder — HuggingFace model downloader (Windows / PowerShell)
# =============================================================================
#
# Downloads a HuggingFace model into a target directory using huggingface_hub
# with hf_transfer (5-10x faster). Designed for the LoopCoder offline pipeline:
# the resulting directory drops straight into LoopCoder/output/bundle/models/.
#
# USAGE (PowerShell 5.1+ or PowerShell 7):
#
#   # Default model (full B300 target):
#   .\Download-Model.ps1 -OutDir D:\loopcoder\models
#
#   # Tiny model for dev / testing:
#   .\Download-Model.ps1 -ModelId Qwen/Qwen2.5-Coder-0.5B-Instruct `
#                        -OutDir D:\loopcoder\models
#
#   # Custom location, with HF token (private/gated models):
#   .\Download-Model.ps1 -ModelId meta-llama/Llama-3.3-70B-Instruct `
#                        -OutDir D:\models -HFToken hf_xxx...
#
#   # Validate-only (don't redownload, just check files):
#   .\Download-Model.ps1 -OutDir D:\loopcoder\models -ValidateOnly
#
# Behavior / safety:
#   - --local-dir-use-symlinks=False (NTFS friendly, no admin needed)
#   - Resume after interruption: just rerun the same command
#   - Disables Windows sleep/hibernate while running (restores on exit)
#   - Optionally adds Defender exclusion for the target dir
#   - Validates that config.json exists and total size is sane afterwards
#
# Requirements: Python 3.10+ on PATH.
# =============================================================================

[CmdletBinding()]
param(
    [string]$ModelId    = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
    [Parameter(Mandatory = $true)]
    [string]$OutDir,
    [string]$HFToken    = "",
    [switch]$ValidateOnly,
    [switch]$NoDefenderExclusion,
    [switch]$KeepSleepSettings,
    [int]$ExpectedMinGB = 0    # If set, fail when downloaded size < this
)

$ErrorActionPreference = "Stop"

# ---------- helpers --------------------------------------------------------

function Write-Section($msg) {
    Write-Host ""
    Write-Host "==== $msg ====" -ForegroundColor Cyan
}

function Test-PythonAvailable {
    try {
        $v = & python --version 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        Write-Host "  $v"
        return $true
    } catch {
        return $false
    }
}

function Get-PythonScriptsDir {
    # Where pip --user installs CLI scripts on Windows
    $userBase = & python -c "import site; print(site.USER_BASE)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $userBase) {
        return Join-Path $userBase "Scripts"
    }
    return $null
}

function Disable-Sleep {
    if ($KeepSleepSettings) { return }
    Write-Host "  disabling sleep / hibernate while running"
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change disk-timeout-ac 0    | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
}

function Restore-Sleep {
    if ($KeepSleepSettings) { return }
    Write-Host "  restoring power defaults (30 / 20 / 180)"
    powercfg /change standby-timeout-ac 30  | Out-Null
    powercfg /change disk-timeout-ac 20     | Out-Null
    powercfg /change hibernate-timeout-ac 180 | Out-Null
}

function Add-DefenderExclusion($path) {
    if ($NoDefenderExclusion) { return }
    try {
        Add-MpPreference -ExclusionPath $path -ErrorAction Stop
        Write-Host "  added Defender exclusion: $path"
    } catch {
        Write-Host "  (could not add Defender exclusion — non-admin? skipping)" -ForegroundColor Yellow
    }
}

# ---------- preflight ------------------------------------------------------

Write-Section "Preflight"

if (-not (Test-PythonAvailable)) {
    Write-Error "Python 3.10+ not on PATH. Install via 'winget install Python.Python.3.12' and retry."
}

$scriptsDir = Get-PythonScriptsDir
if ($scriptsDir -and (Test-Path $scriptsDir) -and ($env:PATH -notlike "*$scriptsDir*")) {
    $env:PATH = "$scriptsDir;$env:PATH"
    Write-Host "  added $scriptsDir to PATH for this session"
}

# Resolve OutDir to absolute path
$resolvedOut = Resolve-Path -Path $OutDir -ErrorAction SilentlyContinue
if (-not $resolvedOut) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    $resolvedOut = (Get-Item $OutDir).FullName
}
$OutDir = "$resolvedOut"

$modelLeaf = ($ModelId -split "/")[-1]
$modelDir  = Join-Path $OutDir $modelLeaf
Write-Host "  model:    $ModelId"
Write-Host "  target:   $modelDir"

# Free space check
$drive = (Get-Item $OutDir).PSDrive
$freeGB = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB, 1)
Write-Host "  free space on $($drive.Name):  $freeGB GB"
if ($freeGB -lt 50) {
    Write-Warning "Less than 50 GB free on $($drive.Name): the full Qwen3-Coder-480B model needs ~600 GB."
}

# ---------- validate-only path --------------------------------------------

if ($ValidateOnly) {
    Write-Section "Validate-only"
    if (-not (Test-Path "$modelDir/config.json")) {
        Write-Error "config.json missing under $modelDir"
    }
    $shards = Get-ChildItem "$modelDir/*.safetensors" -ErrorAction SilentlyContinue
    $sumBytes = ($shards | Measure-Object -Property Length -Sum).Sum
    $sumGB = [math]::Round($sumBytes / 1GB, 1)
    Write-Host "  config.json: OK"
    Write-Host "  safetensors shards: $($shards.Count) ($sumGB GB)"
    if ($ExpectedMinGB -gt 0 -and $sumGB -lt $ExpectedMinGB) {
        Write-Error "size $sumGB GB < expected min $ExpectedMinGB GB"
    }
    Write-Host "Validation OK." -ForegroundColor Green
    exit 0
}

# ---------- install hf_hub + hf_transfer ----------------------------------

Write-Section "Installing huggingface_hub + hf_transfer (idempotent)"
& python -m pip install --upgrade --quiet pip 2>&1 | Out-Null
& python -m pip install --quiet huggingface_hub hf_transfer
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed."
}

# ---------- env vars -------------------------------------------------------

$env:HF_HUB_ENABLE_HF_TRANSFER = "1"
$env:HF_HUB_DISABLE_TELEMETRY  = "1"
if ($HFToken -ne "") {
    $env:HF_TOKEN = $HFToken
    Write-Host "  HF token set (length=$($HFToken.Length))"
}

# ---------- protections ---------------------------------------------------

Write-Section "Power & Defender settings"
Disable-Sleep
Add-DefenderExclusion $OutDir
Add-DefenderExclusion (Join-Path $env:USERPROFILE ".cache\huggingface")

# ---------- download -------------------------------------------------------

Write-Section "Downloading"
Write-Host "  This will take a while. Resumes automatically if interrupted."
Write-Host "  Press Ctrl-C to abort; rerun the same command to continue."
Write-Host ""

$dlScript = @"
import os, sys, time
from huggingface_hub import snapshot_download

start = time.time()
path = snapshot_download(
    repo_id=r'$ModelId',
    local_dir=r'$modelDir',
    local_dir_use_symlinks=False,
    resume_download=True,
)
print(f'OK download_path={path}')
print(f'OK elapsed_sec={int(time.time()-start)}')
"@

# Write to a temp file so PowerShell quoting doesn't mangle the python script.
$tmp = New-TemporaryFile
$pyTmp = "$($tmp.FullName).py"
Move-Item $tmp.FullName $pyTmp
Set-Content -Path $pyTmp -Value $dlScript -Encoding UTF8

try {
    & python $pyTmp
    $rc = $LASTEXITCODE
} finally {
    Remove-Item $pyTmp -ErrorAction SilentlyContinue
    Restore-Sleep
}

if ($rc -ne 0) {
    Write-Error "huggingface_hub download failed with exit $rc"
}

# ---------- post-download validation --------------------------------------

Write-Section "Validating"
if (-not (Test-Path "$modelDir/config.json")) {
    Write-Error "config.json missing after download."
}
$shards = Get-ChildItem "$modelDir/*.safetensors" -ErrorAction SilentlyContinue
$sumBytes = ($shards | Measure-Object -Property Length -Sum).Sum
$sumGB = [math]::Round($sumBytes / 1GB, 1)
Write-Host "  config.json:        OK"
Write-Host "  safetensors shards: $($shards.Count)"
Write-Host "  total weight size:  $sumGB GB"

if ($ExpectedMinGB -gt 0 -and $sumGB -lt $ExpectedMinGB) {
    Write-Error "downloaded size $sumGB GB < expected min $ExpectedMinGB GB"
}

Write-Section "Done"
Write-Host "Model is ready at:" -ForegroundColor Green
Write-Host "  $modelDir"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1) Move/copy the directory to your Linux build host:"
Write-Host "       LoopCoder/output/bundle/models/$modelLeaf"
Write-Host "  2) On the Linux host, run:  bash bundle.sh --skip-model"
Write-Host "  3) Ship to B300:            rsync -avP output/bundle/ b300:/models/"
