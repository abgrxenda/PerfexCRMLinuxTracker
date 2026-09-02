# -*- coding: utf-8 -*-
"""
Unit tests for perfex_tracker.aggregator - pure functions, no GTK/D-Bus/
network/SQLite involved. Run with: python3 -m unittest discover -s tests
(from the PerfexCRMLinuxTracker/ root, with perfex_tracker/ on the path).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perfex_tracker.aggregator import aggregate_session, Segment, to_bulk_entries


def seg(app_class, title, start, end, source="window", url=""):
    return Segment(source=source, app_class=app_class, title=title, start_time=start, end_time=end, url=url)


class TestAggregateSession(unittest.TestCase):
    def test_many_short_hops_sum_correctly(self):
        # Twenty 3-second hops on the same (app_class, title) sum to one
        # accurate total, well over the floor.
        segments = [seg("code", "a.py", i * 10, i * 10 + 3) for i in range(20)]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].duration_seconds, 60)
        self.assertEqual(entries[0].switch_count, 20)
        self.assertEqual(entries[0].merged_group_count, 1)

    def test_undersized_group_merges_into_qualifying_sibling(self):
        # "a.py" qualifies on its own (65s); "b.py" doesn't (20s) but
        # shares the "code" app class - it should merge into "a.py"'s
        # entry rather than being dropped or standing alone.
        segments = [
            seg("code", "a.py", 0, 65),
            seg("code", "b.py", 65, 85),
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "a.py")
        self.assertEqual(entry.duration_seconds, 85)
        self.assertEqual(entry.switch_count, 2)
        self.assertEqual(entry.merged_group_count, 2)
        self.assertIn("+ 1 shorter activity merged in", entry.note)

    def test_undersized_merges_into_the_largest_qualifying_group_specifically(self):
        # Two qualifying groups ("a.py" 100s, "b.py" 50s) plus one
        # undersized ("c.py" 10s) - the undersized one must merge into the
        # LARGEST qualifying group ("a.py"), not just any qualifying one.
        segments = [
            seg("code", "a.py", 0, 100),
            seg("code", "b.py", 100, 150),
            seg("code", "c.py", 150, 160),
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        by_title = {e.title: e for e in entries}
        self.assertEqual(set(by_title.keys()), {"a.py", "b.py"})
        self.assertEqual(by_title["a.py"].duration_seconds, 110)  # 100 + 10 absorbed
        self.assertEqual(by_title["a.py"].merged_group_count, 2)
        self.assertEqual(by_title["b.py"].duration_seconds, 50)   # untouched
        self.assertEqual(by_title["b.py"].merged_group_count, 1)

    def test_all_undersized_siblings_cascade_merge_into_largest(self):
        # No group for "slack" individually clears the floor, but two
        # siblings exist - everything cascades into the larger one.
        segments = [
            seg("slack", "General", 0, 10),
            seg("slack", "DMs", 10, 25),
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "DMs")
        self.assertEqual(entry.duration_seconds, 25)
        self.assertEqual(entry.switch_count, 2)
        self.assertEqual(entry.merged_group_count, 2)

    def test_lone_undersized_group_with_no_sibling_is_dropped(self):
        segments = [seg("terminal", "bash", 0, 8)]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(entries, [])

    def test_floor_is_inclusive_at_exact_boundary(self):
        segments = [seg("code", "a.py", 0, 120)]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].merged_group_count, 1)

    def test_synthesized_span_anchors_to_latest_absorbed_end_time(self):
        segments = [
            seg("code", "a.py", 0, 60),     # qualifies
            seg("code", "b.py", 60, 65),    # undersized, but ends LATER than a.py's own last segment would suggest
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        entry = entries[0]
        # total = 60 + 5 = 65, latest end among absorbed segments = 65
        self.assertEqual(entry.duration_seconds, 65)
        self.assertEqual(entry.end_time, 65)
        self.assertEqual(entry.start_time, 0)

    def test_browser_tabs_group_by_title_not_url(self):
        # Two different URLs sharing an identical title merge together -
        # the user's explicit, accepted tradeoff for consistency with
        # window-title grouping.
        segments = [
            seg("firefox", "Loading...", 0, 30, source="browser-tab", url="https://a.example/x"),
            seg("firefox", "Loading...", 30, 70, source="browser-tab", url="https://b.example/y"),
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Loading...")
        self.assertEqual(entries[0].duration_seconds, 70)
        self.assertEqual(entries[0].switch_count, 2)

    def test_browser_note_format(self):
        segments = [seg("firefox", "GitHub", 0, 60, source="browser-tab", url="https://github.com/foo/bar")]
        entries = aggregate_session(segments, min_group_seconds=120)
        self.assertIn("GitHub", entries[0].note)
        self.assertIn("Firefox tab", entries[0].note)
        self.assertIn("1 visits", entries[0].note)

    def test_window_and_browser_app_classes_are_independent(self):
        segments = [
            seg("code", "a.py", 0, 60),
            seg("firefox", "GitHub", 60, 130, source="browser-tab", url="https://github.com"),
        ]
        entries = aggregate_session(segments, min_group_seconds=120)
        titles = {e.app_class: e.title for e in entries}
        self.assertEqual(titles, {"code": "a.py", "firefox": "GitHub"})

    def test_entries_sorted_by_start_time(self):
        segments = [
            seg("slack", "General", 100, 200),
            seg("code", "a.py", 0, 100),
        ]
        entries = aggregate_session(segments, min_group_seconds=10)
        self.assertEqual([e.app_class for e in entries], ["code", "slack"])

    def test_to_bulk_entries_shape(self):
        segments = [seg("code", "a.py", 0, 60)]
        entries = aggregate_session(segments, min_group_seconds=120)
        bulk = to_bulk_entries(entries)
        self.assertEqual(len(bulk), 1)
        self.assertEqual(set(bulk[0].keys()), {"start_time", "end_time", "note"})
        self.assertEqual(bulk[0]["start_time"], 0)
        self.assertEqual(bulk[0]["end_time"], 60)


if __name__ == "__main__":
    unittest.main()
