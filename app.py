#!/usr/bin/env python3
import threading
import time
from datetime import datetime

import AppKit
import rumps

import api

REFRESH_INTERVAL = 5 * 60  # seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt_reset(unix_ts: float) -> str:
    """'23m (3:00 PM)'  /  '3h 4m (6:00 PM)'  /  '2d 3h (Mon 9:00 AM)'"""
    if not unix_ts:
        return "unknown"
    delta = unix_ts - time.time()
    if delta <= 0:
        return "now"
    mins  = int(delta / 60)
    hours = mins // 60
    days  = hours // 24
    dt    = datetime.fromtimestamp(unix_ts)
    if days >= 1:
        return f"{days}d {hours % 24}h  ({dt.strftime('%a %-I:%M %p')})"
    if hours >= 1:
        return f"{hours}h {mins % 60}m  ({dt.strftime('%-I:%M %p')})"
    return f"{mins}m  ({dt.strftime('%-I:%M %p')})"


def _pct_bar(pct: float) -> str:
    """'████░░░░░░  72%'"""
    filled = int(round(pct / 10))
    return "█" * filled + "░" * (10 - filled) + f"  {pct:.0f}%"


def _fmt_countdown(unix_ts: float) -> str:
    """Compact countdown for the menu bar: '23m', '3h 4m', '2d 3h'"""
    if not unix_ts:
        return "—"
    delta = unix_ts - time.time()
    if delta <= 0:
        return "now"
    mins  = int(delta / 60)
    hours = mins // 60
    days  = hours // 24
    if days >= 1:
        return f"{days}d {hours % 24}h"
    if hours >= 1:
        return f"{hours}h {mins % 60}m"
    return f"{mins}m"


def _set_status_title(status_item, line1: str, line2: str):
    if status_item is None:
        return
    try:
        btn = status_item.button()

        # Detect dark menu bar so we pick readable colors
        try:
            best = btn.effectiveAppearance().bestMatchFromAppearancesWithNames_(
                [AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameAqua]
            )
            is_dark = (best == AppKit.NSAppearanceNameDarkAqua)
        except Exception:
            is_dark = False

        if is_dark:
            color1 = AppKit.NSColor.whiteColor()
            color2 = AppKit.NSColor.colorWithWhite_alpha_(0.7, 1.0)
        else:
            color1 = AppKit.NSColor.blackColor()
            color2 = AppKit.NSColor.colorWithWhite_alpha_(0.35, 1.0)

        font1 = AppKit.NSFont.menuBarFontOfSize_(11)
        font2 = AppKit.NSFont.menuBarFontOfSize_(9)

        as1 = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            line1, {AppKit.NSFontAttributeName: font1,
                    AppKit.NSForegroundColorAttributeName: color1})
        as2 = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            line2, {AppKit.NSFontAttributeName: font2,
                    AppKit.NSForegroundColorAttributeName: color2}) if line2 else None

        w = max(as1.size().width, as2.size().width if as2 else 0) + 8
        h = 22.0  # standard menu bar height

        img = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(w, h))
        img.lockFocus()
        as1.drawAtPoint_(AppKit.NSMakePoint((w - as1.size().width) / 2, 10))
        if as2:
            as2.drawAtPoint_(AppKit.NSMakePoint((w - as2.size().width) / 2, 1))
        img.unlockFocus()

        btn.setTitle_("")
        btn.setImage_(img)
        btn.setImagePosition_(AppKit.NSImageOnly)

        print(f"[title] set: {line1!r} / {line2!r}", flush=True)
    except Exception as e:
        import traceback
        print(f"[title] ERROR: {e}", flush=True)
        traceback.print_exc()


