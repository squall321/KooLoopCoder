#!/usr/bin/env bash
# Inside Bundle VM: build the sandbox Apptainer .sif used for tool execution.
#
# Args:
#   $1 output dir
#   $2 container defs dir

set -euo pipefail
OUT="${1:?output dir}"
DEFS="${2:?defs dir}"
mkdir -p "$OUT"

DEF="$DEFS/loopcoder-sandbox.def"
[[ -f "$DEF" ]] || { echo "missing $DEF"; exit 1; }

OUT_SIF="$OUT/loopcoder-sandbox.sif"
echo "[sandbox-sif] building $OUT_SIF from $DEF"
sudo apptainer build "$OUT_SIF" "$DEF"

sudo apptainer exec "$OUT_SIF" python --version
sudo apptainer exec "$OUT_SIF" pytest --version

ls -lh "$OUT_SIF"
