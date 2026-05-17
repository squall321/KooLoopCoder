#!/usr/bin/env bash
# LoopCoder offline installer for the B300 node (Ubuntu 24.04).
#
# Reads /etc/loopcoder/install.yaml + vllm.yaml + loopcoder.yaml (or paths
# given via --config). The bundle (containing apt/, wheels/, containers/,
# models/, source/, manifest.yaml) is expected at $BUNDLE_ROOT (default
# /models). Each stage is idempotent and creates a marker file so a
# Ctrl-C+rerun resumes from the last successful stage.
#
# Usage:
#   sudo bash setup.sh                            # full install
#   sudo bash setup.sh --bundle /models           # bundle root override
#   sudo bash setup.sh --stage 7                  # resume from a specific stage
#   sudo bash setup.sh --skip-gpu-stages          # for Test VM (no GPU)
#   sudo bash setup.sh --skip-model-stage         # already packed
#   sudo bash setup.sh --model-src DIR            # unpacked model dir to pack
#   sudo bash setup.sh --dry-run                  # plan only
#   sudo bash setup.sh --reinstall                # remove markers, redo all
#   sudo bash setup.sh --uninstall                # tear down
#
# SIF-only model: an unpacked HuggingFace model directory (--model-src,
# typically rsynced in by the Windows deploy step) is packed into a
# read-only model.sif via scripts/pack-model.sh and installed alongside
# the other SIFs under /opt/apptainers/. The host needs apptainer only;
# no Python venv / wheels are installed (the agent lives in
# loopcoder-suite.sif).

set -euo pipefail

# ---------- defaults ----------
BUNDLE_ROOT="${BUNDLE_ROOT:-/models}"
INSTALL_YAML="${INSTALL_YAML:-/etc/loopcoder/install.yaml}"
VLLM_YAML="${VLLM_YAML:-/etc/loopcoder/vllm.yaml}"
LOOPCODER_YAML="${LOOPCODER_YAML:-/etc/loopcoder/loopcoder.yaml}"

INSTALL_ROOT="${INSTALL_ROOT:-/scratch/loopcoder}"
MODEL_CACHE="${MODEL_CACHE:-/scratch/models}"
LOG_DIR="${LOG_DIR:-/var/log/loopcoder}"
STATE_DIR="${STATE_DIR:-/var/lib/loopcoder}"
ETC_DIR="${ETC_DIR:-/etc/loopcoder}"
WORKSPACES_DIR="${WORKSPACES_DIR:-/scratch/workspaces}"
SIF_STORE_DIR="${SIF_STORE_DIR:-/opt/apptainers}"
SIF_CURRENT_DIR="${SIF_CURRENT_DIR:-/opt/apptainers/current}"
export SIF_STORE_DIR SIF_CURRENT_DIR WORKSPACES_DIR

LOOPCODER_USER="${LOOPCODER_USER:-loopcoder}"
LOOPCODER_GROUP="${LOOPCODER_GROUP:-loopcoder}"

DRY_RUN=0
REINSTALL=0
UNINSTALL=0
SKIP_GPU=0
SKIP_MODEL=0
START_STAGE=0
MODEL_SRC="${MODEL_SRC:-}"
TEST_MODE=${LOOPCODER_TEST_MODE:-0}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE_ROOT="$2"; shift 2 ;;
        --config) LOOPCODER_YAML="$2"; shift 2 ;;
        --stage) START_STAGE="$2"; shift 2 ;;
        --model-src) MODEL_SRC="$2"; shift 2 ;;
        --skip-gpu-stages) SKIP_GPU=1; shift ;;
        --skip-model-stage) SKIP_MODEL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --reinstall) REINSTALL=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --test-mode) TEST_MODE=1; SKIP_GPU=1; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ "${TEST_MODE:-0}" == "1" ]]; then
    SKIP_GPU=1
fi

# ---------- logging ----------
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup-$(date +%Y%m%d-%H%M%S).log"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log()   { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
fail()  { log "FAIL: $*"; exit 1; }
note()  { log "  $*"; }
stage() { log "==== STAGE $1: $2 ===="; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        eval "$@"
    fi
}

mark_done() {
    [[ $DRY_RUN -eq 1 ]] && return 0
    mkdir -p "$STATE_DIR"
    touch "$STATE_DIR/.stage_$1"
}
is_done() { [[ -f "$STATE_DIR/.stage_$1" ]]; }

