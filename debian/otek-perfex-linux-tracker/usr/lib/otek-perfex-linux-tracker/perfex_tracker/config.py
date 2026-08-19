# -*- coding: utf-8 -*-
"""
config.py
Local settings storage for the Perfex Linux Tracker app. Same rationale
and shape as the sibling LibreOffice/Clockify extensions' config.py: a
plain JSON file in the user's OS config folder rather than a database -
this only ever holds a handful of scalar settings.

File location: ~/.config/PerfexLinuxTracker/config.json

The staff password is never written here - only the bearer token returned
once at login.
"""

import os
import json
import threading

_LOCK = threading.Lock()


def _config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "PerfexLinuxTracker")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def config_path():
    return os.path.join(_config_dir(), "config.json")


_DEFAULTS = {
    "base_url": "",
    "email": "",
    "token": "",
    "staff_id": "",
    "staff_name": "",
    "last_project_id": "",
    "last_project_name": "",
    "last_task_id": "",
    "last_task_name": "",
    # Tracker-specific, all with sensible defaults so a fresh install
    # doesn't require any setup beyond logging in:
    "poll_interval_seconds": 1.5,     # window-focus D-Bus poll cadence
    "http_port": 17845,               # 127.0.0.1-only tab-focus ingestion port
    "min_group_seconds": 45,          # aggregation floor - see aggregator.py
}


def load():
    """Return the current settings as a dict, filled in with defaults for
    any keys that are missing (so callers never need to guard with .get)."""
    path = config_path()
    data = dict(_DEFAULTS)
    with _LOCK:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (OSError, ValueError):
                # Corrupt or unreadable file: fall back to defaults rather
                # than crashing the app.
                pass
    return data


def save(data):
    """Persist the given dict (merged over any existing saved values)."""
    path = config_path()
    with _LOCK:
        current = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    current = json.load(f)
                if not isinstance(current, dict):
                    current = {}
            except (OSError, ValueError):
                current = {}
        current.update(data)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    return current


def update(**kwargs):
    """Convenience wrapper: save(kwargs)."""
    return save(kwargs)
