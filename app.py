#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import AppKit
import rumps

import api

REFRESH_INTERVAL = 5 * 60  # seconds

LOG_PATH     = os.path.expanduser("~/Library/Logs/ClaudeUsage.log")
LOG_MAX_SIZE = 5 * 1024 * 1024  # rotate past 5 MB


def _setup_logging():
    """Send print() output to the log file when we have nowhere else to write.

    Launched from a terminal, stdout is a tty and we leave it alone. Launched
    with a shell redirect (install.sh), stdout is already a regular file and we
    leave that alone too. Launched from Finder, the Dock, or a login item,
    stdout is /dev/null -- that is the case this exists for, and the one that
    used to silently lose every log line.
    """
    try:
        fd = sys.stdout.fileno()
        if os.stat(os.devnull).st_ino != os.fstat(fd).st_ino:
            return  # a tty, a shell redirect, or a pipe -- all deliberate
    except Exception:
        pass  # no usable stdout at all -- open our own below

    try:
        if os.path.getsize(LOG_PATH) > LOG_MAX_SIZE:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass  # missing or unreadable -- open() below handles it

    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        # utf-8 explicitly: launched from Finder there is no LANG, so the
        # default encoding is ascii and the bar/em-dash glyphs would raise.
        f = open(LOG_PATH, "a", buffering=1,  # line-buffered, so tail -f is live
                 encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f
        print(f"[log] --- started {datetime.now():%Y-%m-%d %H:%M:%S} "
              f"(pid {os.getpid()}) ---", flush=True)
    except OSError:
        pass  # never let logging setup stop the app from launching


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


def _elapsed_pct(unix_ts: float, window_secs: float) -> str:
    """'42% elapsed' for a window ending at unix_ts."""
    if not unix_ts:
        return ""
    elapsed = 1 - (unix_ts - time.time()) / window_secs
    return f"  ·  {max(0, min(100, elapsed * 100)):.0f}% elapsed"


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

        # Skip the redraw when nothing visible has changed (timer fires every 1s)
        cache_key = (line1, line2, is_dark)
        if getattr(_set_status_title, "_last_key", None) == cache_key:
            return
        _set_status_title._last_key = cache_key

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
        self._updated_item      = rumps.MenuItem("Updated: never")
        self._refresh_item      = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._reauth_item       = rumps.MenuItem("Refresh Auth", callback=self._on_reauth)

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
            self._reauth_item,
        ]

        # Prevent AppKit from re-disabling items that have no action at menu-open time
        self.menu._menu.setAutoenablesItems_(False)

        self._pending       = None
        self._pending_error = None
        self._no_session    = False
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

    def _on_reauth(self, _):
        threading.Thread(target=self._reauth_and_refresh, daemon=True).start()

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
        if self._no_session:
            self._title_line1 = "···"
            self._5h_bar_item.title  = "Status will appear once Claude Code starts"
            self._5h_reset_item.title = ""
            self._7d_bar_item.title  = ""
            self._7d_reset_item.title = ""

        elif self._pending_error is not None:
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

        # Live countdowns — updated every tick (skip when no active session)
        if not self._no_session:
            if self._5h_reset_ts:
                self._5h_reset_item.title = f"Resets in:   {_fmt_reset(self._5h_reset_ts)}"
            if self._7d_reset_ts:
                self._7d_reset_item.title = f"Resets in:   {_fmt_reset(self._7d_reset_ts)}{_elapsed_pct(self._7d_reset_ts, 7 * 86400)}"

        # Two-line menu bar title — updated every tick
        nsapp = getattr(self, "_nsapp", None)
        _set_status_title(
            getattr(nsapp, "nsstatusitem", None),
            self._title_line1,
            _fmt_countdown(self._5h_reset_ts) if not self._no_session else "",
        )

    # ── reauth ────────────────────────────────────────────────────────────────

    def _reauth_and_refresh(self):
        self._reauth_item.title = "Refreshing Auth…"
        self._title_line1 = "···"
        try:
            env = os.environ.copy()
            env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:" + env.get("PATH", "")
            claude_bin = shutil.which("claude", path=env["PATH"]) \
                or os.path.expanduser("~/.local/bin/claude")
            print(f"[reauth] running: {claude_bin} -p '2+2'", flush=True)
            result = subprocess.run(
                [claude_bin, "-p", "2+2"],
                capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
            )
            print(f"[reauth] exit={result.returncode} stdout={result.stdout[:80]!r}", flush=True)
            if result.returncode != 0:
                print(f"[reauth] stderr={result.stderr[:200]!r}", flush=True)

            if result.returncode != 0 and "not logged in" in (result.stdout + result.stderr).lower():
                creds = api.read_credentials()
                has_refresh_token = bool(creds and creds.get("refreshToken"))
                print(f"[reauth] has_refresh_token={has_refresh_token}", flush=True)
                if not has_refresh_token:
                    # No refresh token — need full interactive login
                    print("[reauth] opening Terminal for interactive login", flush=True)
                    self._reauth_item.title = "Waiting for login…"
                    as_path = claude_bin.replace("\\", "\\\\").replace('"', '\\"')
                    script = (
                        f'tell application "Terminal"\n'
                        f'  set w to do script "{as_path}"\n'
                        f'  activate\n'
                        f'  repeat\n'
                        f'    delay 2\n'
                        f'    if not busy of w then exit repeat\n'
                        f'  end repeat\n'
                        f'end tell'
                    )
                    subprocess.run(["osascript", "-e", script], timeout=300)
                    print("[reauth] Terminal closed", flush=True)
        except Exception as e:
            print(f"[reauth] ERROR: {e}", flush=True)
        finally:
            self._reauth_item.title = "Refresh Auth"
        # force=True so a concurrent auto-refresh (using the old token) can't
        # cause this post-reauth fetch to be skipped.
        self._fetch(force=True)

    # ── background fetch ──────────────────────────────────────────────────────

    def _fetch(self, force=False):
        # Acquire the "fetching" slot. force=True waits for any in-progress
        # fetch to finish (rather than skipping) so it always runs fresh.
        while True:
            with self._fetch_lock:
                if not self._fetching:
                    self._fetching = True
                    break
                if not force:
                    print("[fetch] already in progress, skipping", flush=True)
                    return
            time.sleep(0.2)
        try:
            print("[fetch] reading oauth credentials", flush=True)
            creds = api.read_credentials()
            if not creds:
                print("[fetch] ERROR: no oauth token found", flush=True)
                self._no_session = True
                return
            oauth = creds["accessToken"]
            print("[fetch] got access token", flush=True)
            limits = api.fetch_rate_limits(oauth)
            print(f"[fetch] success: {limits}", flush=True)
            self._no_session = False
            self._pending = limits
        except Exception as e:
            import traceback
            print(f"[fetch] ERROR: {e}", flush=True)
            traceback.print_exc()
            if "401" in str(e):
                self._no_session = True
            else:
                self._pending_error = str(e)
        finally:
            with self._fetch_lock:
                self._fetching = False


if __name__ == "__main__":
    _setup_logging()
    ClaudeUsageApp().run()
