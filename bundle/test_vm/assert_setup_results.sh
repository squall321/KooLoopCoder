#!/usr/bin/env bash
# After setup.sh has run inside the Test VM, verify the post-conditions:
#   - apptainer installed
#   - python venv built
#   - loopcoder CLI works
#   - vllm.service unit registered (but NOT necessarily started)
#   - all stage markers present 0,2-9,12,13 (1,10,11 are GPU-required and skipped)
# Args: $1 vm-name

set -euo pipefail
VM="${1:?vm}"

run() {
    local label="$1"; shift
    if ssh "$VM" "$@"; then
        echo "  PASS: $label"
    else
        echo "  FAIL: $label" >&2
        return 1
    fi
}

failed=0

echo "Asserting setup.sh post-conditions in Test VM ($VM)…"

run "apptainer present"           "command -v apptainer >/dev/null"      || failed=1
run "python3.12 venv exists"      "test -x /scratch/loopcoder/venv/bin/python" || failed=1
run "loopcoder CLI works"         "/scratch/loopcoder/venv/bin/loopcoder --version" || failed=1
run "vllm.service enabled"        "systemctl is-enabled vllm 2>/dev/null | grep -q enabled" || failed=1
run "stage markers (test mode)"   "test -f /var/lib/loopcoder/.stage_0 && test -f /var/lib/loopcoder/.stage_2 && test -f /var/lib/loopcoder/.stage_5 && test -f /var/lib/loopcoder/.stage_6 && test -f /var/lib/loopcoder/.stage_9 && test -f /var/lib/loopcoder/.stage_12" || failed=1
run "loopcoder config validate"   "/scratch/loopcoder/venv/bin/loopcoder config validate" || failed=1
run "vllm sif imports"            "apptainer exec /scratch/loopcoder/containers/vllm.sif python -c 'import vllm; print(vllm.__version__)'" || failed=1

if [[ $failed -eq 0 ]]; then
    echo "ALL ASSERTIONS PASSED"
    exit 0
else
    echo "SOME ASSERTIONS FAILED" >&2
    exit 1
fi
