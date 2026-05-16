#!/usr/bin/env bash
# Stage a standalone Windows rsync (cwRsync) into the bundle.
#
# Why: the Windows PC ferries the bundle + model to the offline B300 but
# Windows has no rsync and no WSL2. cwRsync is a self-contained set of
# .exe + .dll (Cygwin-based) that runs from a folder with no install.
# Shipping it inside the bundle means the operator just unzips and runs.
#
# This script runs on THIS internet-connected build host. The downloaded
# archive is cached under bundle/win-tools-cache/ so re-runs and offline
# rebuilds reuse it.
#
# Usage:
#   bash scripts/fetch-cwrsync.sh <dest_dir>

set -euo pipefail

DEST="${1:?usage: fetch-cwrsync.sh <dest_dir>}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"
CACHE_DIR="$REPO_ROOT/bundle/win-tools-cache"

# cwRsync free edition. Pin a known version; override with CWRSYNC_URL.
CWRSYNC_VER="${CWRSYNC_VER:-6.2.8}"
CWRSYNC_URL="${CWRSYNC_URL:-https://itefix.net/dl/free-software/cwrsync_${CWRSYNC_VER}_x64_free.zip}"
ARCHIVE="$CACHE_DIR/cwrsync_${CWRSYNC_VER}_x64_free.zip"

mkdir -p "$DEST" "$CACHE_DIR"

if [[ ! -f "$ARCHIVE" ]]; then
    echo "[cwrsync] downloading $CWRSYNC_URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fSL --retry 3 -o "$ARCHIVE.tmp" "$CWRSYNC_URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$ARCHIVE.tmp" "$CWRSYNC_URL"
    else
        echo "[cwrsync] FAIL: need curl or wget to download cwRsync" >&2
        echo "[cwrsync] Manually place the zip at: $ARCHIVE" >&2
        exit 1
    fi
    mv "$ARCHIVE.tmp" "$ARCHIVE"
else
    echo "[cwrsync] using cached archive: $ARCHIVE"
fi

echo "[cwrsync] extracting into $DEST"
if command -v unzip >/dev/null 2>&1; then
    rm -rf "$DEST/cwrsync"
    unzip -q -o "$ARCHIVE" -d "$DEST"
else
    echo "[cwrsync] FAIL: 'unzip' not found on build host" >&2
    exit 1
fi

# Record provenance so the manifest + operator know what shipped.
cat > "$DEST/CWRSYNC_VERSION.txt" <<EOF
cwRsync free edition $CWRSYNC_VER (x64)
source: $CWRSYNC_URL
staged: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

# Sanity: the expected binary must be present somewhere in the tree.
if ! find "$DEST" -iname 'rsync.exe' -print -quit | grep -q .; then
    echo "[cwrsync] FAIL: rsync.exe not found after extraction" >&2
    exit 1
fi
echo "[cwrsync] OK: $(find "$DEST" -iname 'rsync.exe' -print -quit)"