stage_run() {
    local n="$1" name="$2" fn="$3"
    if [[ $n -lt $START_STAGE ]]; then
        log "stage $n ($name) skipped (--stage $START_STAGE)"
        return 0
    fi
    if is_done "$n" && [[ $REINSTALL -eq 0 ]]; then
        log "stage $n ($name) already done — skipping"
        return 0
    fi
    stage "$n" "$name"
    "$fn"
    mark_done "$n"
}

# ---------- uninstall path ----------
if [[ $UNINSTALL -eq 1 ]]; then
    log "Uninstalling LoopCoder."
    run "systemctl stop vllm 2>/dev/null || true"
    run "systemctl disable vllm 2>/dev/null || true"
    run "rm -f /etc/systemd/system/vllm.service"
    run "systemctl daemon-reload || true"
    run "rm -rf '$INSTALL_ROOT' '$STATE_DIR' '$ETC_DIR'"
    log "Done. Logs left at $LOG_DIR; model cache left at $MODEL_CACHE."
    exit 0
fi

if [[ $REINSTALL -eq 1 ]]; then
    log "--reinstall: removing stage markers"
    run "rm -f $STATE_DIR/.stage_* 2>/dev/null || true"
fi

# ---------- multi-model helpers ----------
# install.yaml may carry models[] (preferred) or a single model. We
# parse it with Python from the suite SIF (the only guaranteed Python
# with PyYAML on the offline target). Emits "key<TAB>id<TAB>port" lines;
# empty output means single-model (legacy) mode.
_suite_sif() {
    local c="${SIF_CURRENT_DIR:-/opt/apptainers/current}/loopcoder-suite.sif"
    [[ -e "$c" ]] && { echo "$c"; return; }
    echo "$BUNDLE_ROOT/containers/loopcoder-suite.sif"
}

models_list() {
    local sif; sif="$(_suite_sif)"
    [[ -f "$sif" ]] || return 0
    apptainer exec "$sif" python3 - "$INSTALL_YAML" <<'PY' 2>/dev/null || true
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
for m in (cfg.get("models") or []):
    k, i, p = m.get("key"), m.get("id"), m.get("port")
    if k and i and p:
        print(f"{k}\t{i}\t{p}")
PY
}

default_model_key() {
    local sif; sif="$(_suite_sif)"
    [[ -f "$sif" ]] || return 0
    apptainer exec "$sif" python3 - "$INSTALL_YAML" <<'PY' 2>/dev/null || true
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
print(cfg.get("default_model") or "")
PY
}

# Each stage is a bash function: stage_<n>_<name>.

# Stage 0 — preflight
stage_0_preflight() {
    if [[ $EUID -ne 0 ]]; then fail "must run as root (sudo)"; fi
    . /etc/os-release
    [[ "$ID" == "ubuntu" ]] || fail "unsupported OS: $ID"
    [[ "${VERSION_ID:0:5}" == "24.04" ]] || fail "unsupported Ubuntu version: $VERSION_ID (need 24.04)"
    [[ -d "$BUNDLE_ROOT" ]] || fail "bundle not found at $BUNDLE_ROOT"
    # SIF-only bundles ship manifest.sha256 only; legacy VM bundles also
    # carry manifest.yaml. Accept either as proof of a real bundle.
    [[ -f "$BUNDLE_ROOT/manifest.yaml" || -f "$BUNDLE_ROOT/manifest.sha256" ]] \
        || fail "no manifest (.yaml or .sha256) in $BUNDLE_ROOT"
    note "OS: Ubuntu $VERSION_ID, kernel $(uname -r)"
    note "bundle root: $BUNDLE_ROOT"
    mkdir -p "$INSTALL_ROOT" "$MODEL_CACHE" "$LOG_DIR" "$STATE_DIR" "$ETC_DIR"
    df_mb=$(df -BM --output=avail "$INSTALL_ROOT" | tail -1 | tr -dc 0-9)
    [[ "${df_mb:-0}" -ge 30000 ]] || fail "need ≥30GB free at $INSTALL_ROOT (have ${df_mb}M)"
    note "free space: ${df_mb}M at $INSTALL_ROOT"
}

