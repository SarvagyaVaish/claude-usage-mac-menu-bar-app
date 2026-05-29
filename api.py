import getpass
import json
import re
import subprocess
import time
from pathlib import Path

import requests

_BASE = "https://api.anthropic.com"
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"
_OAUTH_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"


def _parse_credentials(blob: str) -> dict | None:
    blob = blob.strip()
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            if isinstance(data.get("accessToken"), str):
                return data
            for v in data.values():
                if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                    return v
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def _extract_token(blob: str) -> str | None:
    creds = _parse_credentials(blob)
    return creds["accessToken"] if creds else None


def _read_raw_keychain() -> tuple[str, str] | None:
    """Returns (username, raw_blob) from keychain, or None."""
    username = getpass.getuser()
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", username, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return username, out.stdout
    except Exception:
        return None


def read_credentials() -> dict | None:
    """Return full credentials dict from keychain or credentials file."""
    raw = _read_raw_keychain()
    if raw:
        creds = _parse_credentials(raw[1])
        if creds:
            return creds
    try:
        creds = _parse_credentials(_CREDENTIALS_PATH.read_text())
        if creds:
            return creds
    except Exception:
        pass
    return None


def read_oauth_token() -> str | None:
    """Read Claude Code's OAuth token from the macOS Keychain."""
    creds = read_credentials()
    return creds["accessToken"] if creds else None


def _save_credentials(creds: dict) -> bool:
    """Persist refreshed credentials back to keychain and/or credentials file."""
    saved = False
    raw = _read_raw_keychain()
    if raw:
        username, blob = raw
        try:
            data = json.loads(blob.strip())
            # Update in-place, preserving the outer wrapper key if present
            if isinstance(data.get("accessToken"), str):
                data.update(creds)
                new_blob = json.dumps(data)
            else:
                for k, v in data.items():
                    if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                        data[k].update(creds)
                        break
                new_blob = json.dumps(data)
            subprocess.run(
                ["security", "add-generic-password", "-U",
                 "-s", _KEYCHAIN_SERVICE, "-a", username, "-w", new_blob],
                check=True, capture_output=True, timeout=10,
            )
            saved = True
        except Exception as e:
            print(f"[api] failed to save to keychain: {e}", flush=True)
    try:
        text = _CREDENTIALS_PATH.read_text()
        data = json.loads(text.strip())
        if isinstance(data.get("accessToken"), str):
            data.update(creds)
        else:
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                    data[k].update(creds)
                    break
        _CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))
        saved = True
    except Exception:
        pass
    return saved


def refresh_oauth_token(refresh_token: str) -> str | None:
    """Exchange refresh_token for a new access token; persist and return it."""
    print("[api] refreshing OAuth token via claude.ai", flush=True)
    try:
        resp = requests.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        new_access = data.get("access_token")
        if not new_access:
            print("[api] token refresh response missing access_token", flush=True)
            return None
        new_creds = {"accessToken": new_access}
        if "refresh_token" in data:
            new_creds["refreshToken"] = data["refresh_token"]
        if "expires_in" in data:
            new_creds["expiresAt"] = int(time.time() * 1000) + data["expires_in"] * 1000
        _save_credentials(new_creds)
        print(f"[api] token refreshed successfully ({new_access[:12]}…)", flush=True)
        return new_access
    except Exception as e:
        print(f"[api] token refresh failed: {e}", flush=True)
        return None


def _post_messages(oauth_token: str):
    return requests.post(
        f"{_BASE}/v1/messages",
        headers={
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/json",
            "User-Agent": "claude-usage-menu/1.0",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
        timeout=15,
    )


def fetch_rate_limits(oauth_token: str) -> dict:
    """Send a 1-token message and parse the rate-limit response headers."""
    print("[api] POST /v1/messages (rate limits)", flush=True)
    try:
        resp = _post_messages(oauth_token)
        print(f"[api] status={resp.status_code}", flush=True)
        if resp.status_code == 401:
            print("[api] 401 on first attempt, attempting OAuth token refresh", flush=True)
            creds = read_credentials()
            refresh_tok = creds.get("refreshToken") if creds else None
            fresh = refresh_oauth_token(refresh_tok) if refresh_tok else None
            if fresh:
                resp = _post_messages(fresh)
                print(f"[api] retry status={resp.status_code}", flush=True)
        if resp.status_code == 401:
            raise requests.HTTPError(
                "401 Unauthorized — OAuth token expired. Open Claude Code to refresh it.",
                response=resp,
            )
        resp.raise_for_status()
    except Exception as e:
        print(f"[api] ERROR: {e}", flush=True)
        raise

    h = resp.headers
    rl_headers = {k: v for k, v in h.items() if "ratelimit" in k.lower()}
    print(f"[api] rate-limit headers: {rl_headers}", flush=True)

    def pct(key: str) -> float:
        try:
            return float(h.get(key, 0)) * 100
        except (ValueError, TypeError):
            return 0.0

    def ts(key: str) -> float:
        try:
            return float(h.get(key, 0))
        except (ValueError, TypeError):
            return 0.0

    result = {
        "5h_pct":      pct("anthropic-ratelimit-unified-5h-utilization"),
        "5h_reset_ts": ts("anthropic-ratelimit-unified-5h-reset"),
        "5h_status":   h.get("anthropic-ratelimit-unified-5h-status", ""),
        "7d_pct":      pct("anthropic-ratelimit-unified-7d-utilization"),
        "7d_reset_ts": ts("anthropic-ratelimit-unified-7d-reset"),
    }
    print(f"[api] parsed: {result}", flush=True)
    return result
