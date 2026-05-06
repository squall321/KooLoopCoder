#!/usr/bin/env bash
# Reverse setup.sh. Stops vllm, removes systemd unit, install root, configs.
# Model cache and logs are retained unless --purge-data is passed.

set -euo pipefail

PURGE_DATA=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-data) PURGE_DATA=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

run() { if [[ $DRY_RUN -eq 1 ]]; then echo "[dry-run] $*"; else eval "$@"; fi; }

[[ $EUID -eq 0 ]] || { echo "must be root" >&2; exit 1; }

run "systemctl stop vllm 2>/dev/null || true"
run "systemctl disable vllm 2>/dev/null || true"
run "rm -f /etc/systemd/system/vllm.service"
run "systemctl daemon-reload"
run "rm -rf /scratch/loopcoder /etc/loopcoder /var/lib/loopcoder"
run "rm -f /usr/local/bin/loopcoder"

if [[ $PURGE_DATA -eq 1 ]]; then
    run "rm -rf /scratch/models /scratch/workspaces /var/log/loopcoder"
    echo "Including model cache, workspaces and logs in cleanup."
fi

echo "Uninstall complete."
