#!/bin/bash
# Analyze a transcript from your clipboard.
#   bash ~/Downloads/yt-scalper/ANALYZE.sh
#   bash ~/Downloads/yt-scalper/ANALYZE.sh --channel "Meet Kevin" --title "NVDA breakdown"
set -u
cd "$(dirname "$0")" || exit 1
HERE_KEY="$(pwd)/.anthropic-key"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 python3; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -z "$PY" ] && { echo "No Python 3.9+ found."; exit 1; }

KEYFILE="$HERE_KEY"
[ -s "$KEYFILE" ] || { echo "No API key at $KEYFILE - run START_HERE.sh once first."; exit 1; }
ANTHROPIC_API_KEY="$(tr -d '[:space:]' < "$KEYFILE")"; export ANTHROPIC_API_KEY

"$PY" analyze_clipboard.py "$@"
