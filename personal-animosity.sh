#!/usr/bin/env bash
# Collect Shazam stats for PERSONAL ANIMOSITY (Apple artist id 1720235535).
# Extra arguments are passed through, e.g. ./personal-animosity.sh --json
set -euo pipefail

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

exec "$PYTHON" shazam_stats.py --artist-id 1720235535 "$@"
