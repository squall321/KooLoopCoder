#!/usr/bin/env bash
# Inside Bundle VM: download / build Python wheels for the offline B300
# install. The result is a self-contained directory the B300 can use as:
#
#   pip install --no-index --find-links wheels/  loopcoder
#
# Strategy (avoids drift from pyproject.toml):
#   1. `pip wheel <SRC>` — builds OUR wheel + every transitive dep into
#      `$OUT`. Dependencies are read straight from pyproject.toml so adding
#      a new dep there is automatically reflected in the bundle.
#   2. Add bootstrap pip/setuptools/wheel separately (so the offline target
#      can re-run pip install without prior internet).
#   3. Add test-only deps (pytest, ruff, mypy) so the suite SIF can run
#      its own self-tests without network.
#
# Args:
#   $1 = output directory  (e.g. /output/wheels)
#   $2 = loopcoder source  (e.g. /home/loopcoder/loopcoder-src)

set -euo pipefail
OUT="${1:?output dir}"
SRC="${2:?source dir}"
mkdir -p "$OUT"

PY_VER="3.12"
PLATFORM_ARGS=(
    --python-version "$PY_VER"
    --platform manylinux_2_28_x86_64
    --platform manylinux2014_x86_64
    --platform any
    --only-binary=:all:
)

echo "[wheels] (1/3) bootstrap pip/setuptools/wheel"
python3 -m pip download "${PLATFORM_ARGS[@]}" --dest "$OUT" \
    "pip>=24" "wheel>=0.42" "setuptools>=68"

echo "[wheels] (2/3) loopcoder + every dep declared in pyproject.toml"
# Use `pip wheel` so transitive deps come along automatically. We also
# pass the source dir twice (once via constraints to download with the
# matrix above, once via wheel to actually build the loopcoder wheel).
python3 -m pip wheel \
    --wheel-dir "$OUT" \
    "$SRC"

# Some pure-python packages (e.g. mcp) only ship sdists. Re-run download
# with --no-binary fallback to grab those too if pip wheel didn't pull them.
echo "[wheels] (3/3) sdist fallback for source-only deps"
python3 -m pip download \
    --dest "$OUT" \
    --no-deps \
    "mcp>=1.0,<2" \
    "sse-starlette>=2.0,<3" \
    || echo "  (some packages already present; ignoring duplicate-download warnings)"

# Test-only deps (ship them so post-install self-tests work offline).
echo "[wheels] (4/4) test-only deps"
python3 -m pip download "${PLATFORM_ARGS[@]}" --dest "$OUT" \
    "pytest>=8.0" "pytest-cov>=5.0" "ruff>=0.5"

echo "[wheels] $(ls -1 "$OUT" | wc -l) wheel/sdist files in $OUT"
echo "[wheels] $(du -sh "$OUT" | cut -f1) total"

# ------------- Verify -------------
# We must be able to install the loopcoder wheel using ONLY the wheelhouse,
# with no network. This catches missing transitive deps.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[wheels] verifying offline install into throwaway venv"
python3 -m venv "$TMP/venv"
"$TMP/venv/bin/pip" install --no-index --find-links "$OUT" --quiet \
    --upgrade pip wheel setuptools
"$TMP/venv/bin/pip" install --no-index --find-links "$OUT" --quiet loopcoder

# Smoke-test the installed CLI + each subsystem
"$TMP/venv/bin/loopcoder" --version
"$TMP/venv/bin/python" - <<'PY'
import loopcoder
import loopcoder.api.server
import loopcoder.mcp.server
from loopcoder.tools.registry import default_registry
print("loopcoder", loopcoder.__version__, "tools:", len(default_registry().names()))
PY
echo "[wheels] verify OK"