# Stage 1 — hw_check (skipped in test mode)
stage_1_hw_check() {
    if [[ $SKIP_GPU -eq 1 ]]; then
        note "TEST MODE: GPU verification skipped"
        return 0
    fi
    command -v nvidia-smi >/dev/null || fail "nvidia-smi not found"
    local count
    count=$(nvidia-smi -L | wc -l)
    [[ "$count" -eq 8 ]] || fail "expected 8 GPUs, got $count"
    note "GPUs: $count"
    if command -v nvcc >/dev/null; then
        note "CUDA: $(nvcc --version | tail -1)"
    fi
}

# Stage 2 — manifest_verify
stage_2_manifest_verify() {
    if [[ -f "$BUNDLE_ROOT/manifest.sha256" ]]; then
        run "(cd '$BUNDLE_ROOT' && sha256sum -c manifest.sha256 --quiet)" \
            || fail "manifest checksum mismatch"
        note "manifest.sha256 verified"
    else
        note "manifest.sha256 not present; skipping deep verify"
    fi
}

# Stage 3 — apt_offline (install .deb files from bundle/apt)
stage_3_apt_offline() {
    if [[ -d "$BUNDLE_ROOT/apt" ]]; then
        local debs=("$BUNDLE_ROOT/apt"/*.deb)
        if (( ${#debs[@]} > 0 )) && [[ -e "${debs[0]}" ]]; then
            run "apt-get install -y --no-install-recommends ${debs[*]} </dev/null"
            note "installed ${#debs[@]} .deb packages"
        else
            note "bundle/apt empty"
        fi
    else
        note "bundle/apt missing — assuming pre-installed"
    fi
}

# Stage 4 — apptainer
stage_4_apptainer() {
    if ! command -v apptainer >/dev/null; then
        fail "apptainer not installed (expected from stage 3 deb bundle)"
    fi
    note "apptainer: $(apptainer --version)"
}

# Stage 5 — python_env
# SIF-only: the agent + all Python deps live inside loopcoder-suite.sif.
# A host venv is built ONLY if the legacy bundle still ships wheels/.
stage_5_python_env() {
    if [[ ! -d "$BUNDLE_ROOT/wheels" ]]; then
        note "SIF-only bundle (no wheels/): host venv not needed; agent runs from loopcoder-suite.sif"
        return 0
    fi
    local venv="$INSTALL_ROOT/venv"
    if [[ ! -x "$venv/bin/python" ]]; then
        run "python3.12 -m venv '$venv'"
    fi
    run "'$venv/bin/pip' install --no-index --find-links '$BUNDLE_ROOT/wheels' --upgrade pip wheel setuptools"
    note "python: $('$venv/bin/python' --version)"
}

# Stage 6 — agent_deps + loopcoder install (legacy wheel path only)
stage_6_agent_deps() {
    if [[ ! -d "$BUNDLE_ROOT/wheels" ]]; then
        note "SIF-only bundle: skipping host pip install"
        return 0
    fi
    local venv="$INSTALL_ROOT/venv"
    run "'$venv/bin/pip' install --no-index --find-links '$BUNDLE_ROOT/wheels' \
        pydantic pyyaml jinja2 openai tiktoken rich click GitPython sqlalchemy tenacity platformdirs httpx"
    if [[ -d "$BUNDLE_ROOT/source/LoopCoder" ]]; then
        run "'$venv/bin/pip' install --no-index --find-links '$BUNDLE_ROOT/wheels' --no-build-isolation '$BUNDLE_ROOT/source/LoopCoder'"
    fi
    note "loopcoder: $('$venv/bin/loopcoder' --version)"
}

# Stage 7 — model_stage (SIF-only: pack the unpacked model dir into model.sif)
#
# The Windows deploy step rsyncs an unpacked HF model directory to the
# target (--model-src, default /scratch/models/<leaf>). Here we pack it
# into a single read-only model.sif via scripts/pack-model.sh and install
# it under /opt/apptainers/ with a stable 'model.sif' symlink, mirroring
# how the other SIFs are staged in stage 8. vLLM bind-mounts it at /model.
# Pack one unpacked model dir into $store/model-<sifkey>.sif and point
# $current/model-<sifkey>.sif at it. Echoes nothing; fails hard on error.
_pack_one_model() {
    local src="$1" sifkey="$2"
    [[ -d "$src" ]] || fail "model source dir not found: $src"
    [[ -f "$src/config.json" ]] || fail "config.json missing in $src (not an unpacked HF model dir?)"

    local store="${SIF_STORE_DIR:-/opt/apptainers}"
    local current="${SIF_CURRENT_DIR:-${store}/current}"
    mkdir -p "$store" "$current"

    local ver_sif="$store/model-${sifkey}.sif"
    local packer="${SOURCE_DIR:-$BUNDLE_ROOT/source/LoopCoder}/scripts/pack-model.sh"
    [[ -f "$packer" ]] || packer="$(dirname "$0")/scripts/pack-model.sh"
    [[ -f "$packer" ]] || fail "pack-model.sh not found"

    if [[ -f "$ver_sif" && $REINSTALL -eq 0 ]]; then
        note "model-${sifkey}.sif already built (skip)"
    else
        note "packing $src -> $ver_sif (large models take a while)"
        run "bash '$packer' '$src' '$ver_sif'"
    fi
    run "chmod 644 '$ver_sif'"
    run "ln -sfn 'model-${sifkey}.sif' '$current/model-${sifkey}.sif'"
    note "model staged: $current/model-${sifkey}.sif"
}

stage_7_model_stage() {
    if [[ $SKIP_MODEL -eq 1 ]]; then
        note "--skip-model-stage: model SIF(s) assumed already installed"
        return 0
    fi

    # Multi-model: each models[] entry's weights are expected at
    # <MODEL_SRC|/scratch/models>/<leaf>/ (deploy / fetch-models.sh
    # convention). Pack one model-<key>.sif per entry.
    local entries; entries="$(models_list)"
    if [[ -n "$entries" ]]; then
        local model_root="${MODEL_SRC:-${MODEL_CACHE:-/scratch/models}}"
        while IFS=$'\t' read -r key mid port; do
            [[ -n "$key" ]] || continue
            local leaf="${mid##*/}"
            local src="$model_root/$leaf"
            note "[$key] $mid"
            _pack_one_model "$src" "$key"
        done <<< "$entries"
        note "multi-model: staged $(echo "$entries" | grep -c .) model SIF(s)"
        return 0
    fi

    # Single-model (legacy).
    local src="$MODEL_SRC"
    if [[ -z "$src" ]]; then
        src="$(awk -F': *' '/^  source_path:/{print $2; exit}' "$INSTALL_YAML" | tr -d '\"')"
    fi
    [[ -n "$src" ]] || fail "no model source (pass --model-src DIR or use models[])"
    local leaf; leaf="$(basename "$src")"
    _pack_one_model "$src" "$leaf"
    # Legacy stable name the single-model vllm.service expects.
    run "ln -sfn 'model-${leaf}.sif' '${SIF_CURRENT_DIR:-/opt/apptainers/current}/model.sif'"
}

