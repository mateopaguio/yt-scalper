#!/bin/bash
# Everything in one go: hourly runs + GitHub + Pages.
#   bash ~/Downloads/yt-scalper/SETUP_ALL.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HERE_KEY="$HERE/.anthropic-key"
cd "$HERE" || exit 1

echo "=============================================="
echo " The Byrde - full setup"
echo "=============================================="
echo

# ---------- 1. key ----------
if [ ! -s "$HERE_KEY" ]; then
  echo "No API key saved yet. Run this first, then come back:"
  echo "    bash $HERE/START_HERE.sh"
  exit 1
fi
echo "[1/3] API key found"

# ---------- 2. hourly job ----------
LABEL="com.byrde.scalper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HERE/logs"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$HERE/START_HERE.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HERE/logs/run.log</string>
  <key>StandardErrorPath</key><string>$HERE/logs/error.log</string>
  <key>WorkingDirectory</key><string>$HERE</string>
</dict>
</plist>
PLISTEOF
launchctl unload "$PLIST" 2>/dev/null
if launchctl load "$PLIST" 2>/dev/null; then
  echo "[2/3] hourly job installed and started"
else
  echo "[2/3] could not load the job - run: launchctl load $PLIST"
fi

# ---------- 3. github ----------
echo
echo "[3/3] GitHub (gives you the 24/7 website + phone notifications)"
echo "      Leave blank and press Return to skip."
printf "      Your GitHub username: "
read -r GH_USER
if [ -z "${GH_USER:-}" ]; then
  echo "      skipped - your digest is still at $HERE/docs/index.html"
else
  REPO="yt-scalper"
  for bad in anthropic-key.txt byrde-proxy.env; do
    [ -e "$bad" ] && { echo "REFUSING: $bad is inside the project. Move it to ~/Downloads/."; exit 1; }
  done
  [ -d .git ] || { git init -q; git branch -M main; }
  git add -A
  git diff --staged --quiet || git \
      -c user.name="$GH_USER" -c user.email="$GH_USER@users.noreply.github.com" \
      commit -q -m "The Byrde scalper: 46 verified channels, hourly digest"
  git remote get-url origin >/dev/null 2>&1 || \
      git remote add origin "https://github.com/$GH_USER/$REPO.git"
  echo "      pushing to github.com/$GH_USER/$REPO ..."
  PUSH_OUT="$(git push -u origin main 2>&1)"; PUSH_RC=$?
  echo "$PUSH_OUT" | tail -3
  if [ $PUSH_RC -eq 0 ]; then
    echo
    echo "      Next, on github.com/$GH_USER/$REPO :"
    echo "        Settings > Secrets and variables > Actions > New secret"
    echo "            ANTHROPIC_API_KEY = your sk-ant-... key"
    echo "        Settings > Pages > Deploy from a branch > main > /docs"
    echo "        Site: https://$GH_USER.github.io/$REPO/"
  else
    echo
    echo "      Push failed - create the repo first at https://github.com/new"
    echo "        name: $REPO   Public   no README"
    echo "      then rerun this script."
  fi
fi

echo
echo "=============================================="
echo " Done. Watch it work:"
echo "   tail -f $HERE/logs/run.log"
echo "   open $HERE/docs/index.html"
echo "=============================================="
