#!/usr/bin/env bash
# Inside Bundle VM: download Python wheels (manylinux/cp312) for the
# loopcoder package and its dependencies. The B300 node will then
# `pip install --no-index --find-links wheels/`.
#
# Args:
#   $1 = output directory (e.g. /output/wheels)
#   $2 = loopcoder source tree (e.g. /home/loopcoder/loopcoder-src)

set -euo pipefail
OUT="${1:?output dir}"
SRC="${2:?source dir}"

mkdir -p "$OUT"

# Stable list of agent dependencies. Keep in sync with pyproject.toml.
DEPS=(
    "pip"
    "wheel"
    "setuptools"
    "pydantic>=2.6,<3"
    "pyyaml>=6.0,<7"
    "jinja2>=3.1,<4"
    "openai>=1.30,<2"
    "tiktoken>=0.7,<1"
    "rich>=13.7,<14"
    "click>=8.1,<9"
    "GitPython>=3.1,<4"
    "sqlalchemy>=2.0,<3"
    "tenacity>=8.2,<9"
    "platformdirs>=4.2,<5"
    "httpx>=0.27,<1"
    # vLLM client smoke check + tests on the B300
    "pytest>=8.0"
    "pytest-cov>=5.0"
)

echo "[wheels] downloading ${#DEPS[@]} dep specs to $OUT"
python3 -m pip download \
    --dest "$OUT" \
    --python-version 3.12 \
    --platform manylinux_2_28_x86_64 \
    --platform manylinux2014_x86_64 \
    --platform any \
    --only-binary=:all: \
    "${DEPS[@]}"

# Build the loopcoder wheel itself from the source tree.
python3 -m pip wheel --no-deps --wheel-dir "$OUT" "$SRC"

echo "[wheels] $(ls -1 "$OUT" | wc -l) wheel/sdist files"

# Quick verify: try a no-network install into a throwaway venv
TMP=$(mktemp -d)
python3 -m venv "$TMP/venv"
"$TMP/venv/bin/pip" install --no-index --find-links "$OUT" --quiet \
    pydantic openai click jinja2 GitPython
"$TMP/venv/bin/python" -c "import pydantic, openai, click, jinja2, git; print('[wheels] verify OK')"
rm -rf "$TMP"
