#!/usr/bin/env bash
# Inside Bundle VM: download model weights with hf_transfer for speed.
#
# Args:
#   $1 = output dir (e.g. /output/models)
#
# Reads MODEL_ID from $LOOPCODER_MODEL_ID or defaults to Qwen3-Coder-480B-FP8.

set -euo pipefail
OUT="${1:?output dir}"
MODEL_ID="${LOOPCODER_MODEL_ID:-Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8}"
LOCAL_NAME="${MODEL_ID##*/}"
DST="$OUT/$LOCAL_NAME"
mkdir -p "$DST"

export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_HUB_DISABLE_TELEMETRY=1

# Use huggingface-cli (pip --user installed in bootstrap)
HF_BIN="$(command -v huggingface-cli || echo "$HOME/.local/bin/huggingface-cli")"
if [[ ! -x "$HF_BIN" ]]; then
    python3 -m pip install --user --quiet "huggingface_hub[hf_transfer]"
    HF_BIN="$HOME/.local/bin/huggingface-cli"
fi

echo "[model] downloading $MODEL_ID to $DST"
"$HF_BIN" download "$MODEL_ID" \
    --local-dir "$DST" \
    --local-dir-use-symlinks False \
    --resume-download

# Sanity check: must contain config.json
[[ -f "$DST/config.json" ]] || { echo "[model] config.json missing"; exit 1; }
echo "[model] OK: $(du -sh "$DST" | cut -f1)"
