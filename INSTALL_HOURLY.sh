#!/bin/bash
# Run the scalper every hour on this Mac, automatically, forever.
# Uses launchd (macOS's scheduler) - survives reboots, no Terminal needed.
#
#   bash ~/Downloads/yt-scalper/INSTALL_HOURLY.sh          # install
#   bash ~/Downloads/yt-scalper/INSTALL_HOURLY.sh --remove # uninstall
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.byrde.scalper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST" 2>/dev/null
  echo "Hourly job removed."
  exit 0
fi

[ -s "$HOME/Downloads/anthropic-key.txt" ] || {
  echo "No API key yet. Run START_HERE.sh once first."; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$HERE/logs"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HERE/START_HERE.sh</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$HERE/logs/run.log</string>
  <key>StandardErrorPath</key><string>$HERE/logs/error.log</string>
  <key>WorkingDirectory</key><string>$HERE</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST" || { echo "Failed to load the job."; exit 1; }

echo "Installed. It runs every hour from now on."
echo
echo "  watch it:     tail -f $HERE/logs/run.log"
echo "  run it now:   launchctl start $LABEL"
echo "  stop it:      bash $HERE/INSTALL_HOURLY.sh --remove"
echo
echo "Note: this runs while your Mac is awake. Asleep, macOS defers the job"
echo "and runs it once on wake - so a closed laptop means a gap, not a loss."
