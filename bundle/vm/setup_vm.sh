#!/usr/bin/env bash
# Create a Bundle VM. Args:
#   $1 vm name        e.g. loopcoder-bundle-vm
#   $2 disk root dir  e.g. /data/loopcoder-vm
#   $3 output dir     e.g. /data/loopcoder-bundle (mounted as virtiofs `output`)
#   $4 vm user        e.g. loopcoder

set -euo pipefail

VM_NAME="${1:?vm name}"
DISK_ROOT="${2:?disk root}"
OUTPUT_DIR="${3:?output dir}"
VM_USER="${4:-loopcoder}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

mkdir -p "$DISK_ROOT" "$OUTPUT_DIR"

DISK="$DISK_ROOT/${VM_NAME}.qcow2"
SEED_ISO="$DISK_ROOT/${VM_NAME}-seed.iso"

# 1) Download Ubuntu 24.04 cloud image (if not present)
CLOUD_IMG="$DISK_ROOT/ubuntu-24.04-server-cloudimg-amd64.img"
if [[ ! -f "$CLOUD_IMG" ]]; then
    echo "Downloading Ubuntu 24.04 cloud image…"
    curl -fL -o "$CLOUD_IMG" \
        https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
fi

# 2) Make a 50G qcow2 backed by the cloud image
if [[ ! -f "$DISK" ]]; then
    qemu-img create -F qcow2 -b "$CLOUD_IMG" -f qcow2 "$DISK" 50G
fi

# 3) Build cloud-init seed ISO (inject host pubkey)
SSH_KEY="${HOME}/.ssh/id_ed25519"
if [[ ! -f "${SSH_KEY}.pub" ]]; then
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
fi
PUBKEY="$(cat "${SSH_KEY}.pub")"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

sed "s|@PUBKEY@|$PUBKEY|" "$SCRIPT_DIR/cloud-init/user-data" > "$TMPDIR/user-data"
cp "$SCRIPT_DIR/cloud-init/meta-data" "$TMPDIR/meta-data"

if command -v xorriso >/dev/null; then
    xorriso -as mkisofs -output "$SEED_ISO" -volid CIDATA -joliet -rock \
        "$TMPDIR/user-data" "$TMPDIR/meta-data" >/dev/null
else
    genisoimage -output "$SEED_ISO" -volid CIDATA -joliet -rock \
        "$TMPDIR/user-data" "$TMPDIR/meta-data" >/dev/null
fi

# 4) Render libvirt domain XML
DOMAIN_XML="$DISK_ROOT/${VM_NAME}.domain.xml"
sed -e "s#@VM_NAME@#$VM_NAME#g" \
    -e "s#@DISK@#$DISK#g" \
    -e "s#@SEED_ISO@#$SEED_ISO#g" \
    -e "s#@OUTPUT_DIR@#$OUTPUT_DIR#g" \
    -e "s#@SOURCE_DIR@#$SOURCE_DIR#g" \
    "$SCRIPT_DIR/domain.xml.template" > "$DOMAIN_XML"

# 5) Define + start
virsh define "$DOMAIN_XML"

echo "VM '$VM_NAME' defined. First boot will run cloud-init then reboot;"
echo "use start_vm.sh to bring it up and wait for SSH."
