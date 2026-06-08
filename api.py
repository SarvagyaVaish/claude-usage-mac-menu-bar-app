import getpass
import json
import subprocess
from pathlib import Path

import certifi
import requests

_BASE = "https://api.anthropic.com"
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


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
        verify=certifi.where(),
    )


def fetch_rate_limits(oauth_token: str) -> dict:
    """Send a 1-token message and parse the rate-limit response headers."""
    print("[api] POST /v1/messages (rate limits)", flush=True)
    try:
        resp = _post_messages(oauth_token)
        print(f"[api] status={resp.status_code}", flush=True)
        if resp.status_code == 401:
            raise requests.HTTPError("401", response=resp)
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
