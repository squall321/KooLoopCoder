#!/usr/bin/env bash
# One-touch model fetch driven by a deploy.yaml.
#
# Reads the `models:` list from a deploy config, downloads every model
# from HuggingFace into <dest>/<leaf>/, and prints the catalog-resolved
# serving parameters for each (so you can eyeball quant / tp / port).
#
# This runs on a Linux host with internet (build host, or the operator's
# box). It does NOT serve anything — setup.sh brings up the vllm@<key>
# instances from the same deploy.yaml.
#
# Usage:
#   bash scripts/fetch-models.sh --config deploy.yaml [--dest DIR] [--only KEY]
#
# Requires: python3 with huggingface_hub (the repo venv has it), and the
# loopcoder package importable (PYTHONPATH=agent or installed).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"

CONFIG=""
DEST="${MODELS_DEST:-$REPO_ROOT/output/models}"
ONLY=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --dest)   DEST="$2"; shift 2 ;;
        --only)   ONLY="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$CONFIG" ]] || { echo "FAIL: --config deploy.yaml required" >&2; exit 2; }
[[ -f "$CONFIG" ]] || { echo "FAIL: config not found: $CONFIG" >&2; exit 2; }

# Pick a python that can import loopcoder (venv first).
PY="python3"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PY="$REPO_ROOT/.venv/bin/python"
fi
export PYTHONPATH="$REPO_ROOT/agent:${PYTHONPATH:-}"

log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }

# Parse models[] from deploy.yaml: emit "key<TAB>id" lines.
mapfile -t MODEL_LINES < <(
    "$PY" - "$CONFIG" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
for m in (cfg.get("models") or []):
    key = m.get("key"); mid = m.get("id")
    if key and mid:
        print(f"{key}\t{mid}")
PY
)

if [[ ${#MODEL_LINES[@]} -eq 0 ]]; then
    echo "FAIL: no models[] in $CONFIG (multi-model schema required)" >&2
    exit 3
fi

mkdir -p "$DEST"
log "fetching ${#MODEL_LINES[@]} model(s) into $DEST"

rc_any=0
for line in "${MODEL_LINES[@]}"; do
    key="${line%%$'\t'*}"
    mid="${line#*$'\t'}"
    if [[ -n "$ONLY" && "$ONLY" != "$key" ]]; then
        continue
    fi
    leaf="${mid##*/}"
    target="$DEST/$leaf"

    log "[$key] $mid"
    # Serving params from the catalog (informational; setup.sh resolves
    # again at install time via `loopcoder catalog-resolve`).
    "$PY" - "$mid" <<'PY' 2>/dev/null || true
import sys
from loopcoder.catalog import resolve_model
d = resolve_model(sys.argv[1])
print(f"  resolve: quant={d['quantization'] or 'none'} "
      f"tp={d['tensor_parallel_size']} max_len={d['max_model_len']} "
      f"parser={d['tool_call_parser']} known={d['known_in_catalog']}")
PY

    if [[ -f "$target/config.json" ]]; then
        log "  already present: $target (skip)"
        continue
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        log "  [dry-run] would download $mid -> $target"
        continue
    fi

    HF_HUB_ENABLE_HF_TRANSFER=1 "$PY" - "$mid" "$target" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])
print("  downloaded", sys.argv[1])
PY
    if [[ ! -f "$target/config.json" ]]; then
        echo "  FAIL: config.json missing after download: $target" >&2
        rc_any=1
    fi
done

log "done (dest=$DEST)"
exit $rc_any