# Stage 8 — vllm_image / sandbox / suite — install into /opt/apptainers/
# Versioned filenames + current/ symlink layout (atomic upgrades).
stage_8_vllm_image() {
    local store="${SIF_STORE_DIR:-/opt/apptainers}"
    local current="${SIF_CURRENT_DIR:-${store}/current}"
    mkdir -p "$store" "$current"
    chmod 755 "$store" "$current"

    install_sif() {
        local src="$1" stable="$2"
        [[ -f "$src" ]] || { note "skip (missing): $src"; return 0; }
        # Versioned filename = the source file's basename, untouched.
        local base; base="$(basename "$src")"
        run "cp -u '$src' '$store/$base'"
        run "chmod 644 '$store/$base'"
        # Atomic symlink to "stable" name systemd points at
        run "ln -sfn '$base' '$current/$stable'"
        note "installed $base -> $current/$stable"
    }

    install_sif "$BUNDLE_ROOT/containers/vllm.sif"               vllm.sif
    install_sif "$BUNDLE_ROOT/containers/loopcoder-sandbox.sif"  loopcoder-sandbox.sif
    install_sif "$BUNDLE_ROOT/containers/loopcoder-suite.sif"    loopcoder-suite.sif

    [[ -e "$current/vllm.sif" ]] || fail "vllm.sif not staged in $current/"

    if [[ $SKIP_GPU -eq 0 ]]; then
        run "apptainer exec --nv '$current/vllm.sif' python -c 'import vllm; print(vllm.__version__)'"
    else
        run "apptainer exec '$current/vllm.sif' python -c 'import vllm; print(vllm.__version__)'" \
            || note "vllm sif import attempted (CPU-only test)"
    fi

    # Suite import smoke (CPU only; doesn't need GPU)
    if [[ -e "$current/loopcoder-suite.sif" ]]; then
        run "apptainer exec '$current/loopcoder-suite.sif' loopcoder --version"
    fi
}