# ── app ───────────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("Claude", title="···", quit_button="Quit")

        self._5h_bar_item    = rumps.MenuItem("5h window:  —")
        self._5h_reset_item  = rumps.MenuItem("Resets in:  —")
        self._7d_bar_item    = rumps.MenuItem("7d window:  —")
        self._7d_reset_item  = rumps.MenuItem("Resets in:  —")
        self._updated_item   = rumps.MenuItem("Updated: never")
        self._refresh_item   = rumps.MenuItem("Refresh", callback=self._on_refresh)

        self.menu = [
            self._5h_bar_item,
            self._5h_reset_item,
            None,
            self._7d_bar_item,
            self._7d_reset_item,
            None,
            self._updated_item,
            None,
            self._refresh_item,
        ]

        # Prevent AppKit from re-disabling items that have no action at menu-open time
        self.menu._menu.setAutoenablesItems_(False)

        self._pending       = None
        self._pending_error = None
        self._fetching      = False
        self._fetch_lock    = threading.Lock()

        self._5h_reset_ts   = 0.0
        self._7d_reset_ts   = 0.0
        self._title_line1   = "···"

        self._ui_timer      = rumps.Timer(self._apply_pending, 1)
        self._ui_timer.start()
        self._refresh_timer = rumps.Timer(self._auto_refresh, REFRESH_INTERVAL)
        self._refresh_timer.start()

        threading.Thread(target=self._fetch, daemon=True).start()

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_refresh(self, _):
        self._title_line1 = "···"
        threading.Thread(target=self._fetch, daemon=True).start()

    def _auto_refresh(self, _):
        threading.Thread(target=self._fetch, daemon=True).start()

    # ── UI update (main thread via timer) ─────────────────────────────────────

    def _apply_pending(self, _):
        try:
            self._apply_pending_inner()
        except Exception as e:
            import traceback
            print(f"[apply] ERROR: {e}", flush=True)
            traceback.print_exc()

    def _apply_pending_inner(self):
        if self._pending_error is not None:
            err, self._pending_error = self._pending_error, None
            self._title_line1 = "err"
            self._5h_bar_item.title = f"Error: {err[:60]}"
            self._5h_reset_item.title = ""

        elif self._pending is not None:
            limits = self._pending
            self._pending = None

            pct_5h = limits["5h_pct"]
            status = limits.get("5h_status", "")
            prefix = "⚠ " if status in ("throttled", "limited") else ""
            self._title_line1 = f"{prefix}{pct_5h:.0f}%"

            self._5h_reset_ts = limits["5h_reset_ts"]
            self._7d_reset_ts = limits["7d_reset_ts"]

            self._5h_bar_item.title  = f"5h window:   {_pct_bar(pct_5h)}"
            self._7d_bar_item.title  = f"7d window:   {_pct_bar(limits['7d_pct'])}"
            self._updated_item.title = f"Updated: {datetime.now().strftime('%-I:%M %p')}"

        # Live countdowns — updated every tick
        if self._5h_reset_ts:
            self._5h_reset_item.title = f"Resets in:   {_fmt_reset(self._5h_reset_ts)}"
        if self._7d_reset_ts:
            self._7d_reset_item.title = f"Resets in:   {_fmt_reset(self._7d_reset_ts)}"

        # Two-line menu bar title — updated every tick
        nsapp = getattr(self, "_nsapp", None)
        _set_status_title(
            getattr(nsapp, "nsstatusitem", None),
            self._title_line1,
            _fmt_countdown(self._5h_reset_ts),
        )

    # ── background fetch ──────────────────────────────────────────────────────

    def _fetch(self):
        with self._fetch_lock:
            if self._fetching:
                print("[fetch] already in progress, skipping", flush=True)
                return
            self._fetching = True
        try:
            import time
            print("[fetch] reading oauth credentials", flush=True)
            creds = api.read_credentials()
            if not creds:
                print("[fetch] ERROR: no oauth token found", flush=True)
                self._pending_error = "Claude Code not found — install it first"
                return
            # Proactively refresh if the token expires within the next 5 minutes
            expires_at = creds.get("expiresAt", 0)
            if expires_at and time.time() * 1000 > expires_at - 5 * 60 * 1000:
                print("[fetch] token expiring soon, refreshing proactively", flush=True)
                refresh_tok = creds.get("refreshToken")
                if refresh_tok:
                    fresh = api.refresh_oauth_token(refresh_tok)
                    if fresh:
                        creds["accessToken"] = fresh
            oauth = creds["accessToken"]
            print(f"[fetch] got token ({oauth[:12]}…)", flush=True)
            limits = api.fetch_rate_limits(oauth)
            print(f"[fetch] success: {limits}", flush=True)
            self._pending = limits
        except Exception as e:
            import traceback
            print(f"[fetch] ERROR: {e}", flush=True)
            traceback.print_exc()
            self._pending_error = str(e)
        finally:
            with self._fetch_lock:
                self._fetching = False


if __name__ == "__main__":
    ClaudeUsageApp().run()
