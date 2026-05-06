#!/usr/bin/env bash
# Inside Bundle VM: build the vLLM Apptainer .sif from the official Docker image.
#
# Args:
#   $1 = output dir (e.g. /output/containers)
#   $2 = container defs dir (e.g. /home/loopcoder/loopcoder-src/containers)

set -euo pipefail
OUT="${1:?output dir}"
DEFS="${2:?defs dir}"
mkdir -p "$OUT"

DEF="$DEFS/vllm.def"
[[ -f "$DEF" ]] || { echo "missing $DEF"; exit 1; }

OUT_SIF="$OUT/vllm.sif"
echo "[vllm-sif] building $OUT_SIF from $DEF"
sudo apptainer build "$OUT_SIF" "$DEF"

# Smoke test the .sif: must be able to import vllm
sudo apptainer exec "$OUT_SIF" python -c "import vllm; print('[vllm-sif] OK', vllm.__version__)"

# Capture digest of the underlying docker image for manifest
DOCKER_DIGEST=$(grep -E '^From:' "$DEF" | head -1 | awk '{print $2}')
echo "$DOCKER_DIGEST" > "$OUT/vllm.docker_source.txt"

ls -lh "$OUT_SIF"