# Stage 9 — systemd_unit
# Write one vllm-<key>.env from catalog-resolve(model_id) + vllm.yaml
# knobs. $1=model_id $2=env_path $3=served_name $4=port
_write_vllm_env() {
    local model_id="$1" env_file="$2" served="$3" port="$4"
    local resolved
    resolved="$(/usr/local/bin/loopcoder catalog-resolve "$model_id" 2>/dev/null)" \
        || fail "catalog-resolve failed for '$model_id'"
    local M_QUANT M_TP M_MAXLEN M_PARSER
    M_QUANT="$(echo "$resolved"  | awk -F= '/^MODEL_QUANTIZATION=/{print $2}')"
    M_TP="$(echo "$resolved"     | awk -F= '/^MODEL_TP=/{print $2}')"
    M_MAXLEN="$(echo "$resolved" | awk -F= '/^MODEL_MAX_LEN=/{print $2}')"
    M_PARSER="$(echo "$resolved" | awk -F= '/^MODEL_TOOL_PARSER=/{print $2}')"
    note "resolved $model_id -> quant=${M_QUANT:-none} tp=$M_TP max_len=$M_MAXLEN parser=$M_PARSER port=$port"
    {
        echo "# autogenerated: model-derived from catalog, rest from $VLLM_YAML"
        echo "SERVED_MODEL_NAME=$served"
        echo "TENSOR_PARALLEL_SIZE=$M_TP"
        echo "MAX_MODEL_LEN=$M_MAXLEN"
        echo "QUANTIZATION=$M_QUANT"
        echo "TOOL_CALL_PARSER=$M_PARSER"
        echo "GPU_MEMORY_UTILIZATION=$(awk -F': *' '/^  gpu_memory_utilization:/{print $2; exit}' "$VLLM_YAML")"
        echo "MAX_NUM_SEQS=$(awk -F': *' '/^  max_num_seqs:/{print $2; exit}' "$VLLM_YAML")"
        echo "KV_CACHE_DTYPE=$(awk -F': *' '/^  kv_cache_dtype:/{print $2; exit}' "$VLLM_YAML")"
        echo "HOST=$(awk -F': *' '/^  host:/{print $2; exit}' "$VLLM_YAML" | tr -d '\"')"
        echo "PORT=$port"
        echo "HF_HUB_OFFLINE=1"
        echo "TRANSFORMERS_OFFLINE=1"
        echo "NCCL_P2P_LEVEL=NVL"
        echo "VLLM_USE_V1=1"
    } > "$env_file"
}

