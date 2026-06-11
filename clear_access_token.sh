#!/usr/bin/env bash
# Invalidates only the short-lived access token, leaving the refresh token intact.
# Simulates normal token expiry — the "Refresh Auth" button should silently recover.

set -euo pipefail

KEYCHAIN_SERVICE="Claude Code-credentials"
CREDENTIALS_FILE="$HOME/.claude/.credentials.json"
USERNAME=$(whoami)

echo "Clearing access token (preserving refresh token)..."

_corrupt_access_token() {
    # Replace accessToken value with an invalid string
    jq '.accessToken = "expired"' "$1"
}

# ── Keychain ──────────────────────────────────────────────────────────────────
echo "  [keychain] security find-generic-password -s \"$KEYCHAIN_SERVICE\" -a \"$USERNAME\""
if security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$USERNAME" &>/dev/null; then
    echo "  [keychain] security find-generic-password -s \"$KEYCHAIN_SERVICE\" -a \"$USERNAME\" -w"
    BLOB=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$USERNAME" -w)
    UPDATED=$(echo "$BLOB" | jq -c 'if .accessToken then .accessToken = "expired"
                                  elif (to_entries | map(select(.value.accessToken)) | length) > 0
                                  then with_entries(if .value.accessToken then .value.accessToken = "expired" else . end)
                                  else . end')
    security add-generic-password -U \
        -s "$KEYCHAIN_SERVICE" -a "$USERNAME" \
        -w "$UPDATED"
    echo "  [keychain] access token invalidated"
else
    echo "  [keychain] not found, skipping"
fi

# ── Credentials file ──────────────────────────────────────────────────────────
if [ -f "$CREDENTIALS_FILE" ]; then
    UPDATED=$(jq 'if .accessToken then .accessToken = "expired"
                  elif (to_entries | map(select(.value.accessToken)) | length) > 0
                  then with_entries(if .value.accessToken then .value.accessToken = "expired" else . end)
                  else . end' "$CREDENTIALS_FILE")
    echo "$UPDATED" > "$CREDENTIALS_FILE"
    echo "  [file] access token invalidated"
else
    echo "  [file] not found, skipping"
fi

echo "Done. The app should now get a 401, then 'Refresh Auth' can silently recover using the refresh token."
