# =============================================================================
#  LoopCoder — Verify a downloaded model directory (Windows / PowerShell)
# =============================================================================
#
# Standalone verifier. Useful after copying the model to an external SSD or
# different drive. Checks file presence, JSON parsability, total size, and
# (optionally) per-shard SHA256.
#
# USAGE:
#   .\Verify-Model.ps1 -ModelDir D:\loopcoder\models\Qwen3-Coder-480B-A35B-Instruct-FP8
#   .\Verify-Model.ps1 -ModelDir D:\... -ExpectedMinGB 400
#   .\Verify-Model.ps1 -ModelDir D:\... -ComputeHashes -OutHashFile D:\hashes.txt
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ModelDir,
    [int]$ExpectedMinGB = 0,
    [switch]$ComputeHashes,
    [string]$OutHashFile = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ModelDir)) {
    Write-Error "ModelDir does not exist: $ModelDir"
}

Write-Host "Verifying $ModelDir" -ForegroundColor Cyan
Write-Host ""

# --- 1) config.json -------------------------------------------------------

$configPath = Join-Path $ModelDir "config.json"
if (-not (Test-Path $configPath)) {
    Write-Error "config.json missing"
}
try {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    Write-Host "  config.json     OK   (model_type=$($cfg.model_type))"
} catch {
    Write-Error "config.json is invalid JSON: $_"
}

# --- 2) tokenizer ---------------------------------------------------------

if (Test-Path (Join-Path $ModelDir "tokenizer.json")) {
    Write-Host "  tokenizer.json  OK"
} elseif (Test-Path (Join-Path $ModelDir "tokenizer.model")) {
    Write-Host "  tokenizer.model OK"
} else {
    Write-Warning "  no tokenizer file found (may be OK for some models)"
}

# --- 3) safetensors shards ------------------------------------------------

$shards = Get-ChildItem -Path $ModelDir -Filter "*.safetensors" -File -ErrorAction SilentlyContinue
if ($shards.Count -eq 0) {
    Write-Error "no *.safetensors files in $ModelDir"
}
$sum = ($shards | Measure-Object -Property Length -Sum).Sum
$gb  = [math]::Round($sum / 1GB, 2)
Write-Host "  safetensors     $($shards.Count) shards, $gb GB"

if ($ExpectedMinGB -gt 0 -and $gb -lt $ExpectedMinGB) {
    Write-Error "size $gb GB < expected min $ExpectedMinGB GB"
}

# --- 4) shard index consistency ------------------------------------------

$indexPath = Join-Path $ModelDir "model.safetensors.index.json"
if (Test-Path $indexPath) {
    try {
        $idx = Get-Content $indexPath -Raw | ConvertFrom-Json
        $declared = ($idx.weight_map.PSObject.Properties.Value | Sort-Object -Unique).Count
        if ($declared -gt $shards.Count) {
            Write-Warning "  index.json references $declared shard names but only $($shards.Count) present"
        } else {
            Write-Host "  index.json      OK   ($declared shards referenced)"
        }
    } catch {
        Write-Warning "  index.json could not be parsed: $_"
    }
}

# --- 5) optional sha256 ---------------------------------------------------

if ($ComputeHashes) {
    Write-Host ""
    Write-Host "Computing SHA256 (this can take 5-30 minutes for large models)" -ForegroundColor Yellow
    $lines = @()
    $i = 0
    foreach ($s in $shards) {
        $i++
        Write-Progress -Activity "SHA256" -Status "$($s.Name) ($i/$($shards.Count))" `
                       -PercentComplete (($i / $shards.Count) * 100)
        $h = Get-FileHash $s.FullName -Algorithm SHA256
        $lines += "$($h.Hash.ToLower())  $($s.Name)"
    }
    Write-Progress -Activity "SHA256" -Completed
    if ($OutHashFile) {
        $lines | Set-Content -Path $OutHashFile -Encoding ASCII
        Write-Host "  hashes written: $OutHashFile"
    } else {
        $lines | ForEach-Object { Write-Host "  $_" }
    }
}

Write-Host ""
Write-Host "Verification PASS." -ForegroundColor Green
