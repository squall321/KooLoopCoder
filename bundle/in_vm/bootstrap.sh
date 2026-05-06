#!/usr/bin/env bash
# Inside Bundle VM: install collection tooling.
# Idempotent.

set -euo pipefail

sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    apt-rdepends \
    apt-utils \
    python3.12-venv \
    python3.12-dev \
    python3-pip \
    rsync \
    curl \
    ca-certificates \
    git \
    jq \
    docker.io \
    skopeo

# hf_transfer for fast model download
python3 -m pip install --user --quiet huggingface_hub[hf_transfer]

# Apptainer (used to build .sif from docker images)
if ! command -v apptainer >/dev/null; then
    sudo add-apt-repository -y ppa:apptainer/ppa 2>/dev/null || true
    sudo apt-get update -qq
    sudo apt-get install -y apptainer
fi

echo "[bootstrap] OK. apptainer=$(apptainer --version), python=$(python3 --version)"
