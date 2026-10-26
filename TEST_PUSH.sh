#!/bin/bash
# Send a test notification to your phone, built from your real digest data.
#   bash ~/byrde/TEST_PUSH.sh
set -u
cd "$(dirname "$0")" || exit 1

[ -s .notify.env ] || { echo "No .notify.env found."; exit 1; }
set -a; . ./.notify.env; set +a
[ -n "${NTFY_TOPIC:-}" ] || { echo "NTFY_TOPIC not set in .notify.env"; exit 1; }
echo "Topic: $NTFY_TOPIC"
echo "(make sure the ntfy app is subscribed to exactly this string)"
echo

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 python3; do
  command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null \
    && { PY="$cand"; break; }
done
[ -z "$PY" ] && { echo "No Python 3.9+ found."; exit 1; }

"$PY" - <<'PYEOF'
import os, sys, json
sys.path.insert(0, ".")
import urllib.request
from scalper import analyze, catalysts as cat, config as cfgmod, notify, signals, store

c = cfgmod.load()
v = store.recent_videos(days=7)
agg = analyze.aggregate(v, c.watchlist, cfgmod.consensus_tiers(c))
byt = cat.by_ticker(store.recent_catalysts(days=7))
sig = signals.compute(v, agg, byt)
msg, keys = notify.build_signal_alert(sig, c.site_url)

if not msg:
    # nothing crosses thresholds right now - show the shape anyway
    top = agg["multi_channel"][:3]
    msg = ("TEST — nothing crosses the alert thresholds this minute.\n"
           + "Current top by independent agreement:\n"
           + "\n".join("%s %s, %d channels, evidence %s"
                       % (t["ticker"], t["consensus"],
                          len(t["corroborating_channels"]), t["evidence_counts"])
                       for t in top))

topic = os.environ["NTFY_TOPIC"]
req = urllib.request.Request(
    "https://ntfy.sh/%s" % topic,
    data=msg.encode("utf-8"),
    headers={"Title": "Byrde test alert",
             "Tags": "chart_with_upwards_trend",
             "Priority": "default"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        print("ntfy accepted it (HTTP %d). Check your phone." % r.status)
except Exception as e:
    print("Failed to reach ntfy: %s: %s" % (type(e).__name__, e))
    sys.exit(1)

print()
print("--- what was sent ---")
print(msg)
PYEOF
