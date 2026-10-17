#!/usr/bin/env bash
# Collect Shazam (and, with a key in .env, YouTube) stats for PERSONAL ANIMOSITY
# (Apple artist id 1720235535). The artist has two "PERSONAL ANIMOSITY - Topic"
# channels; passing them skips the 100-unit channel search. The channel flags
# are ignored when YouTube is off.
# Extra arguments are passed through, e.g. ./personal-animosity.sh --json
set -euo pipefail

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

exec "$PYTHON" shazam_stats.py --artist-id 1720235535 \
    --youtube-topic-channel UCW0XqjvAVf_zimSmZ9MzTaA \
    --youtube-topic-channel UCExcanjCPOj2O8CKzHas8Fg \
    "$@"
