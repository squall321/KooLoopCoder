#!/usr/bin/env bash
# Standalone helper to (re)build both Apptainer .sif images outside of the
# bundle pipeline. Useful for iterating on container definitions.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
DEFS="$SCRIPT_DIR/../containers"
OUT="${1:-/tmp/loopcoder-sif}"
mkdir -p "$OUT"

build() {
    local def="$1" target="$OUT/$2"
    echo "==> $def -> $target"
    sudo apptainer build "$target" "$def"
    echo "  $(ls -lh "$target" | awk '{print $5, $9}')"
}

build "$DEFS/vllm.def" vllm.sif
build "$DEFS/loopcoder-sandbox.def" loopcoder-sandbox.sif

echo "Images at $OUT"
