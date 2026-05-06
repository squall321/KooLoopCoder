#!/usr/bin/env bash
# Quick throughput benchmark using vllm bench inside the container.
# Reports prompt + completion tokens/sec.

set -euo pipefail

SIF="${VLLM_SIF:-/scratch/loopcoder/containers/vllm.sif}"
PORT="${LOOPCODER_VLLM_PORT:-8000}"

INPUT_LEN="${INPUT_LEN:-4096}"
OUTPUT_LEN="${OUTPUT_LEN:-1024}"
NUM_PROMPTS="${NUM_PROMPTS:-32}"
CONCURRENCY="${CONCURRENCY:-4}"

echo "Benchmarking vLLM at :$PORT (input=$INPUT_LEN, output=$OUTPUT_LEN, prompts=$NUM_PROMPTS, concurrency=$CONCURRENCY)"
apptainer exec "$SIF" python -m vllm.entrypoints.openai.api_server.benchmark_serving \
    --backend openai-chat \
    --model "$(curl -sf http://127.0.0.1:${PORT}/v1/models | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["id"])')" \
    --base-url "http://127.0.0.1:${PORT}/v1" \
    --num-prompts "$NUM_PROMPTS" \
    --max-concurrency "$CONCURRENCY" \
    --random-input-len "$INPUT_LEN" \
    --random-output-len "$OUTPUT_LEN" \
    || echo "(benchmark module not present in this vllm version; consider 'vllm bench' manually)"
