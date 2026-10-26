#!/bin/bash
# Put this project on GitHub so Pages can serve your digest 24/7.
#
#   bash ~/Downloads/yt-scalper/PUSH_TO_GITHUB.sh YOUR_GITHUB_USERNAME
#
# Run it from YOUR Terminal (git needs to work on your real filesystem).
set -u
cd "$(dirname "$0")" || exit 1

USER_NAME="${1:-}"
REPO="${2:-yt-scalper}"
if [ -z "$USER_NAME" ]; then
  echo "Usage: bash PUSH_TO_GITHUB.sh YOUR_GITHUB_USERNAME [repo-name]"
  exit 1
fi

command -v git >/dev/null || { echo "git not found."; exit 1; }

# Safety: make sure no credential file ever gets committed.
for bad in anthropic-key.txt byrde-proxy.env; do
  if [ -e "$bad" ]; then
    echo "REFUSING: $bad is inside the project folder. Move it to ~/Downloads/ first."
    exit 1
  fi
done

if [ ! -d .git ]; then
  git init -q
  git branch -M main
  echo "[1/4] git repo initialised"
else
  echo "[1/4] git repo already present"
fi

git add -A
if git diff --staged --quiet; then
  echo "[2/4] nothing new to commit"
else
  git -c user.name="$USER_NAME" -c user.email="$USER_NAME@users.noreply.github.com" \
      commit -q -m "The Byrde scalper: 46 verified channels, hourly digest"
  echo "[2/4] committed"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/$USER_NAME/$REPO.git"
  echo "[3/4] remote set to github.com/$USER_NAME/$REPO"
else
  echo "[3/4] remote already set: $(git remote get-url origin)"
fi

echo "[4/4] pushing..."
if git push -u origin main; then
  echo
  echo "=============================================="
  echo " Pushed. Now, in the repo on github.com:"
  echo
  echo " 1. Settings -> Secrets and variables -> Actions"
  echo "      New secret: ANTHROPIC_API_KEY  = your sk-ant-... key"
  echo "      New secret: NTFY_TOPIC         = an unguessable string you invent"
  echo
  echo " 2. Settings -> Pages"
  echo "      Source: Deploy from a branch"
  echo "      Branch: main     Folder: /docs      Save"
  echo
  echo " 3. Your site (live in ~1 min):"
  echo "      https://$USER_NAME.github.io/$REPO/"
  echo
  echo " 4. Actions tab -> Scalper Digest -> Run workflow (to test it)"
  echo "=============================================="
else
  RC=$?
  echo
  echo "Push failed (exit $RC). The two usual causes:"
  echo
  echo "1. AUTHENTICATION. GitHub stopped accepting account passwords for"
  echo "   git in 2021. If you saw \"Password authentication is not"
  echo "   supported\", the repo is fine - you need a Personal Access Token:"
  echo "     - https://github.com/settings/tokens"
  echo "     - Generate new token (classic), tick the  repo  scope"
  echo "     - Run this script again; paste the TOKEN at the Password prompt"
  echo "   (credential.helper is set to osxkeychain, so you do this once.)"
  echo
  echo "2. The repo really does not exist. Create it at https://github.com/new"
  echo "     name: $REPO     visibility: Public     do NOT add a README"
fi
