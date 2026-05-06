#!/usr/bin/env bash
# Start Test VM and confirm it has NO internet (key safety check).
# Args: $1 vm-name  $2 disk-root  $3 vm-user

set -euo pipefail
VM_NAME="${1:?vm}"
DISK_ROOT="${2:?disk root}"
VM_USER="${3:-loopcoder}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# Reuse general start_vm.sh for boot + ssh wait
bash "$SCRIPT_DIR/../vm/start_vm.sh" "$VM_NAME" "$DISK_ROOT" "$VM_USER"

# Verify offline
echo "Verifying Test VM has NO internet…"
if ssh "$VM_NAME" 'getent hosts huggingface.co >/dev/null 2>&1'; then
    echo "ERROR: Test VM resolved external host — internet not blocked." >&2
    exit 1
fi
if ssh "$VM_NAME" 'curl -sf --max-time 3 https://huggingface.co >/dev/null 2>&1'; then
    echo "ERROR: Test VM reached huggingface.co — internet not blocked." >&2
    exit 1
fi
echo "OK: Test VM has no external connectivity."
