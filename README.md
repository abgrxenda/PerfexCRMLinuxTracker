# Perfex CRM Linux Tracker

![version](https://img.shields.io/badge/version-0.1.6-2ea44f?style=flat-square)
![platform](https://img.shields.io/badge/platform-Linux%20%28Wayland%2FGNOME%29-lightgrey?style=flat-square)
![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![GTK](https://img.shields.io/badge/GTK-4-4A86CF?style=flat-square&logo=gtk&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)

A standalone Linux desktop app for Perfex CRM time tracking - the 4th client alongside `PerfexCRMLibreOfficeExtension`, `PerfexCRMChromeExtension`, and `PerfexCRMFirefoxExtension`, but built around a fundamentally different idea: instead of one continuous manual timer, this app **passively tracks which window or browser tab has focus for a whole work session**, then reconciles that trail into a handful of accurate timesheet entries and submits them all at once when you click Stop.

## Why this exists

The other three clients call `timer_start`/`timer_stop` on Perfex immediately, keeping one continuous timer running until you stop it - which means every tab/window switch either has to be manually bracketed with a stop/start, or gets silently absorbed into one timer's duration whether it was on-task or not. This app inverts that: pick a project and task, click Start, and just work normally - it watches focus changes in the background and only reconciles everything into billing-accurate entries at the end. See [the plan](#-how-it-works) below for exactly how.

## What it does NOT do

- It never calls Perfex's `timer_start`/`timer_stop` - the CRM has no "timer running" concept while this app tracks. It only ever writes finished, already-reconciled entries, via one new bulk-insert endpoint.
- No project/task creation - same read-only dropdowns as the other three clients.
- No X11 support, by design - Wayland only (see Requirements).

## Requirements

- **GNOME on Wayland**, with the [window-calls-extended](https://github.com/hseliger/window-calls-extended) GNOME Shell extension installed and enabled (install from [extensions.gnome.org](https://extensions.gnome.org/extension/4974/window-calls-extended/) or the linked repo - it's a separate, user-installed extension, not bundled with GNOME and not packaged in apt). The app checks for it at startup and shows an actionable message if it's missing rather than starting a session it can't record.
- Python 3.10+, PyGObject (`python3-gi`), GTK4 + libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`), `gdbus` (part of `libglib2.0-bin`, present on any GNOME desktop already).
- The `taskimportperfexcrm` module installed and activated on your Perfex CRM instance (v2.3.0+, for the `/timesheets_bulk` endpoint), with the **Task Timesheet Importer - API Tracker** permission granted to your staff role.
- Optional: `PerfexCRMChromeExtension`/`PerfexCRMFirefoxExtension` (v0.2.0+) running in your browser, for per-URL granularity on browser time instead of one opaque window blob (see below).
- Optional, for the top-bar tray icon: `gir1.2-gtk-3.0` + `gir1.2-ayatanaappindicator3-0.1`, plus GNOME Shell's own "AppIndicator and KStatusNotifierItem Support" extension (also from extensions.gnome.org). Without these the app just runs window-only - no tray icon, nothing else affected.

## Installation

### From a built `.deb`

```bash
sudo apt install ./dist/otek-perfex-linux-tracker_0.1.6_all.deb
```

### Building the `.deb`

```bash
cd PerfexCRMLinuxTracker
./build-deb.sh
```
Checks for `debhelper`/`dpkg-dev`, builds, and produces `dist/otek-perfex-linux-tracker_0.1.6_all.deb`.

### Running from source (development)

```bash
cd PerfexCRMLinuxTracker
python3 perfex_tracker.py
```

## Usage

1. Enter your CRM base URL, email, and password, then **Log in**. Only the resulting token is stored - never the password.
2. Pick a **Project**, then a **Task** (both read-only lists, same as the other clients).
3. Click **Start Tracking**. Work normally - switch windows, open a terminal, browse the web, whatever the task actually needs.
4. Click **Stop Tracking**. The session's focus trail is reconciled (see below) and submitted to Perfex as a batch of timesheet entries in one call.
5. If the app is closed uncleanly mid-session (crash, `kill -9`, power loss), the next launch detects the unsubmitted session and offers to push or discard it - nothing is silently lost.
6. Optional: use the top-bar tray icon (if available on your system) to Start/Stop/Show/Quit without the main window open at all.

## How it works

| Component | Role |
|---|---|
| `perfex_tracker/windowwatch.py` | Polls the focused window's class/title/pid via `window-calls-extended`'s D-Bus interface every ~1.5s; turns focus changes (including a title change within the same app, e.g. switching files) into closed segments. |
| `perfex_tracker/tabserver.py` | A `127.0.0.1`-only HTTP server that ingests tab-focus reports from the Chrome/Firefox extensions while a session runs, giving browser time real per-page granularity instead of one "Firefox" blob. |
| `perfex_tracker/session_store.py` | Crash-safe SQLite log - every segment is committed to disk the instant it closes, never buffered only in memory. Detects and offers recovery for sessions left unsubmitted by an unclean exit. |
| `perfex_tracker/aggregator.py` | Pure, unit-tested reconciliation: groups segments by exact `(app class, title)` - the same treatment for a window's file and a browser tab's page - sums duration per group, merges undersized groups into the largest sibling of the same app rather than dropping them, and synthesizes one timesheet entry per surviving group. |
| `perfex_tracker/api.py` | REST client for the `taskimportperfexcrm` module's bearer-token API, including the new `timesheets_bulk()` call this app is built around. |
| `perfex_tracker/ui.py` | GTK4 window tying it all together; follows the system's light/dark theme automatically (no hardcoded colors). |
| `perfex_tracker/tray.py` + `tray_helper.py` | Optional top-bar indicator (Start/Stop/Show/Quit). Runs in a separate GTK3 subprocess (`tray_helper.py`) talking to the main GTK4 process over stdin/stdout JSON - `AyatanaAppIndicator3` needs a real GTK3 `Gtk.Menu`, which can't coexist in-process with this app's GTK4 UI. This is the same library combination the Upwork desktop app, VS Code, Zoom, and OnlyOffice/Jitsi use for their own tray icons on Linux. |

### The aggregation algorithm, briefly

Twenty 3-second ALT+TAB hops between the same two files sum into two accurate totals rather than being individually judged (and dropped) as too short - the minimum-duration floor (default 45s) applies to a **group's total**, never to an individual raw segment. A group that still doesn't clear the floor merges into the largest group of the same app (e.g. a 20-second detour into a second file folds into the file you spent the session mostly working on) rather than being silently discarded - it's only dropped outright if there's no sibling group of that app to merge into at all. See `perfex_tracker/aggregator.py`'s docstring and `tests/test_aggregator.py` for the exact scripted scenarios this was built and verified against.

### Browser tab integration

Requires no configuration - if `PerfexCRMChromeExtension`/`PerfexCRMFirefoxExtension` (v0.2.0+) is installed and a session is running here, their `background.js` reports tab focus changes to this app's local HTTP server automatically. If the extension isn't present or up to date, the app falls back to logging the browser window as a single plain segment rather than losing that time entirely.

## Testing

```bash
python3 -m unittest discover -s tests -v
```
Covers `aggregator.py`'s reconciliation logic in isolation (no GTK/D-Bus/network/SQLite involved) - see `tests/test_aggregator.py`.

## Known limitations

- Wayland/GNOME only - no X11 fallback, no KDE/Sway/other-compositor support (each would need its own separate focus-detection mechanism).
- Single CRM account, single active session at a time.
- `window-calls-extended`'s exact D-Bus class strings for Chrome/Firefox (`perfex_tracker/windowwatch.py::BROWSER_WINDOW_CLASSES`) were chosen from typical values - verify against your actual system if browser deferral doesn't trigger correctly.
- The tray icon needs GNOME Shell's "AppIndicator and KStatusNotifierItem Support" extension installed separately (GNOME Shell doesn't support tray icons natively) - without it the helper subprocess still runs but the icon won't be visible anywhere.
- No mini/always-on-top timer widget (considered, per an Upwork-style split-window design, but deliberately not built - the single window plus the tray's Show/Hide was judged sufficient).

## License

[MIT](LICENSE).