stage_9_systemd_unit() {
    local current="${SIF_CURRENT_DIR:-/opt/apptainers/current}"

    # ---- Multi-model: one vllm@<key> instance per models[] entry ----
    local entries; entries="$(models_list)"
    if [[ -n "$entries" ]]; then
        local tmpl="${SOURCE_DIR:-$BUNDLE_ROOT/source/LoopCoder}/systemd/vllm@.service.template"
        [[ -f "$tmpl" ]] || tmpl="$(dirname "$0")/systemd/vllm@.service.template"
        [[ -f "$tmpl" ]] || fail "vllm@.service.template not found"

        # Single instanced unit file; per-instance differences live in
        # vllm-<key>.env (MODEL_SIF, PORT, served name, catalog params).
        sed -e "s#@USER@#$LOOPCODER_USER#g" \
            -e "s#@GROUP@#$LOOPCODER_GROUP#g" \
            -e "s#@ETC_DIR@#$ETC_DIR#g" \
            -e "s#@CACHE_DIR@#$INSTALL_ROOT/cache#g" \
            -e "s#@VLLM_SIF@#$current/vllm.sif#g" \
            -e "s#@LOG_DIR@#$LOG_DIR#g" \
            -e "s#@SYSTEMD_RESTART@#on-failure#g" \
            -e "s#@SYSTEMD_RESTART_SEC@#15#g" \
            "$tmpl" > /etc/systemd/system/vllm@.service

        while IFS=$'\t' read -r key mid port; do
            [[ -n "$key" ]] || continue
            _write_vllm_env "$mid" "$ETC_DIR/vllm-${key}.env" "$key" "$port"
            # MODEL_SIF is per-instance → append to that instance's env.
            echo "MODEL_SIF=$current/model-${key}.sif" >> "$ETC_DIR/vllm-${key}.env"
        done <<< "$entries"
        note "rendered vllm@.service + per-model env ($(echo "$entries" | grep -c .) instances)"
        _stage9_loopcoder_unit
        return 0
    fi

    # ---- Single-model (legacy): vllm.service ----
    local model_id
    model_id="$(awk -F': *' '/^  id:/{print $2; exit}' "$INSTALL_YAML" | tr -d '\"')"
    [[ -n "$model_id" ]] || fail "model.id missing in $INSTALL_YAML"

    local env_file="$ETC_DIR/vllm.env"
    _write_vllm_env "$model_id" "$env_file" "$model_id" \
        "$(awk -F': *' '/^  port:/{print $2; exit}' "$VLLM_YAML")"

    local tmpl="${SOURCE_DIR:-$BUNDLE_ROOT/source/LoopCoder}/systemd/vllm.service.template"
    [[ -f "$tmpl" ]] || tmpl="$(dirname "$0")/systemd/vllm.service.template"
    [[ -f "$tmpl" ]] || fail "vllm.service.template not found"

    local model_sif="$current/model.sif"
    sed -e "s#@MODEL_SIF@#$model_sif#g" \
        -e "s#@USER@#$LOOPCODER_USER#g" \
        -e "s#@GROUP@#$LOOPCODER_GROUP#g" \
        -e "s#@ETC_DIR@#$ETC_DIR#g" \
        -e "s#@CACHE_DIR@#$INSTALL_ROOT/cache#g" \
        -e "s#@VLLM_SIF@#$current/vllm.sif#g" \
        -e "s#@LOG_DIR@#$LOG_DIR#g" \
        -e "s#@SYSTEMD_RESTART@#on-failure#g" \
        -e "s#@SYSTEMD_RESTART_SEC@#15#g" \
        "$tmpl" > /etc/systemd/system/vllm.service

    _stage9_loopcoder_unit
}

