#!/usr/bin/env bash
# LoopCoder bundle orchestrator (host = Ubuntu 22.04 with virt-manager).
#
# Pipeline:
#   1) Ensure a Bundle VM (Ubuntu 24.04, internet ON) exists; create if missing.
#   2) Start the VM and wait for SSH.
#   3) Inside the VM, run bundle/in_vm/*.sh to collect:
#        apt .deb files, Python wheels, vLLM .sif, sandbox .sif, model weights.
#   4) Compute manifest.yaml + manifest.sha256 on the host.
#   5) Optionally run test_setup.sh to verify the bundle in a Test VM.
#
# Output goes to /data/loopcoder-bundle/ (configurable via --output).

set -euo pipefail

# ---------- config ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# All build outputs default to ${SCRIPT_DIR}/output/. Override with env/flags
# if you need a separate filesystem (e.g. /data on dev hosts).
VM_DISK_ROOT="${VM_DISK_ROOT:-${SCRIPT_DIR}/output/vm-disks}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/output/bundle}"
VM_NAME="${VM_NAME:-loopcoder-bundle-vm}"
VM_USER="${VM_USER:-loopcoder}"
TINY_MODEL=0   # --tiny-model flag
SKIP_VM_CREATE=0
SKIP_MODEL=0
SKIP_CONTAINER=0
SKIP_WHEELS=0
SKIP_APT=0
DESTROY_VM=0
VERIFY_ONLY=0
AND_TEST=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vm-disk-root) VM_DISK_ROOT="$2"; shift 2 ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
        --vm-name) VM_NAME="$2"; shift 2 ;;
        --skip-vm-create) SKIP_VM_CREATE=1; shift ;;
        --skip-model) SKIP_MODEL=1; shift ;;
        --skip-container) SKIP_CONTAINER=1; shift ;;
        --skip-wheels) SKIP_WHEELS=1; shift ;;
        --skip-apt) SKIP_APT=1; shift ;;
        --destroy-vm) DESTROY_VM=1; shift ;;
        --verify-only) VERIFY_ONLY=1; shift ;;
        --and-test) AND_TEST=1; shift ;;
        --tiny-model) TINY_MODEL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

log()  { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
fail() { log "FAIL: $*"; exit 1; }
run()  { if [[ $DRY_RUN -eq 1 ]]; then printf '[dry-run] %s\n' "$*"; else eval "$@"; fi; }

# ---------- preflight (V1) ----------
log "Preflight: checking host tooling"
for cmd in virt-install virsh ssh ssh-keygen rsync sha256sum xorriso genisoimage; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        case "$cmd" in
            xorriso|genisoimage)
                # Either of these can build the cloud-init seed ISO
                continue
                ;;
        esac
        fail "missing host tool: $cmd (install with apt-get)"
    fi
done
if ! command -v xorriso >/dev/null && ! command -v genisoimage >/dev/null; then
    fail "need xorriso OR genisoimage to build cloud-init seed ISO"
fi
log "  virsh: $(virsh --version)"
log "  virt-install: $(virt-install --version 2>&1 | head -1)"

# Verify-only path
if [[ $VERIFY_ONLY -eq 1 ]]; then
    [[ -f "$OUTPUT_ROOT/manifest.yaml" ]] || fail "no manifest in $OUTPUT_ROOT"
    [[ -f "$OUTPUT_ROOT/manifest.sha256" ]] || fail "no manifest.sha256"
    log "Verifying $OUTPUT_ROOT against manifest.sha256"
    (cd "$OUTPUT_ROOT" && sha256sum -c manifest.sha256 --quiet) \
        && log "OK" \
        || fail "checksum mismatch"
    exit 0
fi

# Destroy-only path
if [[ $DESTROY_VM -eq 1 ]]; then
    bash "$SCRIPT_DIR/bundle/vm/destroy_vm.sh" "$VM_NAME" "$VM_DISK_ROOT"
    exit 0
fi

# ---------- prepare host directories ----------
mkdir -p "$VM_DISK_ROOT" "$OUTPUT_ROOT" "$OUTPUT_ROOT"/{apt,wheels,containers,models,source,logs}

