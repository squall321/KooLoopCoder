#!/usr/bin/env bash
# LoopCoder bundle deployer.
#
# Sends a built bundle to a remote SSH host and runs the offline
# install. The remote machine must have:
#   - SSH access for the given user (key-based)
#   - sudo (passwordless or interactive — script will prompt)
#   - 24.04 LTS or compatible
#
# Package install uses `apt-get install -y ./pkg.deb` (NOT dpkg) so
# dependencies are resolved automatically across the staged .debs.
#
# Examples:
#   sudo bash scripts/deploy.sh user@b300
#   sudo bash scripts/deploy.sh user@b300 --bundle /data/loopcoder-bundle --remote-bundle /models
#   sudo bash scripts/deploy.sh user@b300 --apt-only          # just stage + apt install, skip setup.sh
#   sudo bash scripts/deploy.sh user@b300 --setup-only        # bundle already on remote
#   sudo bash scripts/deploy.sh user@b300 --skip-gpu-stages   # for non-B300 targets / Test VMs
#   sudo bash scripts/deploy.sh user@b300 --dry-run           # show what would happen

set -euo pipefail

USER_HOST=""
BUNDLE_DIR=""
REMOTE_BUNDLE="/models"
DRY=0
APT_ONLY=0
SETUP_ONLY=0
SKIP_GPU=0
SSH_OPTS=()
SUDO_REMOTE="sudo"

# ---------- arg parse ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle)        BUNDLE_DIR="$2"; shift 2 ;;
        --remote-bundle) REMOTE_BUNDLE="$2"; shift 2 ;;
        --dry-run)       DRY=1; shift ;;
        --apt-only)      APT_ONLY=1; shift ;;
        --setup-only)    SETUP_ONLY=1; shift ;;
        --skip-gpu-stages) SKIP_GPU=1; shift ;;
        --ssh-opt)       SSH_OPTS+=("$2"); shift 2 ;;
        --no-sudo)       SUDO_REMOTE=""; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --*)
            echo "unknown flag: $1" >&2
            exit 2
            ;;
        *)
            if [[ -z "$USER_HOST" ]]; then
                USER_HOST="$1"
            else
                echo "unexpected arg: $1" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

[[ -n "$USER_HOST" ]] || { echo "usage: $0 user@host [...]" >&2; exit 2; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
[[ -z "$BUNDLE_DIR" ]] && BUNDLE_DIR="$PROJECT_ROOT/output/bundle"
# Fallback to /data/loopcoder-bundle if the project-internal one is empty
if [[ ! -d "$BUNDLE_DIR" || ! -d "$BUNDLE_DIR/apt" ]]; then
    if [[ -d /data/loopcoder-bundle/apt ]]; then
        BUNDLE_DIR="/data/loopcoder-bundle"
    fi
fi

log()  { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
fail() { log "FAIL: $*"; exit 1; }
run_local()  { log "$ $*"; if [[ $DRY -eq 0 ]]; then eval "$@"; fi; }
ssh_run()    { log "ssh $USER_HOST $ $*"; if [[ $DRY -eq 0 ]]; then ssh "${SSH_OPTS[@]}" "$USER_HOST" "$*"; fi; }

# ---------- preflight ----------
log "Local preflight"
[[ -d "$BUNDLE_DIR" ]] || fail "bundle dir not found: $BUNDLE_DIR"
if [[ $SETUP_ONLY -eq 0 ]]; then
    [[ -d "$BUNDLE_DIR/apt" ]]    || fail "$BUNDLE_DIR/apt missing"
    [[ -d "$BUNDLE_DIR/wheels" ]] || fail "$BUNDLE_DIR/wheels missing"
fi
if [[ -f "$BUNDLE_DIR/manifest.sha256" ]]; then
    log "Verifying local manifest…"
    (cd "$BUNDLE_DIR" && sha256sum -c manifest.sha256 --quiet) \
        || fail "local manifest checksum mismatch"
fi

log "Remote preflight ($USER_HOST)"
ssh_run 'lsb_release -d 2>/dev/null || cat /etc/os-release | head -3'
ssh_run 'uname -srvm'
ssh_run 'df -h / | tail -1'

# ---------- rsync ----------
if [[ $SETUP_ONLY -eq 0 ]]; then
    log "rsync bundle  $BUNDLE_DIR/  ->  $USER_HOST:$REMOTE_BUNDLE/"
    ssh_run "${SUDO_REMOTE} mkdir -p $REMOTE_BUNDLE && ${SUDO_REMOTE} chown -R \$USER:\$USER $REMOTE_BUNDLE"
    if [[ $DRY -eq 0 ]]; then
        rsync -a --delete --info=progress2 \
              -e "ssh ${SSH_OPTS[*]}" \
              "$BUNDLE_DIR/" "$USER_HOST:$REMOTE_BUNDLE/"
    else
        echo "[dry-run] rsync -a --delete \"$BUNDLE_DIR/\" \"$USER_HOST:$REMOTE_BUNDLE/\""
    fi
fi

# ---------- apt install ----------
if [[ $SETUP_ONLY -eq 0 ]]; then
    log "Remote apt install (./apt/*.deb)"
    # NB: we use `apt-get install -y --no-install-recommends ./*.deb` so apt
    # solves the dep graph across the staged .debs. dpkg -i would fail because
    # it does not handle missing transitive deps.
    ssh_run "cd $REMOTE_BUNDLE/apt && \
             ${SUDO_REMOTE} apt-get install -y --no-install-recommends ./*.deb </dev/null"
    ssh_run "apptainer --version 2>&1 | head -1"
fi

# ---------- run setup.sh ----------
if [[ $APT_ONLY -eq 0 ]]; then
    log "Remote: bash setup.sh"
    setup_args=()
    [[ $SKIP_GPU -eq 1 ]] && setup_args+=("--skip-gpu-stages")
    ssh_run "${SUDO_REMOTE} bash $REMOTE_BUNDLE/source/LoopCoder/setup.sh --bundle $REMOTE_BUNDLE ${setup_args[*]}"
fi

log "Deploy complete. Try:"
log "  ssh $USER_HOST 'systemctl status vllm loopcoder; loopcoder --version'"
