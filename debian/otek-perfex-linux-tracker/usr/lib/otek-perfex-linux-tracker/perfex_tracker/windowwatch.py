# -*- coding: utf-8 -*-
"""
windowwatch.py
Polls the focused window's class/title/pid via the window-calls-extended
GNOME Shell extension's D-Bus interface
(org.gnome.Shell.Extensions.WindowsExt), and turns focus changes into
closed aggregator.Segment objects.

window-calls-extended (https://github.com/hseliger/window-calls-extended)
is a separate, user-installed GNOME Shell extension - never packaged in
apt, not bundled with GNOME. Its absence (not installed, disabled, or a
GNOME-version API break - its own README notes it needed reworking for
GNOME 45's import changes) must degrade gracefully with a clear, actionable
message, never a crash or a silent no-op. health_check() exists precisely
for that: call it before ever letting the user click Start.

Deliberately Wayland-only, via this GNOME Shell D-Bus extension - no X11
fallback (see the plan's Context section for why: Ubuntu is moving away
from X11 sessions, and building/maintaining a second detection path for a
dying session type isn't worth it for an internal tool).

Uses plain `gdbus` subprocess calls rather than a Python D-Bus binding
library, matching this project's stdlib-only ethos elsewhere (see api.py's
docstring) - gdbus ships with GLib, is already a hard runtime dependency
of any GNOME desktop, and needs no extra packaging.
"""

import re
import subprocess
import time

from .aggregator import Segment

BUS_DEST = "org.gnome.Shell"
OBJECT_PATH = "/org/gnome/Shell/Extensions/WindowsExt"
INTERFACE = "org.gnome.Shell.Extensions.WindowsExt"
CALL_TIMEOUT_SECONDS = 2

INSTALL_MESSAGE = (
    "The window-calls-extended GNOME Shell extension isn't installed or "
    "enabled. Install it from extensions.gnome.org, or from "
    "https://github.com/hseliger/window-calls-extended, then restart "
    "GNOME Shell (Alt+F2, r, Enter on X11 sessions; log out/in on "
    "Wayland) and try again."
)

# Known window classes reported by window-calls-extended for the two
# browsers this app integrates with over the local tab-focus HTTP server
# (see tabserver.py) - verify these empirically on the target system
# (`gdbus call ... FocusClass` while each browser has focus) before
# relying on them; window manager/GTK theme differences can affect the
# exact string.
BROWSER_WINDOW_CLASSES = {"google-chrome", "chromium", "firefox"}


class WindowWatchError(Exception):
    """Raised by health_check() when window-calls-extended isn't usable.
    `.actionable` is always a user-presentable message - show it directly
    rather than a raw exception string."""

    def __init__(self, message, actionable=None):
        super().__init__(message)
        self.actionable = actionable or message


def health_check():
    """Raises WindowWatchError with an actionable message if
    window-calls-extended isn't reachable; returns silently on success.
    Call this before enabling the Start button - never start a session
    that can't record anything."""
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session", "--dest", BUS_DEST,
             "--object-path", OBJECT_PATH, "--method", INTERFACE + ".FocusClass"],
            capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise WindowWatchError(
            "gdbus not found.",
            "gdbus (part of GLib) is required and wasn't found on this system. "
            "Is this a GNOME desktop?",
        )
    except subprocess.TimeoutExpired:
        raise WindowWatchError("gdbus call timed out.", INSTALL_MESSAGE)

    if result.returncode != 0:
        stderr = result.stderr or ""
        if "UnknownMethod" in stderr or "UnknownObject" in stderr or "UnknownInterface" in stderr:
            raise WindowWatchError("window-calls-extended not available.", INSTALL_MESSAGE)
        raise WindowWatchError("gdbus call failed: %s" % stderr.strip(), INSTALL_MESSAGE)


