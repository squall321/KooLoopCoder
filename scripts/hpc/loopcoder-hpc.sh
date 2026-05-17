#!/usr/bin/env bash
# LoopCoder on an HPC cluster (Slurm) — no sudo, no systemd, no root.
#
# The SIFs are built elsewhere (scripts/build-sif-bundle.sh) and copied
# to the cluster; here we only *run* them via apptainer inside Slurm
# jobs. All state lives under $LOOPCODER_HOME (default ~/.loopcoder or
# $SCRATCH/loopcoder), never /opt /var /etc.
#
# Layout under $LOOPCODER_HOME:
#   sif/        vllm.sif, loopcoder-suite.sif, loopcoder-sandbox.sif
#   models/<leaf>/   unpacked HF model dirs (or model-*.sif)
#   cache/      HF/vLLM cache
#   logs/       per-job logs
#   workspaces/ loopcoder run workspaces
#   state/      loopcoder SQLite + snapshots
#
# Subcommands:
#   init                      create the dir layout, print what to copy
#   submit-allinone <plan>    sbatch: start vLLM, run a plan, exit
#   submit-serve              sbatch: long-lived vLLM serving job
#   run <plan>                run a plan now (inside an salloc/srun shell)
#   resolve <model_id>        print catalog serving params (debug)
#
# Usage:
#   export LOOPCODER_HOME=$SCRATCH/loopcoder
#   bash loopcoder-hpc.sh init
#   bash loopcoder-hpc.sh submit-allinone plan.yaml --model fast
#   bash loopcoder-hpc.sh submit-serve --partition gpu --gpus 8

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." &>/dev/null && pwd)"

# --- state root: env > $SCRATCH > $HOME, never system dirs ---
if [[ -z "${LOOPCODER_HOME:-}" ]]; then
    if [[ -n "${SCRATCH:-}" && -d "${SCRATCH}" ]]; then
        LOOPCODER_HOME="$SCRATCH/loopcoder"
    else
        LOOPCODER_HOME="$HOME/.loopcoder"
    fi
fi
export LOOPCODER_HOME

SIF_DIR="$LOOPCODER_HOME/sif"
MODELS_DIR="$LOOPCODER_HOME/models"
CACHE_DIR="$LOOPCODER_HOME/cache"
LOGS_DIR="$LOOPCODER_HOME/logs"
WORK_DIR="$LOOPCODER_HOME/workspaces"
STATE_DIR="$LOOPCODER_HOME/state"
ETC_DIR="$LOOPCODER_HOME/etc"

VLLM_SIF="${VLLM_SIF:-$SIF_DIR/vllm.sif}"
SUITE_SIF="${SUITE_SIF:-$SIF_DIR/loopcoder-suite.sif}"

# Slurm + serving knobs (overridable via env or flags).
SL_PARTITION="${LOOPCODER_PARTITION:-gpu}"
SL_GPUS="${LOOPCODER_GPUS:-1}"
SL_TIME="${LOOPCODER_TIME:-04:00:00}"
SL_CPUS="${LOOPCODER_CPUS:-8}"
SL_MEM="${LOOPCODER_MEM:-64G}"
VLLM_PORT="${LOOPCODER_VLLM_PORT:-8000}"
MODEL_KEY="${LOOPCODER_MODEL:-}"          # which models[] key (optional)

