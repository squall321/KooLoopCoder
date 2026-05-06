#!/usr/bin/env bash
# Tear down a VM and its disk.
# Args: $1 vm-name  $2 disk-root

set -euo pipefail
VM="${1:?vm name}"
DISK_ROOT="${2:?disk root}"

if virsh dominfo "$VM" >/dev/null 2>&1; then
    virsh destroy "$VM" 2>/dev/null || true
    virsh undefine "$VM" --remove-all-storage 2>/dev/null || \
        virsh undefine "$VM" 2>/dev/null || true
fi

rm -f "$DISK_ROOT/${VM}.qcow2" "$DISK_ROOT/${VM}-seed.iso" \
      "$DISK_ROOT/${VM}.domain.xml" "$DISK_ROOT/${VM}.known_hosts" || true

echo "VM '$VM' destroyed and disks removed."
