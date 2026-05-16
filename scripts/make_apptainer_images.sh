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

# loopcoder-suite.def uses relative %files paths and an embedded wheelhouse,
# so it can't go through the simple build() above. Delegate to the existing
# collector, which handles CWD + wheelhouse staging. WHEELS_DIR is optional;
# if unset/empty the suite %post falls back to PyPI (needs internet).
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"
echo "==> $DEFS/loopcoder-suite.def -> $OUT/loopcoder-suite.sif"
bash "$REPO_ROOT/bundle/in_vm/collect_loopcoder_suite.sh" "$OUT" "$REPO_ROOT"

echo "Images at $OUT"
