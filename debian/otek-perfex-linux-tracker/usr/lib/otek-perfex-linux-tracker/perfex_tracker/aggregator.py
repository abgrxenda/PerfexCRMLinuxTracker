# -*- coding: utf-8 -*-
"""
aggregator.py
Turns a session's raw focus segments into the handful of timesheet entries
actually pushed to Perfex. Pure functions only - no GTK, no D-Bus, no
network, no SQLite - so this is fully unit-testable in isolation (see
tests/test_aggregator.py) against hand-crafted segment lists.

The algorithm (agreed with the user - see the plan file's "Refinement:
unify grouping by (app class, exact title) + cascade-merge undersized
groups" section for the full reasoning):

  1. Group raw segments by (app_class, exact title) - uniformly for both
     window-sourced and browser-tab-sourced segments. A browser tab's
     title is treated exactly like a window's title (not the URL, which
     is kept only as informational metadata on the Segment) - two
     different pages sharing an identical title will merge, a tradeoff
     the user explicitly accepted for consistency with how window titles
     already work.
  2. Sum the focused duration per (app_class, title) group across the
     whole session. This is what correctly turns e.g. twenty 3-second
     ALT+TAB hops between two files into one accurate total each, instead
     of each hop being judged individually.
  3. Within each app_class, groups whose summed total is >= min_group_
     seconds stand alone as their own entries. Groups under that floor
     are never simply dropped - they merge into the single LARGEST group
     of the same app_class (by total duration - this may itself already
     be a qualifying group, or, if no group in that class qualifies at
     all, the largest of the undersized siblings regardless). Only a
     lone undersized group with no sibling of the same app_class to
     merge into is fully dropped - the one case where time is discarded.
  4. Synthesize one start/end span per surviving entry (tbltaskstimers
     stores one continuous span per row, not a disjoint interval set):
     anchored so the span ENDS at the latest end_time among every segment
     the entry absorbed, extending backward by the summed duration - this
     keeps the synthesized end time close to "when the user was last
     actually doing this."
"""

from dataclasses import dataclass, field
from collections import defaultdict

DEFAULT_MIN_GROUP_SECONDS = 45

# Small, optional prettification map for common window classes - falls
# back to the raw class string for anything not listed. Not load-bearing;
# purely to make note text more readable.
_APP_DISPLAY_NAMES = {
    "code": "VS Code",
    "google-chrome": "Chrome",
    "chromium": "Chromium",
    "firefox": "Firefox",
    "org.gnome.terminal": "Terminal",
    "gnome-terminal": "Terminal",
    "libreoffice-writer": "LibreOffice Writer",
    "libreoffice-calc": "LibreOffice Calc",
    "slack": "Slack",
}


@dataclass
class Segment:
    source: str          # 'window' | 'browser-tab'
    app_class: str        # owning app's window class, e.g. "libreoffice-calc", "firefox"
    title: str             # exact window title / exact tab title - this is the grouping key together with app_class
    start_time: int
    end_time: int
    url: str = ""          # browser-tab only - informational metadata, NOT used for grouping


@dataclass
class AggregatedEntry:
    app_class: str
    title: str              # the surviving (anchor) group's title
    start_time: int
    end_time: int
    duration_seconds: int
    note: str
    switch_count: int
    merged_group_count: int          # distinct (app_class, title) groups folded in; 1 = no merge happened
    segments: list = field(default_factory=list, repr=False)


