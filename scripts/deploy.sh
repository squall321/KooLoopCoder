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
SKIP_MODEL_STAGE=0
SSH_OPTS=()
SUDO_REMOTE="sudo"
CONFIG_FILE=""
MODEL_MODE="none"
MODEL_LOCAL=""
MODEL_REMOTE=""
MODEL_HF_ID=""

# Tiny helper: pull `key.subkey` from a YAML file. Uses python3 + pyyaml.
yaml_get() {
    local key="$1" file="$2"
    python3 - "$key" "$file" <<'PY'
import sys, yaml
key = sys.argv[1].split(".")
with open(sys.argv[2]) as f:
    data = yaml.safe_load(f) or {}
cur = data
for k in key:
    if isinstance(cur, dict) and k in cur:
        cur = cur[k]
    else:
        cur = None
        break
if cur is None:
    sys.exit(0)
if isinstance(cur, list):
    print(" ".join(str(x) for x in cur))
elif isinstance(cur, bool):
    print("true" if cur else "false")
else:
    print(cur)
PY
}

# ---------- arg parse ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)        CONFIG_FILE="$2"; shift 2 ;;
        --bundle)        BUNDLE_DIR="$2"; shift 2 ;;
        --remote-bundle) REMOTE_BUNDLE="$2"; shift 2 ;;
        --dry-run)       DRY=1; shift ;;
        --apt-only)      APT_ONLY=1; shift ;;
        --setup-only)    SETUP_ONLY=1; shift ;;
        --skip-gpu-stages) SKIP_GPU=1; shift ;;
        --skip-model-stage) SKIP_MODEL_STAGE=1; shift ;;
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

# ---------- YAML config (overrides defaults; CLI flags win over YAML) ----------
if [[ -n "$CONFIG_FILE" ]]; then
    [[ -f "$CONFIG_FILE" ]] || { echo "config not found: $CONFIG_FILE" >&2; exit 2; }
    [[ -z "$USER_HOST" ]]    && USER_HOST="$(yaml_get target.host "$CONFIG_FILE")"
    rb="$(yaml_get target.remote_bundle "$CONFIG_FILE")"
    [[ -n "$rb" && "$REMOTE_BUNDLE" == "/models" ]] && REMOTE_BUNDLE="$rb"
    sd="$(yaml_get target.sudo_remote "$CONFIG_FILE")"
    [[ -n "$sd" ]] && SUDO_REMOTE="$sd"
    bd="$(yaml_get bundle.local_dir "$CONFIG_FILE")"
    [[ -n "$bd" && -z "$BUNDLE_DIR" ]] && BUNDLE_DIR="$bd"
    sgs="$(yaml_get flags.skip_gpu_stages "$CONFIG_FILE")"
    [[ "$sgs" == "true" ]] && SKIP_GPU=1
    sms="$(yaml_get flags.skip_model_stage "$CONFIG_FILE")"
    [[ "$sms" == "true" ]] && SKIP_MODEL_STAGE=1
    MODEL_MODE="$(yaml_get model.mode "$CONFIG_FILE")"
    MODEL_LOCAL="$(yaml_get model.local_path "$CONFIG_FILE")"
    MODEL_REMOTE="$(yaml_get model.remote_path "$CONFIG_FILE")"
    MODEL_HF_ID="$(yaml_get model.hf_id "$CONFIG_FILE")"
    # ssh_opts (space-separated)
    for o in $(yaml_get target.ssh_opts "$CONFIG_FILE"); do
        SSH_OPTS+=("$o")
    done
fi

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

