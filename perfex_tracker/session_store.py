# -*- coding: utf-8 -*-
"""
session_store.py
SQLite-backed storage for a tracking session's raw focus segments, at
~/.local/share/PerfexLinuxTracker/sessions.sqlite3 (XDG data dir - this is
data, not settings, so it's kept separate from config.py's config dir).

Every segment is committed to disk the instant it closes - never buffered
only in memory - so a crash mid-session loses at most the one segment that
was still open when the process died, never anything already recorded.
This is what makes the crash-recovery flow in has_orphaned_session()/
load_segments() meaningful: an unclean exit still leaves a resumable,
push-able session behind.
"""

import os
import sqlite3
import time
import threading

from .aggregator import Segment

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    project_id INTEGER,
    task_name TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    pushed_at INTEGER,
    status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    source TEXT NOT NULL,
    app_class TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    start_time INTEGER NOT NULL,
    end_time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
"""


def _data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, "PerfexLinuxTracker")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def db_path():
    return os.path.join(_data_dir(), "sessions.sqlite3")


def _connect():
    # isolation_level=None -> autocommit, so every insert below is durable
    # the instant it executes, with no separate commit() call to forget.
    # check_same_thread=False because writes come from two different
    # threads: the GTK main loop (window-focus segments, via
    # windowwatch.FocusPoller driven by GLib.timeout_add_seconds) and
    # tabserver.TabFocusServer's background HTTP-handler thread
    # (browser-tab segments). Every public method below takes _LOCK to
    # serialize access across both, since Python's sqlite3 module isn't
    # safe for concurrent use across threads on one connection even with
    # check_same_thread disabled.
    conn = sqlite3.connect(db_path(), isolation_level=None, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


class SessionStore:
    def __init__(self):
        self._conn = _connect()

    def close(self):
        with _LOCK:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def start_session(self, task_id, project_id, task_name):
        with _LOCK:
            cur = self._conn.execute(
                "INSERT INTO sessions (task_id, project_id, task_name, started_at, status) VALUES (?, ?, ?, ?, 'running')",
                (task_id, project_id, task_name, int(time.time())),
            )
            return cur.lastrowid

    def add_segment(self, session_id, segment):
        """segment: aggregator.Segment. Committed immediately (autocommit
        connection) - this is the crash-safety guarantee this module
        exists to provide. Thread-safe - see the check_same_thread note on
        _connect() above; this is called from both the GTK main thread and
        tabserver's HTTP-handler thread."""
        with _LOCK:
            self._conn.execute(
                "INSERT INTO segments (session_id, source, app_class, title, url, start_time, end_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    segment.source,
                    segment.app_class,
                    segment.title,
                    segment.url,
                    segment.start_time,
                    segment.end_time,
                ),
            )

    def mark_stopped_pending_push(self, session_id):
        with _LOCK:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, status = 'stopped_pending_push' WHERE id = ?",
                (int(time.time()), session_id),
            )

    def mark_pushed(self, session_id):
        with _LOCK:
            self._conn.execute(
                "UPDATE sessions SET pushed_at = ?, status = 'pushed' WHERE id = ?",
                (int(time.time()), session_id),
            )

    def mark_discarded(self, session_id):
        with _LOCK:
            self._conn.execute("UPDATE sessions SET status = 'discarded' WHERE id = ?", (session_id,))

    def load_segments(self, session_id):
        with _LOCK:
            rows = self._conn.execute(
                "SELECT source, app_class, title, url, start_time, end_time FROM segments "
                "WHERE session_id = ? ORDER BY start_time ASC",
                (session_id,),
            ).fetchall()
        return [
            Segment(
                source=r["source"],
                app_class=r["app_class"],
                title=r["title"] or "",
                url=r["url"] or "",
                start_time=r["start_time"],
                end_time=r["end_time"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Crash recovery
    # ------------------------------------------------------------------ #
    def find_orphaned_sessions(self):
        """Sessions left in 'running' (app killed before Stop was ever
        clicked) or 'stopped_pending_push' (Stop ran, aggregation may have
        happened, but the push to Perfex never completed) state. Normally
        at most one exists - a new session can't start until any orphan is
        resolved - but this returns all of them defensively rather than
        assuming exactly one."""
        with _LOCK:
            rows = self._conn.execute(
                "SELECT id, task_id, project_id, task_name, started_at, ended_at, status "
                "FROM sessions WHERE status IN ('running', 'stopped_pending_push') ORDER BY started_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_running_session_for_recovery(self, session_id):
        """A session still marked 'running' means the app was killed
        before Stop was ever clicked - there's no explicit ended_at. Use
        the last closed segment's end_time as the effective session end
        for recovery purposes (any focus after that point was never
        recorded, since the app died before closing that final segment),
        then flip it to 'stopped_pending_push' so it's treated uniformly
        with a normal Stop from here on."""
        with _LOCK:
            row = self._conn.execute(
                "SELECT MAX(end_time) AS last_end FROM segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            ended_at = row["last_end"] if row and row["last_end"] else int(time.time())
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, status = 'stopped_pending_push' WHERE id = ?",
                (ended_at, session_id),
            )
        return ended_at