log()  { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# loopcoder CLI = exec inside the suite SIF (no host install on HPC).
lc() {
    apptainer exec \
        --bind "$WORK_DIR:/workspaces" \
        --bind "$STATE_DIR:/state" \
        --bind "$ETC_DIR:/etc/loopcoder:ro" \
        "$SUITE_SIF" loopcoder "$@"
}

ensure_layout() {
    mkdir -p "$SIF_DIR" "$MODELS_DIR" "$CACHE_DIR" "$LOGS_DIR" \
             "$WORK_DIR" "$STATE_DIR" "$ETC_DIR"
}

preflight() {
    command -v apptainer >/dev/null 2>&1 || fail "apptainer not found on this node"
    [[ -f "$VLLM_SIF" ]]  || fail "missing $VLLM_SIF (build elsewhere, copy here)"
    [[ -f "$SUITE_SIF" ]] || fail "missing $SUITE_SIF"
}

# Resolve a model id -> serving params via the suite SIF's catalog.
# Sets globals: R_QUANT R_TP R_MAXLEN R_PARSER R_LEAF
resolve_model() {
    local mid="$1" out
    out="$(apptainer exec "$SUITE_SIF" loopcoder catalog-resolve "$mid" 2>/dev/null)" \
        || fail "catalog-resolve failed for '$mid'"
    R_QUANT="$(echo "$out"  | awk -F= '/^MODEL_QUANTIZATION=/{print $2}')"
    R_TP="$(echo "$out"     | awk -F= '/^MODEL_TP=/{print $2}')"
    R_MAXLEN="$(echo "$out" | awk -F= '/^MODEL_MAX_LEN=/{print $2}')"
    R_PARSER="$(echo "$out" | awk -F= '/^MODEL_TOOL_PARSER=/{print $2}')"
    R_LEAF="$(echo "$out"   | awk -F= '/^MODEL_LEAF=/{print $2}')"
}

# Pick the model id: explicit arg > $LOOPCODER_MODEL key in install.yaml
# models[] > install.yaml single model.id. Echoes the id.
pick_model_id() {
    local want="${1:-$MODEL_KEY}"
    local iy="$ETC_DIR/install.yaml"
    [[ -f "$iy" ]] || fail "no $ETC_DIR/install.yaml (copy your config there)"
    apptainer exec "$SUITE_SIF" python3 - "$iy" "$want" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
want = sys.argv[2]
models = cfg.get("models") or []
if models:
    if want:
        for m in models:
            if m.get("key") == want:
                print(m["id"]); raise SystemExit
        raise SystemExit(f"model key {want!r} not in models[]")
    dk = cfg.get("default_model")
    for m in models:
        if not dk or m.get("key") == dk:
            print(m["id"]); raise SystemExit
    print(models[0]["id"])
else:
    print((cfg.get("model") or {}).get("id", ""))
PY
}

# Emit the apptainer command that serves vLLM for $1=model_id on
# $2=port, binding model weights from $MODELS_DIR/<leaf>. The model may
# be an unpacked dir or a model-<leaf>.sif (image-src bind).
vllm_run_cmd() {
    local mid="$1" port="$2"
    resolve_model "$mid"
    local leaf="$R_LEAF"
    local mdir="$MODELS_DIR/$leaf" msif="$MODELS_DIR/model-$leaf.sif"
    local mbind
    if [[ -f "$msif" ]]; then
        mbind="--bind $msif:/model:image-src=/"
    elif [[ -d "$mdir" ]]; then
        mbind="--bind $mdir:/model:ro"
    else
        fail "model not found: neither $msif nor $mdir/ (copy it under $MODELS_DIR)"
    fi
    local q="" tp=""
    [[ -n "$R_QUANT" ]]  && q="--quantization $R_QUANT"
    [[ -n "$R_PARSER" ]] && tp="--enable-auto-tool-choice --tool-call-parser $R_PARSER"
    # Blackwell/sm_120 + FlashInfer workaround is harmless elsewhere.
    cat <<CMD
TORCH_CUDA_ARCH_LIST=\${TORCH_CUDA_ARCH_LIST:-} VLLM_USE_FLASHINFER_SAMPLER=0 \\
apptainer run --nv \\
  $mbind \\
  --bind $CACHE_DIR:/cache \\
  --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \\
  "$VLLM_SIF" \\
  /opt/vllm/bin/vllm serve /model \\
    --served-model-name ${MODEL_KEY:-model} \\
    --tensor-parallel-size $R_TP \\
    --max-model-len $R_MAXLEN \\
    --gpu-memory-utilization 0.90 \\
    $q $tp \\
    --enable-prefix-caching \\
    --host 127.0.0.1 --port $port
CMD
}

render_sbatch() {
    # $1 = template name, $2 = output path, rest = substitutions handled
    # by the caller via exported vars; we just envsubst-lite with sed.
    local tmpl="$SCRIPT_DIR/$1" out="$2"
    [[ -f "$tmpl" ]] || fail "template not found: $tmpl"
    sed -e "s#@PARTITION@#$SL_PARTITION#g" \
        -e "s#@GPUS@#$SL_GPUS#g" \
        -e "s#@TIME@#$SL_TIME#g" \
        -e "s#@CPUS@#$SL_CPUS#g" \
        -e "s#@MEM@#$SL_MEM#g" \
        -e "s#@LOGS_DIR@#$LOGS_DIR#g" \
        -e "s#@LOOPCODER_HOME@#$LOOPCODER_HOME#g" \
        -e "s#@HPC_SH@#$SCRIPT_DIR/loopcoder-hpc.sh#g" \
        -e "s#@VLLM_PORT@#$VLLM_PORT#g" \
        -e "s#@PLAN@#${PLAN_ARG:-}#g" \
        -e "s#@MODEL_KEY@#${MODEL_KEY:-}#g" \
        "$tmpl" > "$out"
}

cmd_init() {
    ensure_layout
    log "LOOPCODER_HOME = $LOOPCODER_HOME"
    cat <<EOF

Created the HPC layout. Now copy in (from your build host):
  $SIF_DIR/vllm.sif
  $SIF_DIR/loopcoder-suite.sif
  $SIF_DIR/loopcoder-sandbox.sif
  $MODELS_DIR/<leaf>/            (unpacked HF model dir, or model-<leaf>.sif)
  $ETC_DIR/install.yaml          (with models[] or a single model.id)
  $ETC_DIR/loopcoder.yaml        (llm.base_url=http://127.0.0.1:${VLLM_PORT}/v1)

Then:
  bash $0 submit-allinone plan.yaml --model <key>
  bash $0 submit-serve
EOF
}

# --- internal: run inside the compute job (called by sbatch script) ---
cmd_serve_inproc() {
    preflight
    local mid; mid="$(pick_model_id)"
    [[ -n "$mid" ]] || fail "no model id resolved"
    log "serving $mid on :$VLLM_PORT"
    eval "$(vllm_run_cmd "$mid" "$VLLM_PORT")"
}

cmd_allinone_inproc() {
    preflight
    ensure_layout
    local plan="${1:?plan required}"
    local mid; mid="$(pick_model_id)"
    [[ -n "$mid" ]] || fail "no model id resolved"

    log "starting vLLM ($mid) in background on :$VLLM_PORT"
    ( eval "$(vllm_run_cmd "$mid" "$VLLM_PORT")" \
        > "$LOGS_DIR/vllm-${SLURM_JOB_ID:-local}.log" 2>&1 ) &
    local vpid=$!

    log "waiting for vLLM…"
    local up=0
    for _ in $(seq 1 180); do
        if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
            up=1; break
        fi
        kill -0 "$vpid" 2>/dev/null || fail "vLLM died early; see $LOGS_DIR/"
        sleep 5
    done
    [[ $up -eq 1 ]] || { kill "$vpid" 2>/dev/null || true; fail "vLLM not ready in 15m"; }

    log "running plan: $plan"
    set +e
    lc run --plan "$plan"
    local rc=$?
    set -e
    log "loopcoder rc=$rc; stopping vLLM"
    kill "$vpid" 2>/dev/null || true
    return $rc
}

main() {
    local sub="${1:-}"; shift || true
    case "$sub" in
        init) cmd_init ;;
        resolve)
            preflight
            apptainer exec "$SUITE_SIF" loopcoder catalog-resolve "${1:?model id}"
            ;;
        run)
            preflight; ensure_layout
            local plan="${1:?usage: run <plan.yaml>}"
            lc run --plan "$plan"
            ;;
        submit-allinone)
            ensure_layout
            local plan=""
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --model) MODEL_KEY="$2"; shift 2 ;;
                    --partition) SL_PARTITION="$2"; shift 2 ;;
                    --gpus) SL_GPUS="$2"; shift 2 ;;
                    --time) SL_TIME="$2"; shift 2 ;;
                    -*) fail "unknown flag: $1" ;;
                    *) plan="$1"; shift ;;
                esac
            done
            [[ -n "$plan" ]] || fail "usage: submit-allinone <plan.yaml> [--model key]"
            export PLAN_ARG; PLAN_ARG="$(readlink -f "$plan")"
            local job="$LOGS_DIR/sbatch-allinone.$$.sh"
            render_sbatch "sbatch-allinone.sh.tmpl" "$job"
            log "submitting: sbatch $job"
            command -v sbatch >/dev/null 2>&1 \
                && sbatch "$job" \
                || { log "(no sbatch here) rendered job at $job"; cat "$job"; }
            ;;
        submit-serve)
            ensure_layout
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --model) MODEL_KEY="$2"; shift 2 ;;
                    --partition) SL_PARTITION="$2"; shift 2 ;;
                    --gpus) SL_GPUS="$2"; shift 2 ;;
                    --time) SL_TIME="$2"; shift 2 ;;
                    -*) fail "unknown flag: $1" ;;
                    *) shift ;;
                esac
            done
            local job="$LOGS_DIR/sbatch-serve.$$.sh"
            render_sbatch "sbatch-serve.sh.tmpl" "$job"
            log "submitting: sbatch $job"
            command -v sbatch >/dev/null 2>&1 \
                && sbatch "$job" \
                || { log "(no sbatch here) rendered job at $job"; cat "$job"; }
            ;;
        _serve-inproc)   cmd_serve_inproc ;;
        _allinone-inproc) cmd_allinone_inproc "$@" ;;
        -h|--help|"")
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            ;;
        *) fail "unknown subcommand: $sub (try --help)" ;;
    esac
}

main "$@"