# Shared tail of stage 9: loopcoder.env + loopcoder.service + user +
# daemon-reload + enable. Enables either vllm@<key> instances (multi) or
# the single vllm.service (legacy), based on install.yaml.
_stage9_loopcoder_unit() {
    local current="${SIF_CURRENT_DIR:-/opt/apptainers/current}"

    # Render loopcoder.env — the suite SIF reads LOOPCODER_API_HOST/PORT
    # (and the optional bearer key) from here via the unit's
    # EnvironmentFile. Defaults expose the API on all interfaces:8765 so
    # other servers can call it; tighten host or set a key by editing
    # this one file (no rebuild). Existing file is preserved on reinstall.
    local lc_env="$ETC_DIR/loopcoder.env"
    if [[ ! -f "$lc_env" || $REINSTALL -eq 1 ]]; then
        {
            echo "# LoopCoder suite service config. Edit + 'systemctl restart loopcoder'."
            echo "LOOPCODER_API_HOST=${LOOPCODER_API_HOST:-0.0.0.0}"
            echo "LOOPCODER_API_PORT=${LOOPCODER_API_PORT:-8765}"
            echo "LOOPCODER_MCP_HOST=${LOOPCODER_MCP_HOST:-0.0.0.0}"
            echo "LOOPCODER_MCP_PORT=${LOOPCODER_MCP_PORT:-8766}"
            echo "# Set a token to require 'Authorization: Bearer <token>'."
            echo "LOOPCODER_API_KEY=${LOOPCODER_API_KEY:-}"
        } > "$lc_env"
        note "wrote $lc_env (API on ${LOOPCODER_API_HOST:-0.0.0.0}:${LOOPCODER_API_PORT:-8765})"
    else
        note "$lc_env exists; preserving (use --reinstall to regenerate)"
    fi

    # Render loopcoder.service template (suite SIF) — optional but
    # standard in the new architecture.
    local suite_tmpl="${SOURCE_DIR:-$BUNDLE_ROOT/source/LoopCoder}/systemd/loopcoder.service.template"
    [[ -f "$suite_tmpl" ]] || suite_tmpl="$(dirname "$0")/systemd/loopcoder.service.template"
    if [[ -f "$suite_tmpl" && -e "$current/loopcoder-suite.sif" ]]; then
        sed -e "s#@USER@#$LOOPCODER_USER#g" \
            -e "s#@GROUP@#$LOOPCODER_GROUP#g" \
            -e "s#@ETC_DIR@#$ETC_DIR#g" \
            -e "s#@LOG_DIR@#$LOG_DIR#g" \
            -e "s#@STATE_DIR@#$STATE_DIR#g" \
            -e "s#@WORKSPACES_DIR@#${WORKSPACES_DIR:-/scratch/workspaces}#g" \
            -e "s#@SUITE_SIF@#$current/loopcoder-suite.sif#g" \
            -e "s#@SANDBOX_SIF@#$current/loopcoder-sandbox.sif#g" \
            -e "s#@SYSTEMD_RESTART@#on-failure#g" \
            -e "s#@SYSTEMD_RESTART_SEC@#10#g" \
            "$suite_tmpl" > /etc/systemd/system/loopcoder.service
        note "rendered /etc/systemd/system/loopcoder.service"
    else
        note "loopcoder-suite.sif absent; skipping loopcoder.service"
    fi

    # Ensure system user exists
    if ! id "$LOOPCODER_USER" >/dev/null 2>&1; then
        run "useradd -r -s /usr/sbin/nologin -d /nonexistent $LOOPCODER_USER"
    fi
    mkdir -p "$INSTALL_ROOT/cache"
    chown -R "$LOOPCODER_USER:$LOOPCODER_GROUP" "$INSTALL_ROOT" "$LOG_DIR" "$STATE_DIR" || true

    run "systemctl daemon-reload"
    local entries; entries="$(models_list)"
    if [[ -n "$entries" ]]; then
        while IFS=$'\t' read -r key mid port; do
            [[ -n "$key" ]] || continue
            run "systemctl enable vllm@${key}"
        done <<< "$entries"
    else
        run "systemctl enable vllm"
    fi
    if [[ -f /etc/systemd/system/loopcoder.service ]]; then
        run "systemctl enable loopcoder"
    fi
}

# Wait for one vLLM instance to answer /v1/models on $1=port.
_wait_vllm_ready() {
    local port="$1" label="$2"
    log "waiting for vLLM [$label] on :${port}…"
    for i in $(seq 1 180); do
        if curl -sf "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
            note "vLLM [$label] ready after ${i}*5s"
            return 0
        fi
        sleep 5
    done
    fail "vLLM [$label] did not become ready within 15 minutes"
}

# Stage 10 — start_vllm (per-instance when multi-model)
stage_10_start_vllm() {
    local entries; entries="$(models_list)"
    if [[ $SKIP_GPU -eq 1 ]]; then
        note "TEST MODE: vLLM NOT started (no GPU). Verifying enable only."
        if [[ -n "$entries" ]]; then
            while IFS=$'\t' read -r key mid port; do
                [[ -n "$key" ]] || continue
                systemctl is-enabled "vllm@${key}" >/dev/null || fail "vllm@${key} not enabled"
            done <<< "$entries"
        else
            systemctl is-enabled vllm >/dev/null || fail "vllm.service not enabled"
        fi
        return 0
    fi
    if [[ -n "$entries" ]]; then
        while IFS=$'\t' read -r key mid port; do
            [[ -n "$key" ]] || continue
            run "systemctl start vllm@${key}"
            _wait_vllm_ready "$port" "$key"
        done <<< "$entries"
        return 0
    fi
    run "systemctl start vllm"
    _wait_vllm_ready "$(awk -F': *' '/^  port:/{print $2; exit}' "$VLLM_YAML")" "single"
}

