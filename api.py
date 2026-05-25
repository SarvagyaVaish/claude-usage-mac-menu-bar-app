import requests
from datetime import datetime, timezone, timedelta

_BASE = "https://api.anthropic.com"
_COMMON_HEADERS = {
    "anthropic-version": "2023-06-01",
    "User-Agent": "claude-usage-menu/1.0",
}


def _headers(key: str) -> dict:
    return {**_COMMON_HEADERS, "x-api-key": key}


def _paginate(url: str, params: dict, key: str) -> list:
    rows = []
    while True:
        r = requests.get(url, params=params, headers=_headers(key), timeout=15)
        r.raise_for_status()
        body = r.json()
        rows.extend(body.get("data", []))
        if not body.get("has_more"):
            break
        params = {**params, "page": body["next_page"]}
    return rows


def fetch_cost_report(key: str, days: int = 31) -> list:
    """Daily cost buckets for the last `days` days."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return _paginate(
        f"{_BASE}/v1/organizations/cost_report",
        {"starting_at": start, "ending_at": end, "bucket_width": "1d"},
        key,
    )


def fetch_usage_today(key: str) -> list:
    """Today's token usage grouped by model (UTC day)."""
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return _paginate(
        f"{_BASE}/v1/organizations/usage_report/messages",
        {
            "starting_at": start,
            "ending_at": end,
            "bucket_width": "1d",
            "group_by[]": "model",
        },
        key,
    )
