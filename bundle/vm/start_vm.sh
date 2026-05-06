#!/usr/bin/env bash
# Start a Bundle/Test VM and wait for SSH. Configures ~/.ssh/config so the
# rest of the pipeline can simply `ssh <vm-name>`.
#
# Args:
#   $1 vm name
#   $2 disk root (for ssh known_hosts isolation)
#   $3 vm user (default loopcoder)

set -euo pipefail

VM_NAME="${1:?vm name}"
DISK_ROOT="${2:?disk root}"
VM_USER="${3:-loopcoder}"

# 1) Start (idempotent)
state="$(virsh domstate "$VM_NAME" 2>/dev/null || echo undefined)"
case "$state" in
    "running") echo "VM '$VM_NAME' already running." ;;
    "shut off"|"shutoff") virsh start "$VM_NAME" ;;
    "paused") virsh resume "$VM_NAME" ;;
    *) virsh start "$VM_NAME" || true ;;
esac

# 2) Get IP via DHCP lease (libvirt's default network leases)
get_ip() {
    virsh -q domifaddr "$VM_NAME" --source agent 2>/dev/null \
        | awk '/ipv4/ && $4 ~ /^192\.168\./ {split($4,a,"/"); print a[1]; exit}' \
        || true
    if [[ -z "${IP:-}" ]]; then
        virsh -q domifaddr "$VM_NAME" 2>/dev/null \
            | awk '$3 == "ipv4" {split($4,a,"/"); print a[1]; exit}'
    fi
}

echo "Waiting for VM IP and SSH (up to 5 minutes)…"
SSH_KEY="${HOME}/.ssh/id_ed25519"
KNOWN_HOSTS="$DISK_ROOT/${VM_NAME}.known_hosts"
: > "$KNOWN_HOSTS"

IP=""
for i in $(seq 1 60); do
    IP="$(get_ip || true)"
    if [[ -n "$IP" ]]; then
        if ssh -i "$SSH_KEY" \
               -o BatchMode=yes \
               -o StrictHostKeyChecking=no \
               -o UserKnownHostsFile="$KNOWN_HOSTS" \
               -o ConnectTimeout=5 \
               "$VM_USER@$IP" 'true' 2>/dev/null; then
            break
        fi
    fi
    sleep 5
done

[[ -n "$IP" ]] || { echo "VM did not get an IP" >&2; exit 1; }
echo "VM '$VM_NAME' reachable at $IP"

# 3) Update ~/.ssh/config with a host alias = $VM_NAME
SSH_CONF="${HOME}/.ssh/config"
mkdir -p "${HOME}/.ssh"
touch "$SSH_CONF"
chmod 600 "$SSH_CONF"

# Remove any prior block for this VM
awk -v vm="$VM_NAME" '
    BEGIN { skip=0 }
    /^Host / { skip = ($2 == vm) }
    !skip { print }
' "$SSH_CONF" > "$SSH_CONF.tmp" && mv "$SSH_CONF.tmp" "$SSH_CONF"

cat <<EOF >> "$SSH_CONF"
Host $VM_NAME
    HostName $IP
    User $VM_USER
    IdentityFile $SSH_KEY
    StrictHostKeyChecking no
    UserKnownHostsFile $KNOWN_HOSTS
    LogLevel ERROR
EOF

# 4) Sanity check from VM side
echo "Verifying Ubuntu version inside VM:"
ssh "$VM_NAME" 'lsb_release -a 2>/dev/null | grep -E "^(Distributor|Description|Release|Codename)"'
