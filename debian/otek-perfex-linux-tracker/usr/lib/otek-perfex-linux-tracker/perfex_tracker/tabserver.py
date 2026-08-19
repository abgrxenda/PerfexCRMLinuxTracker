# -*- coding: utf-8 -*-
"""
tabserver.py
A 127.0.0.1-only HTTP server that ingests best-effort tab-focus POSTs from
PerfexCRMChromeExtension/PerfexCRMFirefoxExtension's background.js while a
tracking session is active, giving browser-sourced segments real per-URL
granularity instead of one opaque "Firefox"/"Chrome" window blob.

Runs on a background thread only for the duration of an active session
(started on Start, stopped on Stop) - no listener at all when nothing is
being tracked, minimizing idle attack surface. No auth: 127.0.0.1-only,
low-sensitivity payload (tab titles/URLs, not credentials), but the
handler still validates JSON shape and rejects anything malformed with a
plain 400 - treat this as attacker-adjacent (another local process could
in principle reach it) even though the realistic threat model is "a
misbehaving extension update," not a network attacker.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_PORT = 17845


class TabFocusServer:
    """Owns the current browser-tab segment across incoming /tab-focus
    POSTs, same closed-segment-callback pattern as windowwatch.FocusPoller.
    Only trusts a POST as segment-authoritative when the caller confirms
    (via current_browser_class()) that the currently GNOME-focused window
    is already known to be a browser - see ui.py for how the two are
    wired together. This cross-check is the main integration-correctness
    risk in the whole app and needs explicit manual testing (see the
    plan's Verification section)."""

    def __init__(self, on_segment_closed, current_browser_class, port=DEFAULT_PORT):
        """current_browser_class: callable() -> str or None - the
        currently GNOME-focused browser's window class (e.g. "firefox"),
        or None if focus isn't currently on a browser at all. Used both as
        the authoritative-or-not check and to tag emitted Segments with
        the right app_class (windowwatch.FocusPoller can't do this itself
        since it only ever sees {title, url} from the extension)."""
        self._on_segment_closed = on_segment_closed
        self._current_browser_class = current_browser_class
        self._port = port
        self._current = None  # (url, title, start_time, app_class) or None
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None

    def start(self):
        handler = _make_handler(self)
        self._httpd = HTTPServer(("127.0.0.1", self._port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._close_current(_now())
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        self._httpd = None
        self._thread = None

    def handle_tab_focus(self, url, title, timestamp):
        app_class = self._current_browser_class()
        if app_class is None:
            # A background/inactive browser window fired onActivated/
            # onUpdated, but GNOME's focus isn't actually on a browser
            # right now - informational only, don't treat as a segment
            # boundary (see the module docstring's cross-check note).
            return

        with self._lock:
            now = timestamp or _now()
            if self._current is not None and self._current[0] == url:
                return  # same tab still active - no boundary crossed

            self._close_current(now)
            self._current = (url, title or "", now, app_class)

    def _close_current(self, end_time):
        if self._current is None:
            return
        url, title, start_time, app_class = self._current
        self._current = None
        if end_time > start_time:
            from .aggregator import Segment
            self._on_segment_closed(Segment(
                source="browser-tab", app_class=app_class, title=title,
                start_time=start_time, end_time=end_time, url=url,
            ))


def _now():
    import time
    return int(time.time())


def _make_handler(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep stdout quiet - this fires on every tab switch

        def do_POST(self):
            if self.path != "/tab-focus":
                self._respond(404, {"status": "error", "message": "not found"})
                return

            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 8192:
                self._respond(400, {"status": "error", "message": "invalid body"})
                return

            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._respond(400, {"status": "error", "message": "invalid JSON"})
                return

            url = payload.get("url")
            if not isinstance(url, str) or not url:
                self._respond(400, {"status": "error", "message": "url is required"})
                return

            title = payload.get("title")
            timestamp = payload.get("timestamp")
            # Extension sends JS epoch milliseconds; normalize to seconds.
            ts_seconds = int(timestamp / 1000) if isinstance(timestamp, (int, float)) else None

            server.handle_tab_focus(url, title if isinstance(title, str) else "", ts_seconds)
            self._respond(200, {"status": "success"})

        def _respond(self, status, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler
