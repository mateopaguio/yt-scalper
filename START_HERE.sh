#!/bin/bash
# One-shot setup + run for The Byrde scalper.
#   bash ~/Downloads/yt-scalper/START_HERE.sh
# Reads your API key from ~/Downloads/anthropic-key.txt

set -u
cd "$(dirname "$0")" || exit 1
HERE_KEY="$(pwd)/.anthropic-key"

echo "=============================================="
echo " The Byrde — Scalper setup & first run"
echo "=============================================="
echo

# --- 1. Python ---
# macOS ships 3.9 as /usr/bin/python3. The code runs on 3.9, but prefer a
# newer interpreter when one is installed (Homebrew, python.org, pyenv).
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: no Python 3.9+ found at all."
  echo
  if command -v brew >/dev/null 2>&1; then
    echo "  You have Homebrew. Run:"
    echo "      brew install python@3.12"
  else
    echo "  Easiest: download the macOS installer from"
    echo "      https://www.python.org/downloads/"
    echo "  Run it, then rerun this script."
  fi
  exit 1
fi
PYV=$("$PY" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')
echo "[1/4] Using $PY (Python $PYV)"

KEYFILE="$HERE_KEY"
if [ ! -s "$KEYFILE" ]; then
  echo
  echo "  Copy your API key in the Anthropic console (click the copy icon in"
  echo "  the dialog shown when the key is created), then press Return here."
  printf "  Ready? [press Return] "
  read -r -s _unused        # silent: a paste here is never echoed
  echo
  if ! command -v pbpaste >/dev/null 2>&1; then
    echo "ERROR: pbpaste not found (is this macOS?)."; exit 1
  fi
  if [ -n "${_unused:-}" ]; then
    CLIP="$(printf '%s' "$_unused" | tr -d '[:space:]')"
  else
    CLIP="$(pbpaste | tr -d '[:space:]')"
  fi
  unset _unused
  case "$CLIP" in
    sk-ant-*) : ;;
    "") echo "ERROR: clipboard is empty. Copy the key, then rerun."; exit 1 ;;
    apikey_*) echo "ERROR: that is the key ID, not the key. You need the long sk-ant-... value."; exit 1 ;;
    *) echo "ERROR: clipboard does not start with sk-ant- (got ${#CLIP} chars)."; exit 1 ;;
  esac
  if [ "${#CLIP}" -lt 80 ]; then
    echo "ERROR: clipboard holds only ${#CLIP} chars - too short for a full key."
    echo "       Re-copy the WHOLE key and rerun."
    exit 1
  fi
  (umask 077; printf '%s' "$CLIP" > "$KEYFILE")
  unset CLIP
  echo "      key read from clipboard and saved (readable only by you)"
fi
ANTHROPIC_API_KEY="$(tr -d '[:space:]' < "$KEYFILE")"
export ANTHROPIC_API_KEY
case "$ANTHROPIC_API_KEY" in
  sk-ant-*) echo "[2/4] API key loaded (${#ANTHROPIC_API_KEY} chars)";;
  *) echo "ERROR: $KEYFILE does not contain a valid key."; exit 1;;
esac

# --- 3. Dependencies ---
REQ=requirements.txt
if "$PY" -c 'import sys;exit(0 if sys.version_info<(3,10) else 1)'; then
  REQ=requirements-py39.txt
  echo "[3/4] Python 3.9 detected - using the 3.9-compatible dependency set"
else
  echo "[3/4] Installing dependencies (quiet, ~1 min first time)..."
fi
"$PY" -m pip install --quiet --user -r "$REQ" 2>&1 \
  | grep -vi "warning\|already satisfied\|consider adding" | head -5
echo "      done"

# --- Optional: residential proxy credentials ---
# Put them in ~/Downloads/byrde-proxy.env as:
#   WEBSHARE_USER=xxxx
#   WEBSHARE_PASS=yyyy
# (or YT_PROXY_URL=http://user:pass@host:port for another provider)
PROXYENV="$(dirname "$0")/.proxy.env"
if [ -s "$PROXYENV" ]; then
  set -a; . "$PROXYENV"; set +a
  if [ -n "${WEBSHARE_USER:-}" ]; then
    echo "      routing transcripts through Webshare residential proxy"
  elif [ -n "${YT_PROXY_URL:-}" ]; then
    echo "      routing transcripts through configured proxy"
  fi
fi

# --- 4. Run ---
echo "[4/4] Running the pipeline against your 43 verified channels..."
echo "      (--dry-run: nothing is texted to you)"
echo
"$PY" run.py --dry-run "$@"
STATUS=$?

echo
echo "=============================================="
if [ $STATUS -eq 0 ]; then
  echo " Finished. Your digest page:"
  echo "   open ~/Downloads/yt-scalper/docs/index.html"
else
  echo " Exited with status $STATUS — copy the output above into the chat."
fi
echo "=============================================="
exit $STATUS
