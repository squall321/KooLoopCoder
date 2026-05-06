#!/usr/bin/env bash
# Tear down Test VM and isolated network.
# Args: $1 vm-name  $2 disk-root

set -euo pipefail
VM="${1:?vm}"
DISK_ROOT="${2:?disk root}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

bash "$SCRIPT_DIR/../vm/destroy_vm.sh" "$VM" "$DISK_ROOT"

# Remove the isolated network if no other domain uses it
if virsh net-info loopcoder-test-isolated >/dev/null 2>&1; then
    virsh net-destroy loopcoder-test-isolated 2>/dev/null || true
    virsh net-undefine loopcoder-test-isolated 2>/dev/null || true
fi
echo "Test VM and isolated network removed."
