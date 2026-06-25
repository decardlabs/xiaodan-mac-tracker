# 🥚 XiaoDan — Mac Time Tracker v0.79

A local macOS tool that tracks how you spend your time on your computer. It records your active app and webpage every few seconds, automatically categorizes your activities, and shows you where your time actually goes — all stored locally, nothing uploaded.

[中文版 README](README.md)

## Features

### Background Recording
- **App & window detection**: tracks the active application and window title every ~5 seconds
- **Browser deep tracking**: captures the current tab's URL and title — distinguishes "watching videos" from "reading docs" even in the same browser
- **Background audio detection**: detects Apple Music, Spotify, and in-browser video/audio playing in the background
- **Auto-classification**: labels activity records every 5 minutes using hardcoded rules → domain cache → LLM fallback
- **AI classification toggle**: disable LLM calls from the settings page without affecting rule-based classification
- **Custom API config**: supports Anthropic official keys or third-party proxies (OpenRouter, DeepSeek, etc.) — configured via a first-launch onboarding wizard, model name auto-detected

### Menu Bar UI
- Status bar icon shows today's total active time (or 🥚 when idle)
- Click to expand: per-category time cards + stacked bar chart
- Learning goal / entertainment limit progress bars
- Wellness reminder card: random suggestions for stretching, water, eye breaks
- Date navigation (‹/›) to browse historical days
- **Standup reminder**: pops an alert after N minutes of continuous use; interval configurable in settings
- **Persistent settings**: chart type, wellness toggle, report time, standup interval — all saved to local JSON

### Weekly / Monthly Reports
- Rendered in a WKWebView window with sidebar navigation
- **Settings page**: toggle AI reports on/off; configure API key and base URL
- **Weekly report**: overview (stacked bar + category cards) / time breakdown (donut chart + sub-category bars) / book notes
- **Monthly report**:
  - Overview: weekly bar chart + category cards + **time-of-day heatmap** (morning/afternoon/evening × every day of the month, dynamic blue scale)
  - Time ranking: smooth line chart (category switcher on the right) + sub-category progress bars
- Weekly / monthly reflections: editable directly in the window, saved to the database
- Chart.js bundled locally — no network dependency

### Book Notes
- Log title / author / date read / tags / notes
- Add / edit / delete, with a custom in-app confirm dialog to prevent accidental deletion

## Supported Browsers

- Safari
- Google Chrome
- Microsoft Edge

## Requirements

- macOS 12+ (Apple Silicon or Intel)
- Python 3.10+ (3.12+ recommended)
- pyobjc

## Install Dependencies

```bash
pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz anthropic
```

AI classification requires an API key. **On first launch, an onboarding wizard will appear** — just follow the prompts. No need to manually set environment variables.

## Permissions

Go to **System Settings → Privacy & Security → Accessibility** and add the terminal you're using (Terminal / iTerm2) to the allow list. Without this, XiaoDan cannot read window titles.

## Run

```bash
cd 桌面监控程序
python3 tracker.py
```

## Auto-start with launchd

Copy `com.user.mactracker.plist` to `~/Library/LaunchAgents/`, then:

```bash
launchctl load ~/Library/LaunchAgents/com.user.mactracker.plist
```

> Note: the plist uses absolute paths (launchd doesn't expand `${HOME}`). Update them if you move to a different machine.

Logs are written to `~/Library/Logs/XiaoDan/tracker.log`.

## Build as .app (optional)

```bash
pip install py2app
./build.sh
# Output: dist/小蛋.app
```

`build.sh` runs: clean → py2app → ad-hoc codesign (required on Apple Silicon, otherwise the system kills it on launch).

> Current build uses ad-hoc signing — for personal use only, not distributable via the Mac App Store.

## Database Schema

```sql
CREATE TABLE activity_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT,
    date          TEXT,
    app_name      TEXT,
    window_title  TEXT,
    activity_type TEXT,   -- "app" | "browser" | "video" | "idle" | "dock"
    url           TEXT,
    bg_music      TEXT,
    category      TEXT,   -- e.g. "Self-learning/Programming"
    classified_at TEXT
);

-- Other tables:
-- domain_categories   domain classification cache
-- daily_reports       LLM-generated daily summaries
-- book_notes          reading log
-- weekly_reflections  weekly notes
-- monthly_reflections monthly notes
```

## File Structure

```
桌面监控程序/
├── tracker.py              # background recorder (main entry point)
├── ui.py                   # menu bar UI (AppKit)
├── report_window.py        # weekly/monthly/settings window (WKWebView)
├── onboarding_window.py    # first-launch onboarding wizard (WKWebView)
├── classifier.py           # activity classification engine
├── analyzer.py             # data aggregation + LLM report generation
├── wellness.py             # wellness reminder data
├── settings.py             # persistent settings (JSON read/write)
├── standup_reminder.py     # standup timer
├── wellness_activities.json
├── chart.umd.min.js        # Chart.js local copy (offline rendering)
├── standup_icon.png        # standup alert custom icon
├── XiaoDan.icns            # app icon
├── setup.py                # py2app build config
├── build.sh                # build script (py2app + codesign)
└── launch_tracker.sh       # manual launch script (for debugging)

~/Library/Application Support/XiaoDan/
├── activity.db             # SQLite database (local, never uploaded)
└── settings.json           # user settings

~/Library/Logs/XiaoDan/
└── tracker.log             # runtime log (redirected by launchd)
```