# ---------- apt install (via a local file:// apt repo on the remote) ----------
if [[ $SETUP_ONLY -eq 0 ]]; then
    log "Remote apt install via local repo at $REMOTE_BUNDLE/apt"
    # Register the bundle's apt/ dir as a temporary file:// source, then
    # call standard `apt-get install -y apptainer …`. NOT `dpkg -i`, NOT
    # `apt install ./*.deb` — so the operator gets familiar apt list /
    # apt-mark hold / apt remove behavior afterwards. Packages.gz must
    # exist in apt/ (build-sif-bundle.sh creates it); if missing, we
    # regenerate it on the fly via dpkg-scanpackages.
    ssh_run "if [[ ! -f $REMOTE_BUNDLE/apt/Packages.gz ]]; then \
               ${SUDO_REMOTE} apt-get install -y dpkg-dev </dev/null; \
               ( cd $REMOTE_BUNDLE/apt && \
                 ${SUDO_REMOTE} bash -c 'dpkg-scanpackages -m . /dev/null 2>/dev/null | gzip -9c > Packages.gz' ); \
             fi"
    ssh_run "echo 'deb [trusted=yes] file://$REMOTE_BUNDLE/apt ./' \
               | ${SUDO_REMOTE} tee /etc/apt/sources.list.d/loopcoder-local.list >/dev/null"
    ssh_run "${SUDO_REMOTE} apt-get \
               -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/loopcoder-local.list \
               -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 update </dev/null"
    ssh_run "${SUDO_REMOTE} DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
               apptainer python3.12 python3.12-venv python3.12-dev python3-pip \
               rsync curl ca-certificates jq tmux git </dev/null"
    ssh_run "${SUDO_REMOTE} rm -f /etc/apt/sources.list.d/loopcoder-local.list"
    ssh_run "apptainer --version 2>&1 | head -1"
fi

# ---------- model staging (per YAML config) ----------
if [[ $APT_ONLY -eq 0 && $SKIP_MODEL_STAGE -eq 0 && "${MODEL_MODE:-none}" != "none" ]]; then
    case "$MODEL_MODE" in
        rsync)
            [[ -d "$MODEL_LOCAL" ]] || fail "model.local_path not found: $MODEL_LOCAL"
            [[ -n "$MODEL_REMOTE" ]] || fail "model.remote_path is required for rsync mode"
            log "rsync model  $MODEL_LOCAL/  ->  $USER_HOST:$MODEL_REMOTE/"
            ssh_run "${SUDO_REMOTE} mkdir -p $MODEL_REMOTE && ${SUDO_REMOTE} chown -R \$USER:\$USER $MODEL_REMOTE"
            if [[ $DRY -eq 0 ]]; then
                rsync -a --info=progress2 \
                      -e "ssh ${SSH_OPTS[*]}" \
                      "$MODEL_LOCAL/" "$USER_HOST:$MODEL_REMOTE/"
            else
                echo "[dry-run] rsync -a \"$MODEL_LOCAL/\" \"$USER_HOST:$MODEL_REMOTE/\""
            fi
            ;;
        hf)
            [[ -n "$MODEL_HF_ID" ]] || fail "model.hf_id is required for hf mode"
            [[ -n "$MODEL_REMOTE" ]] || fail "model.remote_path is required for hf mode"
            log "hf download on remote: $MODEL_HF_ID -> $MODEL_REMOTE"
            ssh_run "${SUDO_REMOTE} mkdir -p $MODEL_REMOTE && ${SUDO_REMOTE} chown -R \$USER:\$USER $MODEL_REMOTE"
            ssh_run "HF_HUB_ENABLE_HF_TRANSFER=1 python3 -m pip install --user --break-system-packages --quiet 'huggingface_hub' hf_transfer || true"
            ssh_run "HF_HUB_ENABLE_HF_TRANSFER=1 \$HOME/.local/bin/huggingface-cli download '$MODEL_HF_ID' --local-dir '$MODEL_REMOTE' --local-dir-use-symlinks False --resume-download"
            ;;
        *)
            fail "unknown model.mode: $MODEL_MODE (expected none|rsync|hf)"
            ;;
    esac
fi

# ---------- run setup.sh ----------
if [[ $APT_ONLY -eq 0 ]]; then
    log "Remote: bash setup.sh"
    setup_args=()
    [[ $SKIP_GPU -eq 1 ]] && setup_args+=("--skip-gpu-stages")
    [[ $SKIP_MODEL_STAGE -eq 1 ]] && setup_args+=("--skip-model-stage")
    # When we just rsync'd / HF-downloaded the model above, point
    # setup.sh at it so stage 7 packs it into model.sif. (We do NOT
    # skip stage 7 — that would leave systemd with no model.sif.)
    if [[ "${MODEL_MODE:-none}" != "none" && -n "$MODEL_REMOTE" ]]; then
        setup_args+=("--model-src" "$MODEL_REMOTE")
    fi
    ssh_run "${SUDO_REMOTE} bash $REMOTE_BUNDLE/source/LoopCoder/setup.sh --bundle $REMOTE_BUNDLE ${setup_args[*]}"
fi

log "Deploy complete. Try:"
log "  ssh $USER_HOST 'systemctl status vllm loopcoder; loopcoder --version'"
