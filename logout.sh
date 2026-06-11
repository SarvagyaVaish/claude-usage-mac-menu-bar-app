#!/usr/bin/env bash
# Deletes the Claude Code auth token from keychain and credentials file.
# Use this to test the "Refresh Auth" button in the menu bar app.

set -euo pipefail

KEYCHAIN_SERVICE="Claude Code-credentials"
CREDENTIALS_FILE="$HOME/.claude/.credentials.json"
USERNAME=$(whoami)

echo "Clearing Claude Code auth token..."

# Remove from keychain (ignore error if not present)
echo "  [keychain] security find-generic-password -s \"$KEYCHAIN_SERVICE\" -a \"$USERNAME\""
if security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$USERNAME" &>/dev/null; then
    echo "  [keychain] security delete-generic-password -s \"$KEYCHAIN_SERVICE\" -a \"$USERNAME\""
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "$USERNAME"
    echo "  [keychain] deleted"
else
    echo "  [keychain] not found, skipping"
fi

# Remove credentials file (ignore error if not present)
if [ -f "$CREDENTIALS_FILE" ]; then
    rm "$CREDENTIALS_FILE"
    echo "  [file] $CREDENTIALS_FILE deleted"
else
    echo "  [file] not found, skipping"
fi

echo "Done. The menu bar app should now show no session."
