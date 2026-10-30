#!/bin/bash
# Push without typing into an invisible password prompt.
#
#   1. Put your GitHub token in a file you can SEE while you paste:
#        nano ~/.byrde-gh-token        (paste, then Ctrl-O Enter Ctrl-X)
#      or open it in TextEdit. One line, just the token.
#   2. bash PUSH.sh
#
# The token lives OUTSIDE this repository, so it can never be committed.
# It is never printed, never echoed, and never written to shell history.
# After the first successful push the macOS keychain remembers it and
# you can delete the file.
set -u
cd "$(dirname "$0")" || exit 1

TOKENFILE="$HOME/.byrde-gh-token"
USERNAME="mateopaguio"

if [ ! -s "$TOKENFILE" ]; then
  cat <<MSG
No token file found at ~/.byrde-gh-token

  1. Create a fine-grained token:
       https://github.com/settings/personal-access-tokens/new
       Repository access : Only select repositories -> mateopaguio/yt-scalper
       Permissions       : Contents -> Read and write
       (nothing else needs to be enabled)

  2. Save it where you can see what you are pasting:
       nano ~/.byrde-gh-token
       paste, then Ctrl-O  Enter  Ctrl-X

  3. Run this script again.
MSG
  exit 1
fi

chmod 600 "$TOKENFILE" 2>/dev/null

# strip whitespace/newlines a paste may add
TOKEN="$(tr -d ' \t\r\n' < "$TOKENFILE")"
if [ -z "$TOKEN" ]; then
  echo "The token file is empty."; exit 1
fi
case "$TOKEN" in
  github_pat_*|ghp_*) : ;;
  *) echo "That does not look like a GitHub token (expected github_pat_... or ghp_...)."
     echo "Make sure you saved the TOKEN, not your account password."; exit 1 ;;
esac

# Feed the credential to git without any prompt and without echoing it.
ASKPASS="$(mktemp)"
trap 'rm -f "$ASKPASS"' EXIT
cat > "$ASKPASS" <<ASK
#!/bin/bash
case "\$1" in
  *Username*) printf '%s' "$USERNAME" ;;
  *)          printf '%s' "$TOKEN" ;;
esac
ASK
chmod 700 "$ASKPASS"
export GIT_ASKPASS="$ASKPASS"
export GIT_TERMINAL_PROMPT=0

echo "Pushing feature/byrde-terminal (main is left untouched)..."
if git push -u origin feature/byrde-terminal; then
  echo
  echo "=============================================="
  echo " Pushed. Review it here:"
  echo "   https://github.com/$USERNAME/yt-scalper/tree/feature/byrde-terminal"
  echo
  echo " Open a pull request against main:"
  echo "   https://github.com/$USERNAME/yt-scalper/compare/main...feature/byrde-terminal"
  echo
  echo " main is unchanged on GitHub, as intended."
  echo " The keychain has the token now - you can delete the file:"
  echo "   rm ~/.byrde-gh-token"
  echo "=============================================="
else
  RC=$?
  echo
  echo "Push failed (exit $RC)."
  echo "  'Invalid username or token' -> the token is wrong, expired, or"
  echo "  lacks Contents: Read and write on mateopaguio/yt-scalper."
  echo "  Generate a new one and overwrite ~/.byrde-gh-token."
  exit $RC
fi
