#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tray_helper.py
Standalone GTK3 subprocess for the top-bar tray indicator (Start / Stop /
Show / Quit). Launched by tray.py, NEVER imported directly by the main
GTK4 app (perfex_tracker/ui.py) - this split exists because
AyatanaAppIndicator3.set_menu() requires a real GTK3 Gtk.Menu widget, and
a single Python process cannot load both the Gtk 3.0 and Gtk 4.0
typelibs (gi.require_version() binds one version of a namespace per
process). Running the indicator in its own GTK3-only process sidesteps
the conflict entirely.

This exact library combination - libayatana-appindicator3 driving a real
Gtk.Menu, via libdbusmenu-gtk3 for the widget-to-DBusMenu conversion - was
confirmed (by inspecting a live, working installation, not guessed) to be
what Upwork's desktop app, VS Code, Zoom, and the OnlyOffice/Jitsi
Flatpaks all actually use successfully in this project's target
environment. It is the well-documented, long-standing public API for
these libraries, unlike the newer libayatana-appindicator-glib (which
isn't installed anywhere on the reference machine and has no confirmed
real-world usage there).

Protocol: newline-delimited JSON over stdin/stdout.
  child -> parent (stdout): {"event": "start"|"stop"|"show"|"quit"}
  parent -> child (stdin):  {"tracking": bool, "task_name": str or null}
On stdin EOF (the parent process is gone), this process quits its own
GTK main loop and exits - never leaves an orphaned tray icon behind if
the main app dies unexpectedly.
"""

import os
import sys
import json

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator3

# AppIndicator3 resolves its icon by NAME through its own icon lookup
# (ultimately handed to the GNOME Shell AppIndicator extension over
# D-Bus/SNI), which does NOT reliably fall back to the default GTK icon
# theme search path the way a regular window/.desktop icon does - a bare
# name lookup with no explicit theme path resulted in a generic "..."
# placeholder instead of the real icon, confirmed live. The fix, matching
# the common working pattern for AppIndicator3 apps (and how Clockify's
# own Electron tray icon is set - two flat 22x22 RGBA PNGs, swapped by
# tracking state via changeIcon(start) in their main.ts): call
# set_icon_theme_path() pointing at a flat directory containing the icon
# files directly, then reference each by filename minus extension.
# Checked in order: the icons/ directory next to this file when run from
# source, then the installed hicolor location once packaged - whichever
# actually contains the icons wins, since the two layouts differ (see
# debian/otek-perfex-linux-tracker.install).
ICON_NAME_INACTIVE = "otek-perfex-linux-tracker-inactive"
ICON_NAME_ACTIVE = "otek-perfex-linux-tracker-active"


def _resolve_icon_theme_path():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icons"),
        "/usr/share/icons/hicolor/22x22/apps",
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, ICON_NAME_INACTIVE + ".svg")):
            return candidate
    return candidates[0]  # best-effort fallback - AppIndicator will show a placeholder if this is wrong too


def _emit(event):
    sys.stdout.write(json.dumps({"event": event}) + "\n")
    sys.stdout.flush()


class TrayHelper:
    def __init__(self):
        self.item_start = Gtk.MenuItem(label="Start")
        self.item_start.connect("activate", lambda *_a: _emit("start"))

        self.item_stop = Gtk.MenuItem(label="Stop")
        self.item_stop.connect("activate", lambda *_a: _emit("stop"))
        self.item_stop.set_sensitive(False)

        item_show = Gtk.MenuItem(label="Show")
        item_show.connect("activate", lambda *_a: _emit("show"))

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda *_a: _emit("quit"))

        menu = Gtk.Menu()
        for item in (self.item_start, self.item_stop, Gtk.SeparatorMenuItem(), item_show, item_quit):
            menu.append(item)
        menu.show_all()

        self.indicator = AppIndicator3.Indicator.new(
            "otek-perfex-linux-tracker", ICON_NAME_INACTIVE,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(_resolve_icon_theme_path())
        self.indicator.set_icon_full(ICON_NAME_INACTIVE, "Perfex CRM Time Tracker")
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(menu)

    def set_tracking_state(self, is_tracking, task_name):
        self.item_start.set_sensitive(not is_tracking)
        self.item_stop.set_sensitive(is_tracking)

        icon_name = ICON_NAME_ACTIVE if is_tracking else ICON_NAME_INACTIVE
        self.indicator.set_icon_full(icon_name, "Perfex CRM Time Tracker")

        title = ("Perfex Tracker — tracking: %s" % task_name) if is_tracking else "Perfex Tracker — idle"
        try:
            self.indicator.set_title(title)
        except AttributeError:
            pass  # title/tooltip support may vary - non-essential


def _on_stdin_ready(source, condition, helper):
    if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
        Gtk.main_quit()
        return False

    line = source.readline()
    if not line:
        Gtk.main_quit()  # parent closed stdin - exit cleanly, no orphaned icon
        return False

    try:
        msg = json.loads(line)
    except ValueError:
        return True

    if "tracking" in msg:
        helper.set_tracking_state(bool(msg["tracking"]), msg.get("task_name"))

    return True


def main():
    helper = TrayHelper()
    GLib.io_add_watch(sys.stdin, GLib.IOCondition.IN | GLib.IOCondition.HUP, _on_stdin_ready, helper)
    Gtk.main()


if __name__ == "__main__":
    main()
