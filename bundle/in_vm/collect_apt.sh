#!/usr/bin/env bash
# Inside Bundle VM (Ubuntu 24.04): download all .deb packages we need
# (recursively) so the offline B300 setup.sh can `apt-get install -y` them.
#
# Args: $1 = output directory (e.g. /output/apt)

set -euo pipefail
OUT="${1:?output dir}"
mkdir -p "$OUT"
cd "$OUT"

# Top-level packages required on the B300 node.
PACKAGES=(
    apptainer
    python3.12
    python3.12-venv
    python3.12-dev
    python3-pip
    rsync
    curl
    ca-certificates
    jq
    htop
    nvtop
    tmux
    git
)

echo "[apt] resolving dependency closure for ${PACKAGES[*]}"
mapfile -t ALL < <(
    for p in "${PACKAGES[@]}"; do
        apt-rdepends "$p" 2>/dev/null \
            | grep -v '^ ' \
            | grep -v '^$'
    done | sort -u
)

echo "[apt] dependencies: ${#ALL[@]} packages"
# Allow apt-get download to fail on virtual packages (they're unmirrorable).
for p in "${ALL[@]}"; do
    if ! apt-get download "$p" 2>/dev/null; then
        echo "  skipped (virtual or unavailable): $p"
    fi
done

ls *.deb >/dev/null 2>&1 || { echo "[apt] no .deb files collected"; exit 1; }
echo "[apt] $(ls -1 *.deb | wc -l) .deb files in $OUT"