def _gdbus_call(method):
    result = subprocess.run(
        ["gdbus", "call", "--session", "--dest", BUS_DEST,
         "--object-path", OBJECT_PATH, "--method", INTERFACE + "." + method],
        capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None
    # gdbus prints a GVariant tuple literal, e.g. ("Some Title",) or
    # ("google-chrome",) - strip the wrapping and quoting rather than
    # pulling in a GVariant parser for a single string value.
    match = re.match(r"^\('(.*)',?\)\s*$", result.stdout.strip())
    return match.group(1) if match else result.stdout.strip()


def poll_focus():
    """Returns (window_class, title, pid) for the currently focused
    window, or (None, None, None) if the call fails (e.g. the extension
    was disabled mid-session - callers should treat this as "no change"
    rather than closing a segment on a transient failure)."""
    window_class = _gdbus_call("FocusClass")
    if window_class is None:
        return None, None, None
    title = _gdbus_call("FocusTitle")
    pid = _gdbus_call("FocusPID")
    return window_class, title, pid


def is_browser_class(window_class):
    return bool(window_class) and window_class.lower() in BROWSER_WINDOW_CLASSES


class FocusPoller:
    """Owns the "what's currently focused" state across polls and turns
    focus changes into closed Segment objects. Call poll() on a
    GLib.timeout_add_seconds() cadence from the GTK main loop (see ui.py)
    - no threading here, so no thread-safety concerns with SQLite/GTK.
    """

    def __init__(self, on_segment_closed):
        """on_segment_closed: callable(aggregator.Segment) - called
        immediately when a window-sourced segment closes (browser-tab
        segments are closed by tabserver.py instead, via the same
        callback, when tab-focus events arrive)."""
        self._on_segment_closed = on_segment_closed
        self._current = None   # (window_class, title, pid, start_time) or None
        self._browser_deferred_since = None   # start_time of the current browser-deferral, if any
        self._browser_deferred_class = None   # which browser window class is deferred
        self._browser_had_tab_data = False    # did tabserver.py record >=1 real segment during this deferral?

    def poll(self):
        window_class, title, pid = poll_focus()
        if window_class is None:
            return  # transient failure - treat as no change, don't close anything

        now = int(time.time())

        if is_browser_class(window_class):
            if self._browser_deferred_since is not None and self._browser_deferred_class == window_class:
                return  # still on the same browser - no boundary crossed
            self._close_current(now)
            self._close_browser_deferral(window_class, now)
            self._browser_deferred_since = now
            self._browser_deferred_class = window_class
            self._browser_had_tab_data = False
            return

        title = title or ""
        if self._current is not None:
            last_class, last_title, last_pid, start_time = self._current
            if last_class == window_class and last_title == title and last_pid == pid:
                return  # same window/file still focused - no boundary crossed
            self._close_current(now)

        self._close_browser_deferral(window_class, now)
        self._current = (window_class, title, pid, now)

    def is_browser_deferred(self):
        return self._browser_deferred_since is not None

    def current_browser_class(self):
        """The currently-deferred browser's window class, or None if no
        browser is currently focused. Used by tabserver.py (via ui.py's
        wiring) to tag emitted browser-tab Segments with the right
        app_class, since tabserver.py only ever sees {title, url} from
        the extension, never the window class itself."""
        return self._browser_deferred_class

    def note_tab_segment_recorded(self):
        """Called (by ui.py's shared on_segment_closed callback) whenever
        tabserver.py successfully closes a real browser-tab segment -
        marks the current browser deferral as having real data, so the
        window-level fallback below doesn't also fire for the same span.

        Called from tabserver's background HTTP-handler thread, while
        poll() runs on the GTK main thread - both touch
        _browser_had_tab_data. Deliberately left unlocked: it's a single
        bool flag only ever set True (never back to False except by
        poll()/stop() themselves), so under CPython's GIL a torn read
        isn't possible - at worst a flag-set loses a benign race against a
        poll() happening in the same instant, which just means one extra
        harmless fallback segment gets logged, not data loss or a crash."""
        self._browser_had_tab_data = True

    def _close_current(self, end_time):
        if self._current is None:
            return
        window_class, title, _pid, start_time = self._current
        if end_time > start_time:
            self._on_segment_closed(Segment(
                source="window", app_class=window_class, title=title,
                start_time=start_time, end_time=end_time,
            ))
        self._current = None

    def _close_browser_deferral(self, _next_window_class, end_time):
        """Falls back to logging the deferred period as a plain window
        segment if tabserver.py never reported real tab data for it (e.g.
        the browser extension isn't installed/updated) - never silently
        lose the time just because the richer per-tab path wasn't
        available."""
        if self._browser_deferred_since is None:
            return
        start_time = self._browser_deferred_since
        window_class = self._browser_deferred_class
        had_data = self._browser_had_tab_data
        self._browser_deferred_since = None
        self._browser_deferred_class = None
        self._browser_had_tab_data = False

        if had_data or end_time <= start_time:
            return
        self._on_segment_closed(Segment(
            source="window", app_class=window_class, title="(no tab data)",
            start_time=start_time, end_time=end_time,
        ))

    def stop(self):
        """Call on session Stop to flush whatever's currently open."""
        now = int(time.time())
        self._close_current(now)
        self._close_browser_deferral(None, now)
