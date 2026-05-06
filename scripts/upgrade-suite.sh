#!/usr/bin/env bash
# Atomic LoopCoder SIF upgrade.
#
# Usage:
#   sudo bash upgrade-suite.sh <new.sif> <stable_name>
#   sudo bash upgrade-suite.sh /tmp/loopcoder-suite-0.2.0.sif loopcoder-suite.sif
#   sudo bash upgrade-suite.sh /tmp/vllm-0.7.5.sif            vllm.sif
#
# Process:
#   1. cp <new.sif> /opt/apptainers/   (versioned filename preserved)
#   2. ln -sfn <basename(new.sif)> /opt/apptainers/current/<stable>
#   3. systemctl restart <unit-derived-from-stable>
#   4. show recent log lines
#
# Roll back by re-running with the previous .sif.
#
# Optional flags:
#   --no-restart      stage the symlink, do not touch systemd
#   --keep N          retain N most-recent versions of <stable>'s prefix; older ones are removed
#                     (default: keep 5)

set -euo pipefail

STORE="${LOOPCODER_SIF_STORE:-/opt/apptainers}"
CURRENT="${LOOPCODER_SIF_CURRENT:-${STORE}/current}"
NO_RESTART=0
KEEP=5

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-restart)   NO_RESTART=1; shift ;;
        --keep)         KEEP="$2"; shift 2 ;;
        --store)        STORE="$2"; CURRENT="${STORE}/current"; shift 2 ;;
        --help|-h)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --*)
            echo "unknown flag: $1" >&2
            exit 2
            ;;
        *)
            POSITIONAL+=("$1"); shift
            ;;
    esac
done

(( ${#POSITIONAL[@]} >= 2 )) || { echo "usage: $0 <new.sif> <stable_name>" >&2; exit 2; }
NEW_SIF="${POSITIONAL[0]}"
STABLE="${POSITIONAL[1]}"

[[ -f "$NEW_SIF" ]] || { echo "no such file: $NEW_SIF" >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || { echo "must run as root" >&2; exit 1; }

mkdir -p "$STORE" "$CURRENT"
chmod 755 "$STORE" "$CURRENT"

NEW_BASE="$(basename "$NEW_SIF")"
TARGET="$STORE/$NEW_BASE"
LINK="$CURRENT/$STABLE"

echo "==> copy $NEW_SIF -> $TARGET"
cp -u "$NEW_SIF" "$TARGET"
chmod 644 "$TARGET"

# remember previous link target for nicer logs
PREV_LINK="$(readlink -f "$LINK" 2>/dev/null || echo '(none)')"

echo "==> atomic link: $LINK -> $NEW_BASE  (was $(basename "$PREV_LINK" 2>/dev/null || echo none))"
ln -sfn "$NEW_BASE" "$LINK"

# Determine which systemd unit corresponds to this stable name.
unit=""
case "$STABLE" in
    vllm.sif)             unit="vllm" ;;
    loopcoder-suite.sif)  unit="loopcoder" ;;
    loopcoder-sandbox.sif) unit="" ;;  # sandbox is invoked on-demand by the agent
    *)                    unit="" ;;
esac

if [[ $NO_RESTART -eq 0 && -n "$unit" ]]; then
    if systemctl list-unit-files | grep -q "^${unit}\.service"; then
        echo "==> systemctl restart $unit"
        systemctl restart "$unit"
        sleep 1
        systemctl --no-pager --lines=20 status "$unit" || true
    else
        echo "(systemd unit '$unit' not installed; skipping restart)"
    fi
fi

# Prune old versioned SIFs sharing the stable prefix.
prefix="${STABLE%.sif}"
# eg. loopcoder-suite.sif -> prefix = loopcoder-suite
if [[ "$KEEP" -gt 0 ]]; then
    mapfile -t old < <(ls -1t "$STORE"/${prefix}*.sif 2>/dev/null || true)
    if (( ${#old[@]} > KEEP )); then
        for f in "${old[@]:$KEEP}"; do
            # never delete the file currently linked
            if [[ "$(readlink -f "$LINK")" != "$(readlink -f "$f")" ]]; then
                echo "==> prune $f"
                rm -f "$f"
            fi
        done
    fi
fi

echo "==> done."
