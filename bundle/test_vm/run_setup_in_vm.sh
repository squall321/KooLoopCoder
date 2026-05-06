#!/usr/bin/env bash
# Inside Test VM: run setup.sh in --skip-gpu-stages mode and capture exit code.
# This script runs ON THE HOST and ssh's in. Args: $1 vm-name

set -euo pipefail
VM="${1:?vm}"

# Place loopcoder configs from the bundle's source tree.
ssh "$VM" 'sudo mkdir -p /etc/loopcoder && sudo cp /models/source/LoopCoder/config/install.yaml.example /etc/loopcoder/install.yaml && sudo cp /models/source/LoopCoder/config/vllm.yaml.example /etc/loopcoder/vllm.yaml && sudo cp /models/source/LoopCoder/config/loopcoder.yaml.example /etc/loopcoder/loopcoder.yaml'

# Adjust install.yaml's bundle paths to match the Test VM mounts:
#   /models/... source paths → use what's actually present
ssh "$VM" 'sudo sed -i "s#/models/Qwen3-Coder-480B-A35B-Instruct-FP8#/models/models/Qwen3-Coder-480B-A35B-Instruct-FP8#" /etc/loopcoder/install.yaml || true'
ssh "$VM" 'sudo sed -i "s#/models/containers/vllm.sif#/models/containers/vllm.sif#" /etc/loopcoder/install.yaml || true'

# Run setup.sh from the bundled source, with skip-gpu-stages.
echo "Running setup.sh inside Test VM…"
ssh "$VM" 'sudo bash /models/source/LoopCoder/setup.sh --bundle /models --skip-gpu-stages --skip-model-stage'
echo "setup.sh inside Test VM completed."
