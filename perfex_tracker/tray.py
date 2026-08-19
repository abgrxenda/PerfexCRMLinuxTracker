# -*- coding: utf-8 -*-
"""
tray.py
Parent-process (GTK4) side of the top-bar tray indicator. The indicator
itself runs in a separate GTK3 subprocess (tray_helper.py) - see that
file's docstring for exactly why this split is unavoidable:
AyatanaAppIndicator3.set_menu() needs a real GTK3 Gtk.Menu widget, and a
single Python process can only load one version of the "Gtk" typelib
(this app's own UI is GTK4). This is not a workaround for a missing
feature - it is the confirmed, proven approach: inspecting a real,
working installation on this project's target machine showed Upwork,
VS Code, Zoom, and OnlyOffice/Jitsi (Flatpak) all use exactly this
library combination successfully.

available() has to probe Gtk 3.0 + AyatanaAppIndicator3 importability in
a throwaway subprocess, never in this process directly - gi.require_
version() would raise immediately if called here, since Gtk 4.0 is
already bound in this process by the time ui.py has imported anything.
"""

import os
import sys
import json
import subprocess

from gi.repository import GLib

_HELPER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_helper.py")

_PROBE_SCRIPT = (
    "import gi; "
    "gi.require_version('Gtk', '3.0'); "
    "gi.require_version('AyatanaAppIndicator3', '0.1'); "
    "from gi.repository import Gtk, AyatanaAppIndicator3"
)


def available():
    """Best-effort probe for whether the tray helper subprocess can even
    start (Gtk 3.0 + AyatanaAppIndicator3 importable). Runs in a
    throwaway subprocess since this process can never attempt the import
    itself without conflicting with its own already-loaded Gtk 4.0."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class TrayIndicator:
    """Launches tray_helper.py and bridges its stdout events (Start/Stop/
    Show/Quit clicks) to the given PerfexTrackerWindow's existing
    methods, and pushes tracking-state changes back down over the
    subprocess's stdin. Construct only after available() is True."""

    def __init__(self, window):
        self._window = window
        self._proc = subprocess.Popen(
            [sys.executable, _HELPER_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._watch_id = GLib.io_add_watch(
            self._proc.stdout, GLib.IOCondition.IN | GLib.IOCondition.HUP, self._on_stdout_ready
        )

    def _on_stdout_ready(self, source, condition):
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            return False

        line = source.readline()
        if not line:
            return False  # helper process exited

        try:
            msg = json.loads(line)
        except ValueError:
            return True

        event = msg.get("event")
        if event == "start":
            self._window.start_tracking_from_tray()
        elif event == "stop":
            self._window.stop_tracking_from_tray()
        elif event == "show":
            self._window.present()
        elif event == "quit":
            self._window.quit_from_tray()

        return True

    def set_tracking_state(self, is_tracking, task_name):
        if self._proc.poll() is not None:
            return  # helper process already gone - nothing to send to
        try:
            self._proc.stdin.write(json.dumps({"tracking": is_tracking, "task_name": task_name}) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def stop(self):
        """Call on app shutdown. Closing stdin makes the helper quit its
        own main loop cleanly (see tray_helper.py) - never leaves an
        orphaned tray icon behind."""
        if self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
