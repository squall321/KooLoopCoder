#!/usr/bin/env bash
# Pack a HuggingFace model directory into a single, read-only Apptainer SIF.
#
# Why pack? A SIF is a single file → trivially scp/rsync/usb-copy. The
# vLLM systemd unit then bind-mounts it read-only at /model:
#
#     apptainer run --nv \
#         --bind model.sif:/model:image-src=/  \
#         vllm.sif  /opt/vllm/bin/vllm serve /model ...
#
# Usage:
#   bash pack-model.sh <model_dir> <output.sif>
#   bash pack-model.sh /scratch/models/Qwen2.5-Coder-1.5B-Instruct  qwen-1.5b.sif
#
#   # Or download AND pack in one go:
#   bash pack-model.sh --hf Qwen/Qwen2.5-Coder-1.5B-Instruct  qwen-1.5b.sif [tmp_dir]
#
# Requires: apptainer ≥ 1.3, sudo (for build), python3 + huggingface_hub
# (only when --hf is used).

set -euo pipefail

MODE="dir"
HF_ID=""
TMP_DIR=""
SUDO_BIN="sudo"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hf)        MODE="hf"; HF_ID="$2"; shift 2 ;;
        --tmp)       TMP_DIR="$2"; shift 2 ;;
        --no-sudo)   SUDO_BIN=""; shift ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --*)         echo "unknown flag: $1" >&2; exit 2 ;;
        *)           break ;;
    esac
done

if [[ "$MODE" == "dir" ]]; then
    SRC_DIR="${1:?usage: pack-model.sh <model_dir> <output.sif>}"
    OUT_SIF="${2:?need output sif path}"
else
    OUT_SIF="${1:?need output sif path}"
    [[ -n "$TMP_DIR" ]] || TMP_DIR="$(mktemp -d -t loopcoder-pack.XXXXXX)"
    SRC_DIR="$TMP_DIR/$(echo "$HF_ID" | awk -F/ '{print $NF}')"
fi

# ---------- HF download (optional) ----------
if [[ "$MODE" == "hf" ]]; then
    echo "[pack-model] huggingface download: $HF_ID -> $SRC_DIR"
    mkdir -p "$SRC_DIR"
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    python3 -m huggingface_hub.commands.huggingface_cli download \
        "$HF_ID" \
        --local-dir "$SRC_DIR" \
        --local-dir-use-symlinks False \
        --resume-download
fi

[[ -d "$SRC_DIR" ]] || { echo "model dir not found: $SRC_DIR" >&2; exit 1; }
[[ -f "$SRC_DIR/config.json" ]] || { echo "$SRC_DIR/config.json missing — not a HF model dir?" >&2; exit 1; }

# ---------- build a tiny .def that copies the dir in ----------
DEF=$(mktemp -t loopcoder-model-XXXX.def)
trap 'rm -f "$DEF"' EXIT

cat > "$DEF" <<DEF
Bootstrap: scratch
Stage: model

%files
    $SRC_DIR/* /

%labels
    org.loopcoder.role model
    org.loopcoder.source ${HF_ID:-local}
    org.loopcoder.packed_at $(date -Iseconds)

%runscript
    echo "This SIF contains read-only model weights. Mount with:"
    echo "  apptainer run --bind model.sif:/model:image-src=/ vllm.sif vllm serve /model ..."
DEF

echo "[pack-model] building $OUT_SIF from $SRC_DIR"
${SUDO_BIN} apptainer build "$OUT_SIF" "$DEF"

# ---------- verify ----------
echo "[pack-model] verifying SIF can list its files"
apptainer exec "$OUT_SIF" ls -la /config.json >/dev/null
echo "[pack-model] OK"
ls -lh "$OUT_SIF"

# Cleanup --hf temp dir if we made it
if [[ "$MODE" == "hf" && -n "$TMP_DIR" ]]; then
    echo "[pack-model] keeping temp model dir at $TMP_DIR"
    echo "[pack-model] (you can rm -rf it once you've copied the SIF off)"
fi

echo
echo "To run with vLLM:"
echo "  apptainer run --nv \\"
echo "      --bind $OUT_SIF:/model:image-src=/ \\"
echo "      /opt/apptainers/current/vllm.sif \\"
echo "      /opt/vllm/bin/vllm serve /model --tensor-parallel-size 1 --max-model-len 8192"