def aggregate_session(segments, min_group_seconds=DEFAULT_MIN_GROUP_SECONDS):
    """segments: list[Segment]. Returns list[AggregatedEntry], sorted by
    start_time ascending, ready to hand to api.timesheets_bulk() (as
    {"start_time":, "end_time":, "note":} dicts via to_bulk_entries())."""
    groups = defaultdict(list)
    for seg in segments:
        groups[(seg.app_class, seg.title)].append(seg)

    group_totals = {key: sum(s.end_time - s.start_time for s in segs) for key, segs in groups.items()}

    by_class = defaultdict(list)  # app_class -> [(title, total_seconds, segs), ...]
    for (app_class, title), segs in groups.items():
        by_class[app_class].append((title, group_totals[(app_class, title)], segs))

    entries = []
    for app_class, class_groups in by_class.items():
        qualifying = [g for g in class_groups if g[1] >= min_group_seconds]
        undersized = [g for g in class_groups if g[1] < min_group_seconds]

        if not undersized:
            for title, _total, segs in qualifying:
                entries.append(_build_entry(app_class, title, segs, merged_group_count=1))
            continue

        if qualifying:
            # At least one group of this class already clears the floor -
            # every undersized sibling merges into the single largest
            # qualifying one (which may not be the largest overall).
            anchor_title, _anchor_total, anchor_segs = max(qualifying, key=lambda g: g[1])
        elif len(class_groups) > 1:
            # Nothing in this class qualifies, but siblings exist -
            # cascade-merge everything into whichever has the most time
            # regardless of it also being under the floor (user's explicit
            # choice: nothing is lost as long as a sibling exists).
            anchor_title, _anchor_total, anchor_segs = max(class_groups, key=lambda g: g[1])
        else:
            # A lone undersized group with no sibling of this app_class to
            # merge into - the only case where time is fully discarded.
            continue

        merged_segs = list(anchor_segs)
        merged_group_count = 1
        for title, _total, segs in undersized:
            if title == anchor_title:
                continue  # the anchor itself, when it came from the cascade branch
            merged_segs.extend(segs)
            merged_group_count += 1

        entries.append(_build_entry(app_class, anchor_title, merged_segs, merged_group_count))

        # Other qualifying groups of this class (besides the one chosen as
        # the merge anchor) stand alone, untouched.
        for title, _total, segs in qualifying:
            if title != anchor_title:
                entries.append(_build_entry(app_class, title, segs, merged_group_count=1))

    entries.sort(key=lambda e: e.start_time)
    return entries


def to_bulk_entries(entries):
    """AggregatedEntry list -> the plain dict shape api.timesheets_bulk()
    expects for its `entries` JSON payload."""
    return [
        {"start_time": e.start_time, "end_time": e.end_time, "note": e.note}
        for e in entries
    ]


def _build_entry(app_class, title, segs, merged_group_count):
    total_seconds = sum(s.end_time - s.start_time for s in segs)
    synth_end = max(s.end_time for s in segs)
    synth_start = synth_end - total_seconds
    switch_count = len(segs)
    # segs[0] is always from the anchor's own original group (placed first
    # by aggregate_session before any absorbed segments are appended), so
    # its source reliably reflects what this entry fundamentally is.
    source = segs[0].source

    note = _build_note(source, app_class, title, switch_count, total_seconds, merged_group_count)

    return AggregatedEntry(
        app_class=app_class, title=title,
        start_time=synth_start, end_time=synth_end,
        duration_seconds=total_seconds, note=note,
        switch_count=switch_count, merged_group_count=merged_group_count,
        segments=segs,
    )


def _build_note(source, app_class, title, switch_count, total_seconds, merged_group_count):
    duration = _human_duration(total_seconds)
    app_name = _APP_DISPLAY_NAMES.get(app_class.lower(), app_class)
    display_title = title or app_name

    if source == "browser-tab":
        note = "%s (%s tab, %d visits, %s total)" % (display_title, app_name, switch_count, duration)
    else:
        note = "%s — %s (%d focus switches, %s total)" % (app_name, display_title, switch_count, duration)

    if merged_group_count > 1:
        extra = merged_group_count - 1
        note += " + %d shorter activit%s merged in" % (extra, "y" if extra == 1 else "ies")

    return note


def _human_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append("%dh" % hours)
    if hours or minutes:
        parts.append("%dm" % minutes)
    parts.append("%ds" % secs)
    return "".join(parts)
