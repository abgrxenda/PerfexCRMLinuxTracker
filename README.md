# 🕒 Perfex CRM Linux Tracker

![version](https://img.shields.io/badge/version-1.0.6-2ea44f?style=flat-square)
![platform](https://img.shields.io/badge/platform-Linux%20%28Wayland%2FGNOME%29-lightgrey?style=flat-square&logo=linux&logoColor=white)
![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![GTK](https://img.shields.io/badge/GTK-4-4A86CF?style=flat-square&logo=gtk&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)

A standalone Linux desktop app for Perfex CRM time tracking - the 4th client alongside `PerfexCRMLibreOfficeExtension`, `PerfexCRMChromeExtension`, and `PerfexCRMFirefoxExtension`, but built around a fundamentally different idea: instead of one continuous manual timer, this app **passively tracks which window or browser tab has focus for a whole work session**, then reconciles that trail into a handful of accurate timesheet entries and submits them all at once when you click Stop.

## 💡 Why this exists

The other three clients call `timer_start`/`timer_stop` on Perfex immediately, keeping one continuous timer running until you stop it - which means every tab/window switch either has to be manually bracketed with a stop/start, or gets silently absorbed into one timer's duration whether it was on-task or not. This app inverts that: pick a project and task, click Start, and just work normally - it watches focus changes in the background and only reconciles everything into billing-accurate entries at the end. See [the plan](#-how-it-works) below for exactly how.

## ✨ Features

- **Passive focus tracking** - watches which window (and, with the browser extensions, which tab) has focus for the whole session; no stop/start on every context switch.
- **One-click batch submit** - on Stop, the session is reconciled into a few timesheet entries and sent to Perfex in a single `/timesheets_bulk` call, each with a readable note (e.g. `VS Code — ui.py (14 focus switches, 38m total)`).
- **Project/task status in the dropdowns** - entries read `Project Name - Status` and `Task Name - Status` (e.g. `Website Redesign - In Progress`), so you can tell active work from finished work at a glance.
- **Searchable dropdowns** - type to filter projects and tasks; matches anywhere in the name, not only the start.
- **Refresh button** - the ⟳ button next to **Project** re-fetches projects and tasks from the CRM without restarting the app (e.g. after an admin adds a project). Your current project/task stay selected if they still exist.
- **Remembers your last project and task** - reopening the app or logging in again selects whatever you last worked on. If that project no longer exists in the CRM, it falls back to the first entry.
- **Crash-safe** - every segment is written to a local SQLite log the moment it closes; a session interrupted by a crash, power loss, or a failed submit is offered again on next launch (**Push now** / **Discard**).
- **Per-tab browser time** (optional) - with `PerfexCRMChromeExtension`/`PerfexCRMFirefoxExtension` installed, browser time is split per page instead of one "Firefox" block.
- **Top-bar tray icon** (optional) - Start/Stop/Show/Quit from the GNOME top bar; the icon changes while tracking, and closing the window just hides it so tracking continues in the background.
- **Single instance** - launching the app again brings the existing window forward instead of opening a second copy.
- **Follows the system light/dark theme** automatically.
- **Password never stored** - only the API token returned at login is saved.

## 🚫 What it does NOT do

- It never calls Perfex's `timer_start`/`timer_stop` - the CRM has no "timer running" concept while this app tracks. It only ever writes finished, already-reconciled entries, via one new bulk-insert endpoint.
- No project/task creation - same read-only dropdowns as the other three clients.
- No X11 support, by design - Wayland only (see Requirements).

## 📋 Requirements

- **GNOME on Wayland**, with the [window-calls-extended](https://github.com/hseliger/window-calls-extended) GNOME Shell extension installed and enabled (install from [extensions.gnome.org](https://extensions.gnome.org/extension/4974/window-calls-extended/) or the linked repo - it's a separate, user-installed extension, not bundled with GNOME and not packaged in apt). The app checks for it at startup and shows an actionable message if it's missing rather than starting a session it can't record.
- Python 3.10+, PyGObject (`python3-gi`), GTK4 + libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`), `gdbus` (part of `libglib2.0-bin`, present on any GNOME desktop already).
- The `taskimportperfexcrm` module installed and activated on your Perfex CRM instance, with the **Task Timesheet Importer - API Tracker** permission granted to your staff role. v2.3.0+ is required for the `/timesheets_bulk` endpoint; v2.3.2+ is needed for the project/task status to show in the dropdowns (older versions still work, the labels just show the name only).
- Optional: `PerfexCRMChromeExtension`/`PerfexCRMFirefoxExtension` (v0.2.0+) running in your browser, for per-URL granularity on browser time instead of one opaque window blob (see below).
- Optional, for the top-bar tray icon: `gir1.2-gtk-3.0` + `gir1.2-ayatanaappindicator3-0.1`, plus GNOME Shell's own "AppIndicator and KStatusNotifierItem Support" extension (also from extensions.gnome.org). Without these the app just runs window-only - no tray icon, nothing else affected.

## ⬇️ Installation

### 📦 From a release

Download `otek-perfex-linux-tracker_1.0.6_all.deb` from the latest release on [GitHub](https://github.com/abgrxenda/PerfexCRMLinuxTracker/releases), [GitLab](https://gitlab.com/abgrxenda/PerfexCRMLinuxTracker/-/releases), or [Gitea](https://git.otek.today/abgrxenda/PerfexCRMLinuxTracker/releases), then:

```bash
sudo apt install ./otek-perfex-linux-tracker_1.0.6_all.deb
```

`apt` pulls in the required dependencies (and the recommended tray-icon libraries). The `window-calls-extended` GNOME Shell extension still has to be installed separately - see Requirements.

### 🗃️ From the Gitea apt repository (automatic updates)

```bash
sudo curl -o /etc/apt/keyrings/gitea-abgrxenda.asc https://git.otek.today/api/packages/abgrxenda/debian/repository.key
echo "deb [signed-by=/etc/apt/keyrings/gitea-abgrxenda.asc] https://git.otek.today/api/packages/abgrxenda/debian stable main" | sudo tee /etc/apt/sources.list.d/otek.list
sudo apt update && sudo apt install otek-perfex-linux-tracker
```

Future versions then arrive with a normal `sudo apt upgrade`.

### 📦 From a locally built `.deb`

```bash
sudo apt install ./dist/otek-perfex-linux-tracker_1.0.6_all.deb
```

### 🛠️ Building the `.deb`

```bash
cd PerfexCRMLinuxTracker
./build-deb.sh
```
Checks for `debhelper`/`dpkg-dev`, builds, and produces `dist/otek-perfex-linux-tracker_<version>_all.deb` (currently `1.0.6`). The version comes from the top entry of `debian/changelog` - add a new entry there to bump it.

### 💻 Running from source (development)

```bash
cd PerfexCRMLinuxTracker
python3 perfex_tracker.py
```

## ▶️ Usage

1. Launch **Perfex Linux Tracker** from the app grid (or run `otek-perfex-linux-tracker`).
2. Enter your CRM base URL, email, and password, then **Log in**. Only the resulting token is stored - never the password. The URL and email are pre-filled next time.
3. Pick a **Project**, then a **Task**. Each entry shows its status (`Name - Status`); click a dropdown and start typing to search. Your last-used project and task are already selected when you come back. If a project or task is missing, click the ⟳ **refresh** button next to **Project** to reload the lists from the CRM.
4. Click **Start Tracking**. The app first checks that `window-calls-extended` is available and tells you exactly what to install if it isn't. Then just work normally - switch windows, open a terminal, browse the web, whatever the task needs. The project/task dropdowns and refresh button are locked while a session runs.
5. Click **Stop Tracking**. The session's focus trail is reconciled (see below) and submitted to Perfex as a batch of timesheet entries in one call. The status line then shows how many entries were submitted and the total time. If nothing reached the minimum duration, nothing is submitted.
6. If submitting fails (network down, CRM unreachable) or the app is closed uncleanly mid-session (crash, `kill -9`, power loss), the session stays saved locally. The next launch asks you to **Push now** or **Discard** it - nothing is silently lost.
7. Optional: with the tray icon available, closing the window only hides it - tracking keeps running, and the tray menu offers **Start / Stop / Show / Quit**. **Quit** stops and submits a running session before exiting. Without the tray, closing the window quits the app.
8. **Log out** clears the saved token (stop any running session first).

## 🔧 Configuration and data

Settings live in `~/.config/PerfexLinuxTracker/config.json` (respects `$XDG_CONFIG_HOME`). Everything has a sensible default; the app writes the login and last project/task fields itself. Tunable keys:

| Key | Default | Meaning |
|---|---|---|
| `min_group_seconds` | `120` | Minimum total time a group needs to become its own timesheet entry (see the aggregation algorithm below). |
| `poll_interval_seconds` | `1.5` | How often the focused window is checked. |
| `http_port` | `17845` | Local port for browser tab-focus reports. If it's taken, tracking still starts and browsers are logged as plain windows. |

Saved automatically: `base_url`, `email`, `token`, `staff_id`, `staff_name`, `last_project_id`/`last_project_name`, `last_task_id`/`last_task_name`.

Session data (the raw focus segments) is stored in `~/.local/share/PerfexLinuxTracker/sessions.sqlite3`.

## ⚙️ How it works

| Component | Role |
|---|---|
| `perfex_tracker/windowwatch.py` | Polls the focused window's class/title/pid via `window-calls-extended`'s D-Bus interface every ~1.5s; turns focus changes (including a title change within the same app, e.g. switching files) into closed segments. |
| `perfex_tracker/tabserver.py` | A `127.0.0.1`-only HTTP server that ingests tab-focus reports from the Chrome/Firefox extensions while a session runs, giving browser time real per-page granularity instead of one "Firefox" blob. |
| `perfex_tracker/session_store.py` | Crash-safe SQLite log - every segment is committed to disk the instant it closes, never buffered only in memory. Detects and offers recovery for sessions left unsubmitted by an unclean exit. |
| `perfex_tracker/aggregator.py` | Pure, unit-tested reconciliation: groups segments by exact `(app class, title)` - the same treatment for a window's file and a browser tab's page - sums duration per group, merges undersized groups into the largest sibling of the same app rather than dropping them, and synthesizes one timesheet entry per surviving group. |
| `perfex_tracker/api.py` | REST client for the `taskimportperfexcrm` module's bearer-token API (`/login`, `/logout`, `/projects`, `/tasks`, and the `/timesheets_bulk` call this app is built around). |
| `perfex_tracker/config.py` | JSON settings file (see Configuration), written atomically. |
| `perfex_tracker/ui.py` | GTK4 window tying it all together: login, searchable project/task dropdowns with status labels, refresh, last-selection restore, Start/Stop, crash-recovery prompt. Follows the system's light/dark theme automatically (no hardcoded colors). |
| `perfex_tracker/tray.py` + `tray_helper.py` | Optional top-bar indicator (Start/Stop/Show/Quit). Runs in a separate GTK3 subprocess (`tray_helper.py`) talking to the main GTK4 process over stdin/stdout JSON - `AyatanaAppIndicator3` needs a real GTK3 `Gtk.Menu`, which can't coexist in-process with this app's GTK4 UI. This is the same library combination the Upwork desktop app, VS Code, Zoom, and OnlyOffice/Jitsi use for their own tray icons on Linux. |

### 🧮 The aggregation algorithm, briefly

Twenty 3-second ALT+TAB hops between the same two files sum into two accurate totals rather than being individually judged (and dropped) as too short - the minimum-duration floor (default 120s, `min_group_seconds`) applies to a **group's total**, never to an individual raw segment. A group that still doesn't clear the floor merges into the largest group of the same app (e.g. a 20-second detour into a second file folds into the file you spent the session mostly working on) rather than being silently discarded - it's only dropped outright if there's no sibling group of that app to merge into at all. Each surviving group becomes one timesheet entry whose time span ends when you last worked on it and whose length is the summed focus time; its note names the app, window/tab title, number of focus switches, total time, and how many shorter activities were merged in. See `perfex_tracker/aggregator.py`'s docstring and `tests/test_aggregator.py` for the exact scripted scenarios this was built and verified against.

### 🌐 Browser tab integration

Requires no configuration - if `PerfexCRMChromeExtension`/`PerfexCRMFirefoxExtension` (v0.2.0+) is installed and a session is running here, their `background.js` reports tab focus changes (`POST http://127.0.0.1:17845/tab-focus`) to this app's local HTTP server automatically. The server only listens on localhost and only while a session is running. Tab reports only count while GNOME says a browser window actually has focus, so a background browser window can't steal time. If the extension isn't present or up to date, the app falls back to logging the browser window as a single plain segment rather than losing that time entirely.

## ✅ Testing

```bash
python3 -m unittest discover -s tests -v
```
Covers `aggregator.py`'s reconciliation logic in isolation (no GTK/D-Bus/network/SQLite involved) - see `tests/test_aggregator.py`.

## ⚠️ Known limitations

- Wayland/GNOME only - no X11 fallback, no KDE/Sway/other-compositor support (each would need its own separate focus-detection mechanism).
- Single CRM account, single active session at a time.
- `window-calls-extended`'s exact D-Bus class strings for Chrome/Firefox (`perfex_tracker/windowwatch.py::BROWSER_WINDOW_CLASSES`) were chosen from typical values - verify against your actual system if browser deferral doesn't trigger correctly.
- The tray icon needs GNOME Shell's "AppIndicator and KStatusNotifierItem Support" extension installed separately (GNOME Shell doesn't support tray icons natively) - without it the helper subprocess still runs but the icon won't be visible anywhere.
- No mini/always-on-top timer widget (considered, per an Upwork-style split-window design, but deliberately not built - the single window plus the tray's Show/Hide was judged sufficient).

## 📝 Changelog

Full history in [`debian/changelog`](debian/changelog). Recent highlights:

- **1.0.6** - Remembers the last project and task across restarts and re-logins.
- **1.0.5** - Refresh button to reload projects and tasks without restarting.
- **1.0.4** - Project dropdown shows `Project Name - Status`.
- **1.0.3** - Task dropdown shows `Task Name - Status`.
- **1.0.2** - Minimum group duration raised from 45s to 120s.
- **0.1.8** - Relaunching brings the existing window forward instead of opening a duplicate.
- **0.1.7** - Searchable project/task dropdowns (substring match).
- **0.1.5** - Tray indicator, light/dark theme support, merge-into-sibling aggregation.

## 📄 License

[MIT](LICENSE).
