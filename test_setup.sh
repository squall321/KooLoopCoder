#!/usr/bin/env bash
# LoopCoder setup-validator orchestrator.
#
# Spins up a Test VM (Ubuntu 24.04, NO internet, NO GPU), mounts the bundle
# as /models, then runs setup.sh --skip-gpu-stages inside the VM and verifies
# post-conditions. Generates a markdown report at $REPORT.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
VM_NAME="${VM_NAME:-loopcoder-test-vm}"
VM_DISK_ROOT="${VM_DISK_ROOT:-${SCRIPT_DIR}/output/vm-disks}"
BUNDLE_DIR="${BUNDLE_DIR:-${SCRIPT_DIR}/output/bundle}"
SCRATCH_DIR="${SCRATCH_DIR:-${SCRIPT_DIR}/output/test-scratch}"
RESULTS_DIR="${RESULTS_DIR:-${SCRIPT_DIR}/output/test-results}"
KEEP_VM=0
DESTROY_AFTER=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE_DIR="$2"; shift 2 ;;
        --vm-disk-root) VM_DISK_ROOT="$2"; shift 2 ;;
        --vm-name) VM_NAME="$2"; shift 2 ;;
        --scratch) SCRATCH_DIR="$2"; shift 2 ;;
        --keep-vm) KEEP_VM=1; shift ;;
        --destroy-after) DESTROY_AFTER=1; shift ;;
        -h|--help) sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$RESULTS_DIR" "$SCRATCH_DIR"

REPORT="$RESULTS_DIR/test-$(date +%Y%m%d-%H%M%S).md"
log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; tee -a "$REPORT" </dev/null; }

{
    echo "# LoopCoder setup test report"
    echo "- Date: $(date -Iseconds)"
    echo "- VM: $VM_NAME"
    echo "- Bundle: $BUNDLE_DIR"
    echo
} > "$REPORT"

# 1) Preflight
[[ -f "$BUNDLE_DIR/manifest.yaml" ]] || { echo "no manifest.yaml in $BUNDLE_DIR" >&2; exit 1; }

# 2) Create Test VM if missing
if ! virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
    bash "$SCRIPT_DIR/bundle/test_vm/setup_test_vm.sh" \
        "$VM_NAME" "$VM_DISK_ROOT" "$BUNDLE_DIR" "$SCRATCH_DIR"
fi

# 3) Start + verify offline
bash "$SCRIPT_DIR/bundle/test_vm/start_test_vm.sh" \
    "$VM_NAME" "$VM_DISK_ROOT"

# 4) Run setup.sh in VM
status=ok
if ! bash "$SCRIPT_DIR/bundle/test_vm/run_setup_in_vm.sh" "$VM_NAME"; then
    status=fail
fi

# 5) Assert post-conditions
if ! bash "$SCRIPT_DIR/bundle/test_vm/assert_setup_results.sh" "$VM_NAME"; then
    status=fail
fi

{
    echo
    echo "## Result: $status"
} >> "$REPORT"

# 6) Cleanup
if [[ $DESTROY_AFTER -eq 1 ]]; then
    bash "$SCRIPT_DIR/bundle/test_vm/destroy_test_vm.sh" "$VM_NAME" "$VM_DISK_ROOT"
elif [[ $KEEP_VM -eq 0 ]]; then
    virsh shutdown "$VM_NAME" 2>/dev/null || true
fi

if [[ "$status" == "ok" ]]; then
    echo "Report: $REPORT"
    echo "OK"
    exit 0
else
    echo "Report: $REPORT" >&2
    exit 1
fi
