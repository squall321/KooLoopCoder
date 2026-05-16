#!/usr/bin/env bash
# Build a SIF-only offline bundle on THIS host (no VM, no WSL2).
#
# Why this exists:
#   The build server and the GPU server (B300) are BOTH offline and run
#   the same Ubuntu 24.04. The B300 already has apptainer installed. So
#   we don't ship apt .deb or Python wheels separately — everything the
#   target needs lives inside three self-contained SIFs:
#
#       vllm.sif               vLLM serving engine
#       loopcoder-suite.sif    agent + HTTP API + MCP server
#       loopcoder-sandbox.sif  restricted tool-execution environment
#
#   SIFs carry their own root filesystem, so they are OS-version
#   independent: building here (even on 22.04) produces images that run
#   unchanged on the 24.04 B300. No 24.04 build VM is needed.
#
#   The model is NOT built here. The internet-connected Windows PC
#   downloads it from HuggingFace and ferries it to the B300 alongside
#   this bundle (see scripts/windows/Deploy-To-Linux.ps1).
#
# Requirements on THIS host:
#   - apptainer >= 1.3 (registry pull works without a docker daemon)
#   - internet (to pull base images + Python deps for the suite wheelhouse)
#   - sudo (apptainer build)
#   - python3 + pip (to build the offline wheelhouse for the suite SIF)
#
# Output layout (ready to back up to Windows as-is):
#   <output>/
#     containers/{vllm,loopcoder-suite,loopcoder-sandbox}.sif
#     source/LoopCoder/         exact source tree (setup.sh + helpers)
#     win-tools/                cwRsync (so Windows can rsync without WSL2)
#     manifest.sha256
#
# Usage:
#   bash scripts/build-sif-bundle.sh [--output DIR] [--skip-vllm]
#                                    [--skip-wheels] [--no-win-tools]
#                                    [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"

OUTPUT_ROOT="${OUTPUT_ROOT:-$REPO_ROOT/output/sif-bundle}"
SKIP_VLLM=0
SKIP_WHEELS=0
NO_WIN_TOOLS=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)       OUTPUT_ROOT="$2"; shift 2 ;;
        --skip-vllm)    SKIP_VLLM=1; shift ;;
        --skip-wheels)  SKIP_WHEELS=1; shift ;;
        --no-win-tools) NO_WIN_TOOLS=1; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

log()  { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
fail() { log "FAIL: $*"; exit 1; }
run()  { if [[ $DRY_RUN -eq 1 ]]; then printf '[dry-run] %s\n' "$*"; else eval "$@"; fi; }

# ---------- preflight ----------
log "Preflight: checking host tooling (no VM, no WSL2)"
command -v apptainer >/dev/null 2>&1 || fail "apptainer not found (need >= 1.3)"
command -v python3   >/dev/null 2>&1 || fail "python3 not found"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum not found"
log "  apptainer: $(apptainer --version)"

CONTAINERS="$OUTPUT_ROOT/containers"
SOURCE_OUT="$OUTPUT_ROOT/source/LoopCoder"
WHEELS_OUT="$OUTPUT_ROOT/wheels"
WIN_TOOLS="$OUTPUT_ROOT/win-tools"
run "mkdir -p '$CONTAINERS' '$SOURCE_OUT' '$WHEELS_OUT'"

# ---------- 1. wheelhouse (so suite SIF is fully self-contained) ----------
if [[ $SKIP_WHEELS -eq 0 ]]; then
    log "Building offline wheelhouse for the suite SIF"
    run "bash '$REPO_ROOT/bundle/in_vm/collect_wheels.sh' '$WHEELS_OUT' '$REPO_ROOT'"
else
    log "--skip-wheels: suite SIF %post will fall back to PyPI (needs internet at build time)"
fi

# ---------- 2. SIF images ----------
# collect_loopcoder_suite.sh picks up the wheelhouse from a sibling
# 'wheels' dir of its output arg; our layout already satisfies that
# ($OUTPUT_ROOT/wheels next to $OUTPUT_ROOT/containers).
if [[ $SKIP_VLLM -eq 0 ]]; then
    log "Building vllm.sif (large; ~7 GB)"
    run "bash '$REPO_ROOT/bundle/in_vm/collect_vllm_image.sh' '$CONTAINERS' '$REPO_ROOT/containers'"
else
    log "--skip-vllm: skipping vllm.sif"
fi

log "Building loopcoder-sandbox.sif"
run "bash '$REPO_ROOT/bundle/in_vm/collect_sandbox_image.sh' '$CONTAINERS' '$REPO_ROOT/containers'"

log "Building loopcoder-suite.sif"
run "WHEELS_DIR='$WHEELS_OUT' bash '$REPO_ROOT/bundle/in_vm/collect_loopcoder_suite.sh' '$CONTAINERS' '$REPO_ROOT'"

# ---------- 3. source tree (setup.sh + all helpers travel with the bundle) ----------
log "Copying source tree into bundle"
run "rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'output' --exclude 'examples/workspaces' \
    --exclude '*.sif' \
    '$REPO_ROOT/' '$SOURCE_OUT/'"

# ---------- 4. cwRsync for Windows (no rsync on Windows, no WSL2) ----------
if [[ $NO_WIN_TOOLS -eq 0 ]]; then
    log "Staging cwRsync for the Windows transfer step"
    run "bash '$SCRIPT_DIR/fetch-cwrsync.sh' '$WIN_TOOLS'"
else
    log "--no-win-tools: skipping cwRsync (Windows side must provide its own rsync/scp)"
fi

# ---------- 5. manifest ----------
log "Writing manifest.sha256"
if [[ $DRY_RUN -eq 0 ]]; then
    ( cd "$OUTPUT_ROOT" \
        && find containers source win-tools -type f -print0 2>/dev/null \
        | xargs -0 sha256sum > manifest.sha256 )
    ( cd "$OUTPUT_ROOT" && sha256sum -c manifest.sha256 --quiet ) \
        || fail "manifest self-check failed"
fi

log "Bundle ready: $OUTPUT_ROOT"
log "Next: back this directory up to the Windows PC, then run"
log "  scripts\\windows\\Deploy-To-Linux.ps1 -Target user@b300 -BundleDir <dir>"
log "DONE."
