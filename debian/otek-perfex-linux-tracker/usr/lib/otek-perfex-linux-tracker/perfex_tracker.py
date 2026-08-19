#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
perfex_tracker.py
Entry point for the Perfex CRM Linux Tracker - a standalone GTK4 desktop
app that passively tracks focused windows/browser tabs for a whole work
session against one Perfex CRM task, then submits the reconciled result
as a batch of timesheet entries at Stop. See perfex_tracker/ui.py for the
UI and perfex_tracker/aggregator.py for the reconciliation algorithm.

Wayland-only (via the window-calls-extended GNOME Shell extension - see
perfex_tracker/windowwatch.py), packaged as a Debian .deb, not Flatpak.
"""

from perfex_tracker.ui import PerfexTrackerApp

if __name__ == "__main__":
    PerfexTrackerApp().run()
