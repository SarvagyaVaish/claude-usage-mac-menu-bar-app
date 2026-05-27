import getpass
import json
import re
import subprocess
from pathlib import Path

import requests

_BASE = "https://api.anthropic.com"
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def _extract_token(blob: str) -> str | None:
    blob = blob.strip()
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            if isinstance(data.get("accessToken"), str):
                return data["accessToken"]
            for v in data.values():
                if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                    return v["accessToken"]
    except (json.JSONDecodeError, AttributeError):
        pass
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    return m.group(1) if m else None


def read_oauth_token() -> str | None:
    """Read Claude Code's OAuth token from the macOS Keychain."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", getpass.getuser(), "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return _extract_token(out.stdout)
    except Exception:
        pass
    try:
        return _extract_token(_CREDENTIALS_PATH.read_text())
    except Exception:
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
            # Claude Code may have rotated the token — re-read and retry once
            print("[api] 401 on first attempt, re-reading token and retrying", flush=True)
            fresh = read_oauth_token()
            if fresh and fresh != oauth_token:
                resp = _post_messages(fresh)
                print(f"[api] retry status={resp.status_code}", flush=True)
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