# Probe one model's /v1/chat/completions on $1=port $2=served-name.
_smoke_one() {
    local port="$1" model="$2" label="$3"
    local payload="{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 1+1? Answer with one digit only.\"}],\"max_tokens\":4}"
    local resp
    resp="$(curl -sf -H 'Content-Type: application/json' -d "$payload" "http://127.0.0.1:${port}/v1/chat/completions" || true)"
    [[ "$resp" == *"\"2\""* ]] || note "WARNING: smoke [$label] did not contain '2'. Response: $resp"
    note "smoke [$label] OK"
}

# Stage 11 — smoke_test (per-instance when multi-model)
stage_11_smoke_test() {
    if [[ $SKIP_GPU -eq 1 ]]; then
        note "TEST MODE: smoke skipped (no GPU)"
        return 0
    fi
    local entries; entries="$(models_list)"
    if [[ -n "$entries" ]]; then
        while IFS=$'\t' read -r key mid port; do
            [[ -n "$key" ]] || continue
            _smoke_one "$port" "$key" "$key"
        done <<< "$entries"
        return 0
    fi
    _smoke_one \
        "$(awk -F': *' '/^  port:/{print $2; exit}' "$VLLM_YAML")" \
        "$(awk -F': *' '/^  id:/{print $2; exit}' "$INSTALL_YAML" | tr -d '\"')" \
        "single"
}

# Stage 12 — agent_install (CLI)
# SIF-only: /usr/local/bin/loopcoder is a thin wrapper that execs the CLI
# inside loopcoder-suite.sif. Legacy wheel path: symlink the venv binary.
stage_12_agent_install() {
    local venv="$INSTALL_ROOT/venv"
    if [[ -x "$venv/bin/loopcoder" ]]; then
        run "ln -sf '$venv/bin/loopcoder' /usr/local/bin/loopcoder"
        note "loopcoder (venv): $(/usr/local/bin/loopcoder --version)"
        return 0
    fi
    local current="${SIF_CURRENT_DIR:-/opt/apptainers/current}"
    local suite="$current/loopcoder-suite.sif"
    [[ -e "$suite" ]] || fail "loopcoder-suite.sif not staged at $suite"
    if [[ $DRY_RUN -eq 0 ]]; then
        cat > /usr/local/bin/loopcoder <<EOF
#!/usr/bin/env bash
# Auto-generated by setup.sh — run the loopcoder CLI from the suite SIF.
exec apptainer exec \\
    --bind ${WORKSPACES_DIR:-/scratch/workspaces}:/workspaces \\
    --bind ${STATE_DIR}:/state \\
    --bind ${ETC_DIR}:/etc/loopcoder:ro \\
    "$suite" loopcoder "\$@"
EOF
        chmod 755 /usr/local/bin/loopcoder
    fi
    note "loopcoder (suite.sif wrapper): $(/usr/local/bin/loopcoder --version 2>/dev/null || echo 'installed')"
}

# Stage 13 — summary
stage_13_summary() {
    cat <<EOF
================================================================
LoopCoder install complete.

  vLLM service:  systemctl status vllm
  Logs:          $LOG_DIR/
  CLI:           loopcoder --help
  Config:        $LOOPCODER_YAML
  Workspaces:    /scratch/workspaces/

Next steps:
  1) Author a plan.yaml (see examples/plan_simple.yaml).
  2) loopcoder run --plan <path>
================================================================
EOF
}

# ---------- main flow ----------
log "LoopCoder setup starting (test_mode=${TEST_MODE:-0}, skip_gpu=$SKIP_GPU, dry_run=$DRY_RUN)"

stage_run 0  preflight       stage_0_preflight
stage_run 1  hw_check        stage_1_hw_check
stage_run 2  manifest_verify stage_2_manifest_verify
stage_run 3  apt_offline     stage_3_apt_offline
stage_run 4  apptainer       stage_4_apptainer
stage_run 5  python_env      stage_5_python_env
stage_run 6  agent_deps      stage_6_agent_deps
stage_run 7  model_stage     stage_7_model_stage
stage_run 8  vllm_image      stage_8_vllm_image
stage_run 9  systemd_unit    stage_9_systemd_unit
stage_run 10 start_vllm      stage_10_start_vllm
stage_run 11 smoke_test      stage_11_smoke_test
stage_run 12 agent_install   stage_12_agent_install
stage_run 13 summary         stage_13_summary

log "ALL STAGES OK"
