#!/usr/bin/env bash
# Quick end-to-end test on the dev host using a tiny model:
#
#   1. Download Qwen/Qwen2.5-Coder-0.5B-Instruct (~1GB) directly on the host.
#   2. Start a local vLLM server (no Apptainer; just pip-installed vllm in venv).
#   3. Run loopcoder against examples/plan_simple.yaml and assert it passes.
#
# This validates the entire LoopCoder loop without needing the Bundle/Test VM
# pipeline or the full 480GB model. Useful as a fast smoke test on every code
# change to the agent.
#
# Requires: a venv with vllm + dependencies, and a CUDA-capable GPU (or the
# CPU build of vllm, which is much slower but possible).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

OUTPUT="$PROJECT_ROOT/output/tiny-test"
MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-Coder-0.5B-Instruct}"
MODEL_DIR="$OUTPUT/models/${MODEL_ID##*/}"
VLLM_PORT="${VLLM_PORT:-18000}"   # avoid collision with system vllm
WORKSPACE="$OUTPUT/workspace"
LOG_DIR="$OUTPUT/logs"
PIDFILE="$OUTPUT/vllm.pid"

mkdir -p "$OUTPUT" "$WORKSPACE" "$LOG_DIR" "$OUTPUT/models"

# venv check
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    echo "venv missing. Create one and install loopcoder + vllm:" >&2
    echo "  python3.12 -m venv .venv" >&2
    echo "  .venv/bin/pip install -e ." >&2
    echo "  .venv/bin/pip install vllm  # may take a while" >&2
    exit 1
fi

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
VENV_PIP="$PROJECT_ROOT/.venv/bin/pip"
LOOPCODER="$PROJECT_ROOT/.venv/bin/loopcoder"

# 1) install vllm + huggingface_hub if absent
if ! "$VENV_PY" -c 'import vllm' 2>/dev/null; then
    echo "[tiny-e2e] installing vllm into venv (this can take 5-10 min)…"
    "$VENV_PIP" install --quiet "vllm" "huggingface_hub[hf_transfer]"
fi

# 2) download tiny model into LoopCoder/output/tiny-test/models/
if [[ ! -f "$MODEL_DIR/config.json" ]]; then
    echo "[tiny-e2e] downloading $MODEL_ID -> $MODEL_DIR"
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    "$VENV_PY" -m huggingface_hub.commands.huggingface_cli download \
        "$MODEL_ID" \
        --local-dir "$MODEL_DIR" \
        --local-dir-use-symlinks False \
        --resume-download
fi

# 3) start vllm in background (host mode, single GPU, no Apptainer)
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[tiny-e2e] vllm already running (pid $(cat "$PIDFILE"))"
else
    echo "[tiny-e2e] starting vllm on :$VLLM_PORT"
    nohup "$VENV_PY" -m vllm.entrypoints.openai.api_server \
        --model "$MODEL_DIR" \
        --served-model-name "$MODEL_ID" \
        --host 127.0.0.1 \
        --port "$VLLM_PORT" \
        --max-model-len 8192 \
        --gpu-memory-utilization 0.5 \
        --enable-prefix-caching \
        > "$LOG_DIR/vllm.log" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "  pid=$(cat "$PIDFILE"), log=$LOG_DIR/vllm.log"
fi

# 4) wait for vllm /v1/models
echo "[tiny-e2e] waiting for vllm…"
for i in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null; then
        echo "  vllm ready after ${i}*5s"
        break
    fi
    sleep 5
done
curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null || {
    echo "vllm did not start; see $LOG_DIR/vllm.log" >&2
    exit 2
}

# 5) write a dev loopcoder config pointing at our local vllm + workspace
DEV_CFG="$OUTPUT/loopcoder.yaml"
cat > "$DEV_CFG" <<YAML
llm:
  base_url: http://127.0.0.1:${VLLM_PORT}/v1
  api_key: EMPTY
  model: "$MODEL_ID"
  temperature: 0.2
  top_p: 0.95
  max_completion_tokens: 1024
  request_timeout_sec: 120

context:
  total_budget_tokens: 6000
  reserve_for_completion: 1024
  always_pin: []

sandbox:
  backend: host
  exec_timeout_sec: 60

tools:
  shell:
    allowed_patterns:
      - "pytest*"
      - "python*"
      - "ls*"
      - "cat *"
    output_max_kb: 64
  fs:
    forbidden_paths: []
    max_read_bytes: 262144

loop:
  max_iterations_per_goal: 8
  max_total_minutes: 15
  strategy_change_after: 3
  rollback_after: 99
  use_critic: false

storage:
  state_db: $OUTPUT/sessions.db
  log_dir: $LOG_DIR
  workspaces_root: $OUTPUT/workspaces

ui:
  tty: plain
YAML

# 6) prepare a plan that uses the demo workspace
PLAN="$OUTPUT/plan.yaml"
cat > "$PLAN" <<YAML
project:
  name: hello_loopcoder_tiny
  workspace: $WORKSPACE
  language: python
constraints:
  max_iterations_per_goal: 8
  max_total_minutes: 10
  forbidden_paths: []
  allowed_shell_commands:
    - "pytest*"
    - "python*"
    - "ls*"
    - "cat *"
  network_allowed: false
context:
  description: |
    Implement a tiny Python module to make the failing test pass.
goals:
  - id: implement
    title: "Make hello.py print hello"
    description: |
      Create hello.py that prints "hello world" exactly when run with python3.
    acceptance:
      - kind: file_exists
        path: hello.py
      - kind: shell
        run: "python3 hello.py"
        timeout: 10
        expect:
          exit_code: 0
          stdout_contains: "hello world"
YAML

# 7) run loopcoder
echo "[tiny-e2e] running loopcoder…"
LOOPCODER_YAML="$DEV_CFG" "$LOOPCODER" run --plan "$PLAN" || {
    echo "loopcoder run failed; check $LOG_DIR/" >&2
    exit 3
}

# 8) report
SESSION_ID=$(LOOPCODER_YAML="$DEV_CFG" "$LOOPCODER" list 2>/dev/null | head -1 | awk '{print $1}')
if [[ -n "$SESSION_ID" ]]; then
    echo "[tiny-e2e] session: $SESSION_ID"
    LOOPCODER_YAML="$DEV_CFG" "$LOOPCODER" report "$SESSION_ID" --out "$OUTPUT/report.md" || true
    echo "[tiny-e2e] report at $OUTPUT/report.md"
fi

# 9) optional shutdown
if [[ "${KEEP_VLLM:-0}" != "1" ]]; then
    if [[ -f "$PIDFILE" ]]; then
        echo "[tiny-e2e] stopping vllm pid=$(cat "$PIDFILE")"
        kill "$(cat "$PIDFILE")" 2>/dev/null || true
        rm -f "$PIDFILE"
    fi
fi

echo "[tiny-e2e] DONE — generated workspace at $WORKSPACE"
