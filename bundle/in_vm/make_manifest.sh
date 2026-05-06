#!/usr/bin/env bash
# Inside Bundle VM: produce manifest.yaml + manifest.sha256 for the bundle.
#
# Args: $1 = bundle root (e.g. /output)

set -euo pipefail
ROOT="${1:?bundle root}"
cd "$ROOT"

UNAME=$(uname -srvm)
APPT_VER=$(apptainer --version 2>/dev/null || echo unknown)
PY_VER=$(python3 --version)
LSB=$(lsb_release -ds 2>/dev/null || echo unknown)
NOW=$(date -Iseconds)

echo "[manifest] indexing files"

# Generate sha256sums for every regular file (skip very large model shards
# only if the user opts out via LOOPCODER_MANIFEST_SKIP_LARGE).
> manifest.sha256
find apt wheels containers source -type f -print0 2>/dev/null \
    | xargs -0 -r sha256sum >> manifest.sha256 || true

# Models are large; record per-file hashes too unless explicitly skipped
if [[ "${LOOPCODER_MANIFEST_SKIP_LARGE:-0}" != "1" ]]; then
    find models -type f -print0 2>/dev/null \
        | xargs -0 -r sha256sum >> manifest.sha256 || true
fi

# manifest.yaml
{
    echo "manifest_version: 1"
    echo "generated_at: \"$NOW\""
    echo "host:"
    echo "  uname: \"$UNAME\""
    echo "  lsb_release: \"$LSB\""
    echo "  apptainer: \"$APPT_VER\""
    echo "  python: \"$PY_VER\""
    echo "components:"
    for d in apt wheels containers models source; do
        if [[ -d "$d" ]]; then
            count=$(find "$d" -type f | wc -l)
            size=$(du -sb "$d" 2>/dev/null | awk '{print $1}')
            echo "  $d:"
            echo "    files: $count"
            echo "    bytes: $size"
        fi
    done
    echo "sha256_count: $(wc -l < manifest.sha256)"
} > manifest.yaml

echo "[manifest] OK"
echo "  manifest.yaml: $(wc -l < manifest.yaml) lines"
echo "  manifest.sha256: $(wc -l < manifest.sha256) entries"

# Self-verify
sha256sum -c manifest.sha256 --quiet \
    && echo "[manifest] checksum self-verify OK" \
    || { echo "[manifest] checksum self-verify FAILED"; exit 1; }
