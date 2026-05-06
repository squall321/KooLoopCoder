#!/usr/bin/env bash
# Inside Bundle VM (Ubuntu 24.04): build loopcoder-suite.sif from
# containers/loopcoder-suite.def, baking in the agent + API + MCP + CLI.
#
# Args:
#   $1 output dir   (e.g. /output/containers)
#   $2 source root  (e.g. /home/loopcoder/loopcoder-src)

set -euo pipefail
OUT="${1:?output dir}"
SRC="${2:?source dir}"
mkdir -p "$OUT"

DEF="$SRC/containers/loopcoder-suite.def"
[[ -f "$DEF" ]] || { echo "missing $DEF" >&2; exit 1; }

# Embed wheelhouse so suite SIF is built fully offline. collect_wheels.sh
# is expected to have run first and produced /output/wheels.
# We ALWAYS create $SRC/wheels (possibly empty) because the .def's %files
# block requires it to exist. If empty, the %post falls back to PyPI.
WHEELS_DIR="${WHEELS_DIR:-$(dirname "$OUT")/wheels}"
rm -rf "$SRC/wheels"
mkdir -p "$SRC/wheels"
if [[ -d "$WHEELS_DIR" && -n "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]]; then
    echo "[suite-sif] embedding wheelhouse: $WHEELS_DIR ($(ls "$WHEELS_DIR" | wc -l) files)"
    cp -r "$WHEELS_DIR"/. "$SRC/wheels/"
else
    echo "[suite-sif] no wheelhouse at $WHEELS_DIR; .sif build will fall back to PyPI"
fi

# Apptainer build needs the source tree visible at the working directory
# because the .def's %files block uses relative paths.
cd "$SRC"

OUT_SIF="$OUT/loopcoder-suite.sif"
echo "[suite-sif] building $OUT_SIF from $DEF"
sudo apptainer build --force "$OUT_SIF" "$DEF"

# Smoke-test inside the freshly-built SIF
sudo apptainer exec "$OUT_SIF" loopcoder --version
sudo apptainer exec "$OUT_SIF" python -c "
from loopcoder.api.server import build_app
from loopcoder.mcp.server import build_mcp_server
from loopcoder.tools.registry import default_registry
print('tools:', len(default_registry().names()))
print('OK suite imports')
"

ls -lh "$OUT_SIF"
