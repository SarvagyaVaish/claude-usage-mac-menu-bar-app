#!/usr/bin/env python3
import threading
from datetime import datetime, timezone

import rumps

import api
import config

REFRESH_INTERVAL = 300  # seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def _model_label(name: str) -> str:
    """'claude-opus-4-7' → 'Opus 4.7', etc."""
    if not name:
        return "unknown"
    stripped = name.replace("claude-", "")
    parts = stripped.split("-")
    # drop date suffixes like '20251001'
    parts = [p for p in parts if not (len(p) == 8 and p.isdigit())]
    if len(parts) >= 3:
        return f"{parts[0].capitalize()} {parts[1]}.{parts[2]}"
    return name


def _summarize_costs(data: list) -> tuple[float, float]:
    """Returns (today_usd, month_usd) from cost report bucket list."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    today_c = month_c = 0.0
    for bucket in data:
        d = bucket.get("starting_at", "")
        bucket_sum = sum(float(r.get("amount", 0)) for r in bucket.get("results", []))
        if d[:10] == today:
            today_c += bucket_sum
        if d[:7] == month:
            month_c += bucket_sum
    # amounts are in cents (lowest USD units)
    return today_c / 100, month_c / 100


def _summarize_usage(data: list) -> dict[str, dict]:
    """Returns {model_label: {in: N, out: N}} from usage report bucket list."""
    models: dict[str, dict] = {}
    for bucket in data:
        for r in bucket.get("results", []):
            label = _model_label(r.get("model") or "unknown")
            if label not in models:
                models[label] = {"in": 0, "out": 0}
            cc = r.get("cache_creation") or {}
            total_in = (
                r.get("uncached_input_tokens", 0)
                + r.get("cache_read_input_tokens", 0)
                + cc.get("ephemeral_1h_input_tokens", 0)
                + cc.get("ephemeral_5m_input_tokens", 0)
            )
            models[label]["in"] += total_in
            models[label]["out"] += r.get("output_tokens", 0)
    return models


# ── app ───────────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("Claude", title="···", quit_button="Quit")

        self._today_item = rumps.MenuItem("Today: —")
        self._month_item = rumps.MenuItem("This month: —")
        self._tokens_item = rumps.MenuItem("Tokens today")
        self._updated_item = rumps.MenuItem("Updated: never")
        self._refresh_item = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._set_key_item = rumps.MenuItem("Set API Key…", callback=self._on_set_key)

        self.menu = [
            self._today_item,
            self._month_item,
            None,
            self._tokens_item,
            None,
            self._updated_item,
            self._refresh_item,
            None,
            self._set_key_item,
        ]

        # Pending data written by background thread, consumed by UI timer on main thread
        self._pending: tuple | None = None
        self._pending_error: str | None = None
        self._fetching = False
        self._fetch_lock = threading.Lock()

        self._ui_timer = rumps.Timer(self._apply_pending, 1)
        self._ui_timer.start()

        self._refresh_timer = rumps.Timer(self._auto_refresh, REFRESH_INTERVAL)
        self._refresh_timer.start()

        threading.Thread(target=self._fetch, daemon=True).start()

    # ── callbacks (main thread) ───────────────────────────────────────────────

    def _on_refresh(self, _):
        self.title = "···"
        threading.Thread(target=self._fetch, daemon=True).start()

    def _auto_refresh(self, _):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _apply_pending(self, _):
        """Consumes results produced by the background fetch thread."""
        if self._pending_error is not None:
            err = self._pending_error
            self._pending_error = None
            self.title = "err"
            self._today_item.title = f"Error: {err[:50]}"
            self._month_item.title = ""
            return

        if self._pending is None:
            return

        today_usd, month_usd, model_tokens = self._pending
        self._pending = None

        self.title = f"${today_usd:.2f}"
        self._today_item.title = f"Today:       ${today_usd:.2f}"
        self._month_item.title = f"This month: ${month_usd:.2f}"
        self._updated_item.title = f"Updated: {datetime.now().strftime('%-I:%M %p')}"

        # Rebuild tokens submenu
        for key in list(self._tokens_item.keys()):
            del self._tokens_item[key]

        if model_tokens:
            for label, counts in sorted(model_tokens.items()):
                title = f"{label}:  {_fmt_tokens(counts['in'])} in · {_fmt_tokens(counts['out'])} out"
                self._tokens_item[title] = rumps.MenuItem(title)
        else:
            self._tokens_item["No token data yet"] = rumps.MenuItem("No token data yet")

    def _on_set_key(self, _):
        w = rumps.Window(
            title="Anthropic Admin API Key",
            message="Enter your Admin API key (starts with sk-ant-admin…):",
            default_text=config.get_key() or "",
            ok="Save",
            cancel="Cancel",
            dimensions=(380, 22),
        )
        resp = w.run()
        if resp.clicked == 1 and resp.text.strip():
            config.set_key(resp.text.strip())
            self.title = "···"
            threading.Thread(target=self._fetch, daemon=True).start()

    # ── background fetch ──────────────────────────────────────────────────────

    def _fetch(self):
        with self._fetch_lock:
            if self._fetching:
                return
            self._fetching = True
        try:
            key = config.get_key()
            if not key:
                self._pending_error = "No API key — click 'Set API Key…'"
                return

            cost_data = api.fetch_cost_report(key)
            today_usd, month_usd = _summarize_costs(cost_data)

            usage_data = api.fetch_usage_today(key)
            model_tokens = _summarize_usage(usage_data)

            self._pending = (today_usd, month_usd, model_tokens)
        except Exception as e:
            self._pending_error = str(e)
        finally:
            with self._fetch_lock:
                self._fetching = False


if __name__ == "__main__":
    ClaudeUsageApp().run()
