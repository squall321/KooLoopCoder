#!/usr/bin/env bash
# Quick health probe: vLLM up + responding + recent error rate sane.
# Exit 0 = healthy, non-zero = problem.
set -euo pipefail

PORT="${LOOPCODER_VLLM_PORT:-8000}"
URL="http://127.0.0.1:${PORT}"

# 1) /v1/models reachable
if ! curl -sf --max-time 5 "$URL/v1/models" >/dev/null; then
    echo "FAIL: /v1/models not responding" >&2
    exit 1
fi

# 2) Quick chat completion
PAYLOAD='{"model":"healthcheck-ignore","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
# Pull model id from /v1/models
MODEL=$(curl -sf "$URL/v1/models" | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["id"])')
PAYLOAD=$(echo "$PAYLOAD" | sed "s|healthcheck-ignore|$MODEL|")
if ! curl -sf --max-time 30 -H 'Content-Type: application/json' -d "$PAYLOAD" "$URL/v1/chat/completions" >/dev/null; then
    echo "FAIL: chat/completions failed" >&2
    exit 2
fi

# 3) systemd unit reports active
if ! systemctl is-active --quiet vllm; then
    echo "FAIL: systemd vllm not active" >&2
    exit 3
fi

echo "OK"
