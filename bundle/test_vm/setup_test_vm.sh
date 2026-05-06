#!/usr/bin/env bash
# Create a Test VM (Ubuntu 24.04, NO internet, NO GPU).
#
# Args:
#   $1 vm-name       e.g. loopcoder-test-vm
#   $2 disk-root     e.g. /data/loopcoder-vm
#   $3 bundle-dir    bundle output to mount as /models (ro)
#   $4 scratch-dir   host dir to mount as /scratch (rw)
#   $5 vm-user       default loopcoder

set -euo pipefail
VM_NAME="${1:?vm name}"
DISK_ROOT="${2:?disk root}"
BUNDLE_DIR="${3:?bundle dir}"
SCRATCH_DIR="${4:?scratch dir}"
VM_USER="${5:-loopcoder}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

mkdir -p "$DISK_ROOT" "$BUNDLE_DIR" "$SCRATCH_DIR"

DISK="$DISK_ROOT/${VM_NAME}.qcow2"
SEED_ISO="$DISK_ROOT/${VM_NAME}-seed.iso"

# 1) Create isolated libvirt network if missing (no DHCP forwarding)
if ! virsh net-info loopcoder-test-isolated >/dev/null 2>&1; then
    cat <<'XML' | virsh net-define /dev/stdin
<network>
  <name>loopcoder-test-isolated</name>
  <forward mode='none'/>
  <bridge name='lct0' stp='on' delay='0'/>
  <ip address='192.168.231.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.231.2' end='192.168.231.254'/>
    </dhcp>
  </ip>
</network>
XML
    virsh net-autostart loopcoder-test-isolated
    virsh net-start loopcoder-test-isolated
fi

# 2) Reuse the cloud image downloaded by Bundle VM (or download fresh if absent)
CLOUD_IMG="$DISK_ROOT/ubuntu-24.04-server-cloudimg-amd64.img"
if [[ ! -f "$CLOUD_IMG" ]]; then
    curl -fL -o "$CLOUD_IMG" \
        https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
fi

# 3) qcow2 (smaller for test VM — 30G is enough)
if [[ ! -f "$DISK" ]]; then
    qemu-img create -F qcow2 -b "$CLOUD_IMG" -f qcow2 "$DISK" 30G
fi

# 4) Cloud-init seed
SSH_KEY="${HOME}/.ssh/id_ed25519"
[[ -f "${SSH_KEY}.pub" ]] || ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
PUBKEY="$(cat "${SSH_KEY}.pub")"

TMPDIR=$(mktemp -d); trap 'rm -rf "$TMPDIR"' EXIT
sed "s|@PUBKEY@|$PUBKEY|" "$SCRIPT_DIR/cloud-init/user-data" > "$TMPDIR/user-data"
cp "$SCRIPT_DIR/cloud-init/meta-data" "$TMPDIR/meta-data"
if command -v xorriso >/dev/null; then
    xorriso -as mkisofs -output "$SEED_ISO" -volid CIDATA -joliet -rock \
        "$TMPDIR/user-data" "$TMPDIR/meta-data" >/dev/null
else
    genisoimage -output "$SEED_ISO" -volid CIDATA -joliet -rock \
        "$TMPDIR/user-data" "$TMPDIR/meta-data" >/dev/null
fi

# 5) Domain XML
DOMAIN_XML="$DISK_ROOT/${VM_NAME}.domain.xml"
sed -e "s#@VM_NAME@#$VM_NAME#g" \
    -e "s#@DISK@#$DISK#g" \
    -e "s#@SEED_ISO@#$SEED_ISO#g" \
    -e "s#@BUNDLE_DIR@#$BUNDLE_DIR#g" \
    -e "s#@SCRATCH_DIR@#$SCRATCH_DIR#g" \
    "$SCRIPT_DIR/domain.xml.template" > "$DOMAIN_XML"

virsh define "$DOMAIN_XML"
echo "Test VM '$VM_NAME' defined (isolated network, no internet)."