# ---------- VM lifecycle (V5/V6) ----------
if [[ $SKIP_VM_CREATE -eq 0 ]]; then
    if virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
        log "VM '$VM_NAME' already defined; skipping create"
    else
        log "Creating Bundle VM '$VM_NAME'"
        run "bash '$SCRIPT_DIR/bundle/vm/setup_vm.sh' '$VM_NAME' '$VM_DISK_ROOT' '$OUTPUT_ROOT' '$VM_USER'"
    fi
fi

log "Starting VM '$VM_NAME' and waiting for SSH"
run "bash '$SCRIPT_DIR/bundle/vm/start_vm.sh' '$VM_NAME' '$VM_DISK_ROOT' '$VM_USER'"

# ---------- copy bundle/in_vm scripts into VM via virtiofs (already mounted) ----------
# The VM has /output and /opt/loopcoder-source mounted via virtiofs (defined in domain.xml.template).
# Copy our in_vm/ scripts into the VM through a 9p/virtiofs path if available; else via scp.
SSH_HOST="$VM_NAME"  # ssh config alias set by start_vm.sh
log "Copying in_vm scripts to VM"
run "rsync -a --delete '$SCRIPT_DIR/bundle/in_vm/' $SSH_HOST:/home/$VM_USER/in_vm/"
run "rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.git' '$SCRIPT_DIR/' $SSH_HOST:/home/$VM_USER/loopcoder-src/"

# ---------- run collectors (B1~B7) ----------
run_remote() {
    local label="$1"; shift
    log "VM> $label"
    run "ssh '$SSH_HOST' 'bash -lc $(printf '%q' "$*")'"
}

run_remote "bootstrap" "bash /home/$VM_USER/in_vm/bootstrap.sh"

if [[ $SKIP_APT -eq 0 ]]; then
    run_remote "collect_apt" "bash /home/$VM_USER/in_vm/collect_apt.sh /output/apt"
fi
if [[ $SKIP_WHEELS -eq 0 ]]; then
    run_remote "collect_wheels" "bash /home/$VM_USER/in_vm/collect_wheels.sh /output/wheels /home/$VM_USER/loopcoder-src"
fi
if [[ $SKIP_CONTAINER -eq 0 ]]; then
    run_remote "collect_vllm_image" "bash /home/$VM_USER/in_vm/collect_vllm_image.sh /output/containers /home/$VM_USER/loopcoder-src/containers"
    run_remote "collect_sandbox_image" "bash /home/$VM_USER/in_vm/collect_sandbox_image.sh /output/containers /home/$VM_USER/loopcoder-src/containers"
fi
if [[ $SKIP_MODEL -eq 0 ]]; then
    if [[ $TINY_MODEL -eq 1 ]]; then
        run_remote "collect_model (tiny)" \
            "LOOPCODER_MODEL_ID=Qwen/Qwen2.5-Coder-0.5B-Instruct bash /home/$VM_USER/in_vm/collect_model.sh /output/models"
    else
        run_remote "collect_model" "bash /home/$VM_USER/in_vm/collect_model.sh /output/models"
    fi
fi

# ---------- copy source tree into bundle ----------
run "rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.git' --exclude 'examples/workspaces' '$SCRIPT_DIR/' '$OUTPUT_ROOT/source/LoopCoder/'"

# ---------- manifest (B7) ----------
run_remote "make_manifest" "bash /home/$VM_USER/in_vm/make_manifest.sh /output"
log "Verifying manifest on host"
(cd "$OUTPUT_ROOT" && sha256sum -c manifest.sha256 --quiet) || fail "manifest mismatch on host"
log "Bundle ready at $OUTPUT_ROOT"

# ---------- optional Test VM run ----------
if [[ $AND_TEST -eq 1 ]]; then
    log "Running test_setup.sh in Test VM"
    bash "$SCRIPT_DIR/test_setup.sh" --bundle "$OUTPUT_ROOT" --vm-disk-root "$VM_DISK_ROOT"
fi

log "DONE."
