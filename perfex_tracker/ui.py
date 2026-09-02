# -*- coding: utf-8 -*-
"""
ui.py
GTK4 UI for the Perfex Linux Tracker: login -> project/task dropdowns
(both read-only lists, same as the other three clients - no create-
project/create-task controls here either) -> Start/Stop. Unlike the other
three clients, Start/Stop here never calls timer_start()/timer_stop() -
see windowwatch.py/tabserver.py/aggregator.py/session_store.py for the
passive-tracking machinery this view drives.

Network calls run on background threads (api.py's urllib calls are
blocking) and marshal results back to the GTK main loop via
GLib.idle_add(), matching the pattern already used in the reference
today.otek.currency_converter Flathub app.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw

from . import api
from . import config
from . import windowwatch
from .aggregator import aggregate_session, to_bulk_entries
from .session_store import SessionStore


class PerfexTrackerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Perfex CRM Time Tracker")
        self.set_default_size(420, 480)

        self.cfg = config.load()
        self.store = SessionStore()

        self.projects = []
        self.tasks = []
        self.current_session_id = None
        self.poller = None
        self.tab_server = None
        self._poll_source_id = None
        self.tray = None  # set from PerfexTrackerApp.do_activate() if the tray library is available
        self._is_quitting = False  # see _on_close_request() - same pattern Clockify's Electron app uses

        self._apply_css()
        self._build_ui()
        self._load_initial_state()

        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, _window):
        """The window's own close button (or Ctrl+Q, etc.) hides the
        window instead of destroying it, so a running session keeps
        tracking in the background and the tray's Show can bring it back
        - exactly the behavior confirmed by reading Clockify's Electron
        main process (mainWindow.on('close', ...) + event.preventDefault()
        + mainWindow.hide(), gated by an isQuitting flag only set true
        right before a real app.quit() from the tray's Quit item).
        Returning True here blocks GTK's default close-and-destroy;
        quit_from_tray() sets _is_quitting first so that path still
        exits for real."""
        if self._is_quitting or self.tray is None:
            # No tray means no way to Show the window or Quit the app
            # again once hidden - closing must behave normally (actually
            # quit) so the UI never becomes unreachable.
            return False
        self.set_visible(False)
        return True

    def _apply_css(self):
        """Deliberately does NOT hardcode any background/foreground colors
        - GTK4's default Adwaita theme already follows the system's light/
        dark preference on its own as long as nothing overrides it (unlike
        the reference today.otek.currency_converter app, which hardcodes a
        light-only palette in its own _apply_css() and would look wrong on
        a dark desktop - not something to copy here). The one custom class
        this app needs (.error, for the two error labels) uses libadwaita's
        named semantic color (@error_color) rather than a fixed hex value,
        so it automatically gets the right red for whichever theme is
        active instead of needing separate light/dark branches."""
        css = b"""
        label.error {
            color: @error_color;
            font-size: 12px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(20)
        root.set_margin_end(20)
        self.set_child(root)

        # ---- Login view ----
        self.login_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.login_box.append(Gtk.Label(label="CRM base URL", halign=Gtk.Align.START))
        self.entry_base_url = Gtk.Entry(placeholder_text="https://crm.otek.local")
        self.login_box.append(self.entry_base_url)

        self.login_box.append(Gtk.Label(label="Email", halign=Gtk.Align.START))
        self.entry_email = Gtk.Entry()
        self.login_box.append(self.entry_email)

        self.login_box.append(Gtk.Label(label="Password", halign=Gtk.Align.START))
        self.entry_password = Gtk.Entry(visibility=False)
        self.entry_password.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.login_box.append(self.entry_password)

        self.btn_login = Gtk.Button(label="Log in")
        self.btn_login.connect("clicked", self._on_login_clicked)
        self.login_box.append(self.btn_login)

        self.lbl_login_error = Gtk.Label(label="", wrap=True)
        self.lbl_login_error.add_css_class("error")
        self.login_box.append(self.lbl_login_error)

        root.append(self.login_box)

        # ---- Tracker view ----
        self.tracker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self.lbl_status = Gtk.Label(label="", wrap=True, halign=Gtk.Align.START)
        self.tracker_box.append(self.lbl_status)

        self.tracker_box.append(Gtk.Label(label="Project", halign=Gtk.Align.START))
        self.dd_project = Gtk.DropDown(model=Gtk.StringList())
        self.dd_project.set_enable_search(True)
        self.dd_project.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.dd_project.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self.dd_project.connect("notify::selected", self._on_project_changed)
        self.tracker_box.append(self.dd_project)

        self.tracker_box.append(Gtk.Label(label="Task", halign=Gtk.Align.START))
        self.dd_task = Gtk.DropDown(model=Gtk.StringList())
        self.dd_task.set_enable_search(True)
        self.dd_task.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.dd_task.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self.tracker_box.append(self.dd_task)

        self.btn_start_stop = Gtk.Button(label="Start Tracking")
        self.btn_start_stop.connect("clicked", self._on_start_stop_clicked)
        self.tracker_box.append(self.btn_start_stop)

        self.lbl_tracking = Gtk.Label(label="Not tracking.", wrap=True, halign=Gtk.Align.START)
        self.tracker_box.append(self.lbl_tracking)

        self.btn_logout = Gtk.Button(label="Log out")
        self.btn_logout.connect("clicked", self._on_logout_clicked)
        self.tracker_box.append(self.btn_logout)

        self.lbl_tracker_error = Gtk.Label(label="", wrap=True)
        self.lbl_tracker_error.add_css_class("error")
        self.tracker_box.append(self.lbl_tracker_error)

        root.append(self.tracker_box)

        self.tracker_box.set_visible(False)

    # ------------------------------------------------------------------ #
    # Initial state / crash recovery
    # ------------------------------------------------------------------ #
    def _load_initial_state(self):
        self.entry_base_url.set_text(self.cfg.get("base_url") or "")
        self.entry_email.set_text(self.cfg.get("email") or "")

        orphans = self.store.find_orphaned_sessions()
        if orphans:
            self._prompt_orphaned_session(orphans[0])
            return

        if self.cfg.get("token"):
            self._show_tracker_view()
        else:
            self._show_login_view()

    def _prompt_orphaned_session(self, orphan):
        if orphan["status"] == "running":
            self.store.resolve_running_session_for_recovery(orphan["id"])

        # Deliberately only two choices (no "decide later"): the app
        # refuses to enter the normal Start flow until any orphan is
        # resolved (see the plan's crash-recovery section), and this
        # modal dialog already blocks interaction with the rest of the
        # window until answered, so there's no benign "decide later"
        # state to design for - closing the dialog without choosing just
        # re-prompts on next launch (see the choose_finish exception path
        # below), which is an acceptable outcome for an edge case.
        dialog = Gtk.AlertDialog()
        dialog.set_message("Unsubmitted tracking session found")
        dialog.set_detail(
            "A previous session for task \"%s\" wasn't submitted to the CRM. "
            "Push it now, or discard it?" % (orphan.get("task_name") or "?")
        )
        dialog.set_buttons(["Push now", "Discard"])

        def on_response(_dialog, result):
            try:
                choice = dialog.choose_finish(result)
            except GLib.Error:
                self._prompt_orphaned_session(orphan)  # dismissed without choosing - ask again
                return

            if choice == 0:
                self._show_tracker_view()
                self._push_session(orphan["id"], orphan["task_id"])
            else:
                self.store.mark_discarded(orphan["id"])
                self._load_initial_state()

        dialog.choose(self, None, on_response)

    # ------------------------------------------------------------------ #
    # View switching
    # ------------------------------------------------------------------ #
    def _show_login_view(self):
        self.login_box.set_visible(True)
        self.tracker_box.set_visible(False)

    def _show_tracker_view(self):
        self.login_box.set_visible(False)
        self.tracker_box.set_visible(True)
        self.lbl_status.set_label("Logged in as %s." % (self.cfg.get("staff_name") or self.cfg.get("email") or "?"))
        self._run_async(lambda: api.list_projects(self.cfg["base_url"], self.cfg["token"]), self._on_projects_loaded, self._on_tracker_error)

    # ------------------------------------------------------------------ #
    # Background-thread helper
    # ------------------------------------------------------------------ #
    def _run_async(self, work, on_success, on_error):
        def runner():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI regardless of type
                GLib.idle_add(on_error, exc)
                return
            GLib.idle_add(on_success, result)

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #
    def _on_login_clicked(self, _button):
        self.lbl_login_error.set_label("")
        base_url = self.entry_base_url.get_text().strip()
        email = self.entry_email.get_text().strip()
        password = self.entry_password.get_text()

        if not base_url or not email or not password:
            self.lbl_login_error.set_label("Please fill in all fields.")
            return

        self.btn_login.set_sensitive(False)
        self._run_async(
            lambda: api.login(base_url, email, password, device_name="Linux Tracker"),
            lambda result: self._on_login_success(base_url, email, result),
            self._on_login_error,
        )

    def _on_login_success(self, base_url, email, result):
        self.btn_login.set_sensitive(True)
        self.entry_password.set_text("")
        staff = result.get("staff") or {}
        self.cfg = config.save({
            "base_url": base_url,
            "email": email,
            "token": result.get("token", ""),
            "staff_id": staff.get("staff_id", ""),
            "staff_name": staff.get("name", ""),
        })
        self._show_tracker_view()

    def _on_login_error(self, exc):
        self.btn_login.set_sensitive(True)
        self.lbl_login_error.set_label(getattr(exc, "message", str(exc)))

    def _on_logout_clicked(self, _button):
        if self.current_session_id is not None:
            self.lbl_tracker_error.set_label("Stop the current session first.")
            return
        token = self.cfg.get("token")
        base_url = self.cfg.get("base_url")
        if token:
            self._run_async(lambda: api.logout(base_url, token), lambda _r: None, lambda _e: None)
        self.cfg = config.save({"token": "", "staff_id": "", "staff_name": ""})
        self._show_login_view()

    # ------------------------------------------------------------------ #
    # Project / task lists
    # ------------------------------------------------------------------ #
    def _on_projects_loaded(self, projects):
        self.projects = projects
        model = Gtk.StringList()
        for p in projects:
            status_name = p.get("status_name")
            label = "%s - %s" % (p["name"], status_name) if status_name else p["name"]
            model.append(label)
        self.dd_project.set_model(model)
        if projects:
            self._load_tasks_for_selected_project()

    def _on_project_changed(self, _dropdown, _pspec):
        self._load_tasks_for_selected_project()

    def _load_tasks_for_selected_project(self):
        idx = self.dd_project.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self.projects):
            return
        project_id = self.projects[idx]["id"]
        self._run_async(
            lambda: api.list_tasks(self.cfg["base_url"], self.cfg["token"], project_id),
            self._on_tasks_loaded,
            self._on_tracker_error,
        )

    def _on_tasks_loaded(self, tasks):
        self.tasks = tasks
        model = Gtk.StringList()
        for t in tasks:
            status_name = t.get("status_name")
            label = "%s - %s" % (t["name"], status_name) if status_name else t["name"]
            model.append(label)
        self.dd_task.set_model(model)

    def _on_tracker_error(self, exc):
        self.lbl_tracker_error.set_label(getattr(exc, "message", str(exc)))

    # ------------------------------------------------------------------ #
    # Start / Stop
    # ------------------------------------------------------------------ #
    def _on_start_stop_clicked(self, _button):
        if self.current_session_id is not None:
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self):
        self.lbl_tracker_error.set_label("")

        proj_idx = self.dd_project.get_selected()
        task_idx = self.dd_task.get_selected()
        if proj_idx == Gtk.INVALID_LIST_POSITION or task_idx == Gtk.INVALID_LIST_POSITION:
            self.lbl_tracker_error.set_label("Pick a project and a task first.")
            return

        try:
            windowwatch.health_check()
        except windowwatch.WindowWatchError as exc:
            self.lbl_tracker_error.set_label(exc.actionable)
            return

        project = self.projects[proj_idx]
        task = self.tasks[task_idx]

        self.current_session_id = self.store.start_session(task["id"], project["id"], task["name"])
        self._current_task = task
        self._current_project = project

        self.poller = windowwatch.FocusPoller(on_segment_closed=self._on_segment_closed)
        self.tab_server = None
        try:
            from .tabserver import TabFocusServer
            self.tab_server = TabFocusServer(
                on_segment_closed=self._on_segment_closed,
                current_browser_class=self._current_browser_class,
                port=int(self.cfg.get("http_port") or 17845),
            )
            self.tab_server.start()
        except OSError as exc:
            # Port already in use, etc. - degrade to window-only tracking
            # rather than blocking Start entirely.
            self.tab_server = None
            self.lbl_tracker_error.set_label(
                "Could not start the local tab-focus server (%s) - browser tabs "
                "will be tracked as plain windows instead of per-URL." % exc
            )

        interval = float(self.cfg.get("poll_interval_seconds") or 1.5)
        self._poll_source_id = GLib.timeout_add(int(interval * 1000), self._poll_tick)

        self.dd_project.set_sensitive(False)
        self.dd_task.set_sensitive(False)
        self.btn_start_stop.set_label("Stop Tracking")
        self.lbl_tracking.set_label("Tracking: %s / %s" % (project["name"], task["name"]))
        if self.tray is not None:
            self.tray.set_tracking_state(True, task["name"])

    def _current_browser_class(self):
        if self.poller is None:
            return None
        return self.poller.current_browser_class()

    def _poll_tick(self):
        if self.poller is not None:
            self.poller.poll()
        return GLib.SOURCE_CONTINUE

    def _on_segment_closed(self, segment):
        # May be called from tabserver's background thread as well as the
        # GTK main thread (window polling) - SessionStore.add_segment() is
        # internally locked for exactly this reason.
        self.store.add_segment(self.current_session_id, segment)
        if segment.source == "browser-tab" and self.poller is not None:
            self.poller.note_tab_segment_recorded()

    def _stop_tracking(self):
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None

        if self.poller is not None:
            self.poller.stop()
            self.poller = None

        if self.tab_server is not None:
            self.tab_server.stop()
            self.tab_server = None

        session_id = self.current_session_id
        self.current_session_id = None
        self.store.mark_stopped_pending_push(session_id)

        self.dd_project.set_sensitive(True)
        self.dd_task.set_sensitive(True)
        self.btn_start_stop.set_label("Start Tracking")
        self.lbl_tracking.set_label("Reconciling session...")
        if self.tray is not None:
            self.tray.set_tracking_state(False, None)

        self._push_session(session_id, self._current_task["id"])

    # ------------------------------------------------------------------ #
    # Tray indicator entry points (see tray.py) - all delegate to the same
    # logic the window's own button already uses, guarded so a stale/
    # racing tray click can't double-trigger.
    # ------------------------------------------------------------------ #
    def start_tracking_from_tray(self):
        if self.current_session_id is None:
            self._start_tracking()

    def stop_tracking_from_tray(self):
        if self.current_session_id is not None:
            self._stop_tracking()

    def quit_from_tray(self):
        # Best-effort: if a session is running, stop it (flushes segments
        # and kicks off the push) before exiting. Don't block exit on the
        # async push completing - if it's still in flight when the process
        # actually quits, the session stays 'stopped_pending_push' and the
        # existing crash-recovery flow picks it up next launch, so nothing
        # is lost either way.
        if self.current_session_id is not None:
            self._stop_tracking()
        self._is_quitting = True
        self.get_application().quit()

    def _push_session(self, session_id, task_id):
        segments = self.store.load_segments(session_id)
        min_seconds = int(self.cfg.get("min_group_seconds") or 120)
        entries = aggregate_session(segments, min_group_seconds=min_seconds)

        if not entries:
            self.store.mark_pushed(session_id)
            self.lbl_tracking.set_label("Not tracking. (Nothing met the minimum duration to submit.)")
            return

        bulk_entries = to_bulk_entries(entries)
        self._run_async(
            lambda: api.timesheets_bulk(self.cfg["base_url"], self.cfg["token"], task_id, bulk_entries),
            lambda result: self._on_push_success(session_id, result),
            lambda exc: self._on_push_error(session_id, exc),
        )

    def _on_push_success(self, session_id, result):
        self.store.mark_pushed(session_id)
        self.lbl_tracking.set_label(
            "Not tracking. Last session: %d entr%s submitted (%ds total)."
            % (result.get("inserted", 0), "y" if result.get("inserted") == 1 else "ies", result.get("total_duration_seconds", 0))
        )

    def _on_push_error(self, session_id, exc):
        # Session stays 'stopped_pending_push' - the crash-recovery flow
        # (or a manual retry) will pick it up next launch; nothing is lost.
        self.lbl_tracker_error.set_label(
            "Could not submit the session (%s). It's saved locally and will "
            "be offered again next time you open the app." % getattr(exc, "message", str(exc))
        )
        self.lbl_tracking.set_label("Not tracking.")


class PerfexTrackerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="today.otek.perfex_linux_tracker")
        # Explicit rather than relying on the implicit default (which
        # already happens to be DEFAULT/system-following) - states the
        # intent directly so a future change to this line is the one
        # place that could turn "follow the system" into anything else.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

    def do_activate(self):
        windows = self.get_windows()
        if windows:
            windows[0].present()
            return

        win = PerfexTrackerWindow(self)

        from . import tray
        if tray.available():
            try:
                win.tray = tray.TrayIndicator(win)
            except Exception:
                # Never let a tray-construction problem block the app from
                # opening its main window - same graceful-degradation
                # philosophy as the window-calls-extended health check.
                win.tray = None

        win.present()

    def do_shutdown(self):
        # Guaranteed cleanup regardless of how the app exits (tray Quit,
        # window close, Ctrl+C) - closing the helper subprocess's stdin
        # makes it quit its own GTK3 main loop cleanly, so no orphaned
        # tray icon survives this process.
        for win in self.get_windows():
            if getattr(win, "tray", None) is not None:
                win.tray.stop()
        Gtk.Application.do_shutdown(self)
