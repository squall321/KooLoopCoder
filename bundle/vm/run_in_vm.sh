#!/usr/bin/env bash
# Convenience wrapper: ssh into the VM and run a command, propagating exit code.
# Usage: run_in_vm.sh <vm-name> <command...>

set -euo pipefail
VM="${1:?vm name}"; shift
ssh "$VM" "bash -lc $(printf '%q' "$*")"
