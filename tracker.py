#!/usr/bin/env python3
"""
小蛋 — Mac使用时间监控工具（含状态栏）
=======================================================
依赖安装：
    pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz rumps

系统权限要求：
    请前往「系统设置 → 隐私与安全性 → 辅助功能」，
    将运行此脚本的 Python 解释器添加到允许列表。
    未授权时辅助功能 API 无法读取窗口标题，该字段将显示为空。
"""

import time
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta

import rumps

# ── 依赖导入 ──────────────────────────────────────────────────────────────────
try:
    from AppKit import NSWorkspace, NSRunningApplication, NSScreen
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-Cocoa")

try:
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
    )
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-ApplicationServices")

try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        CGEventCreate,
        CGEventGetLocation,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowListExcludeDesktopElements,
        kCGNullWindowID,
        kCGWindowOwnerName,
        kCGWindowName,
        kCGWindowBounds,
        kCGWindowLayer,
        kCGWindowOwnerPID,
    )
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-Quartz")


# ── 配置 ──────────────────────────────────────────────────────────────────────
POLL_INTERVAL = 30       # 检测间隔（秒），同时是状态栏刷新间隔
SLEEP_DETECT_THRESHOLD = POLL_INTERVAL * 3  # 间隔超过此值视为系统休眠
DB_PATH = os.path.expanduser("~/Documents/xiaodan-mac-tracker/activity.db")

# 统计时过滤掉的系统的应用（不计入使用时长）
FILTER_APPS = {
    "通知中心", "系统设置", "程序坞", "控制中心",
    "universalAccessAuthWarn", "Window Server", "Spotlight",
    "查找", "启动台", "", "空闲",
}

# bundle ID → AppleScript 英文名（AppleScript 不受系统语言影响）
BROWSER_BUNDLE_IDS = {
    "com.apple.Safari": "Safari",
    "com.google.Chrome": "Google Chrome",
    "com.microsoft.edgemac": "Microsoft Edge",
}

DOCK_BUNDLE_ID = "com.apple.dock"

VIDEO_PATTERNS = ("youtube.com/watch", "bilibili.com")

MUSIC_SITE_PATTERNS = (
    "music.youtube.com",
    "open.spotify.com",
    "music.apple.com",
    "music.163.com",
    "y.qq.com",
)

# AX 属性名（直接用字符串常量，兼容所有 pyobjc 版本）
_AX_FOCUSED_WINDOW = "AXFocusedWindow"
_AX_TITLE = "AXTitle"


# ── 线程安全共享状态 ─────────────────────────────────────────────────────────
class SharedState:
    """tracking 线程和 UI 线程之间的共享状态。"""
    def __init__(self):
        self._lock = threading.Lock()
        self.app_name = "启动中…"
        self.window_title = ""
        self.activity_type = "idle"

    def update(self, app_name: str, window_title: str, activity_type: str):
        with self._lock:
            self.app_name = app_name
            self.window_title = window_title
            self.activity_type = activity_type

    def snapshot(self) -> tuple[str, str, str]:
        with self._lock:
            return self.app_name, self.window_title, self.activity_type


state = SharedState()


# ── AppleScript 辅助 ──────────────────────────────────────────────────────────
def run_applescript(script: str, timeout: int = 4) -> str | None:
    """执行 AppleScript，返回去首尾空格的输出，失败返回 None。"""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


# ── 主活动检测 ────────────────────────────────────────────────────────────────
def get_frontmost_app() -> tuple[str | None, int | None, str | None]:
    """返回 (应用本地化名称, PID, bundle_id)。"""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app:
            bid = app.bundleIdentifier()
            return app.localizedName(), app.processIdentifier(), str(bid) if bid else None
    except Exception as e:
        print(f"  [警告] 获取前台应用失败: {e}")
    return None, None, None


def get_bundle_id(pid: int) -> str | None:
    """通过 PID 获取应用的 bundle ID。"""
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bid = app.bundleIdentifier() if app else None
        return str(bid) if bid else None
    except Exception:
        return None


def get_focused_window_title(pid: int) -> str | None:
    """通过辅助功能 API 获取该应用当前有键盘焦点的窗口标题。"""
    try:
        elem = AXUIElementCreateApplication(pid)
        err, window = AXUIElementCopyAttributeValue(elem, _AX_FOCUSED_WINDOW, None)
        if err != 0 or not window:
            return None
        err, title = AXUIElementCopyAttributeValue(window, _AX_TITLE, None)
        if err != 0:
            return None
        return str(title) if title else None
    except Exception as e:
        print(f"  [警告] 获取焦点窗口标题失败 (pid={pid}): {e}")
        return None


def _get_mouse_pos() -> tuple[float, float]:
    """返回当前鼠标的 Quartz 坐标 (x, y)，失败返回 (-1, -1)。"""
    try:
        event = CGEventCreate(None)
        pos = CGEventGetLocation(event)
        return pos.x, pos.y
    except Exception:
        return -1.0, -1.0


def get_window_under_mouse() -> tuple[str | None, str | None, int | None]:
    """
    返回鼠标当前所在的最上层窗口 (app_name, window_title, pid)。
    鼠标在桌面（无窗口命中）时返回 (None, None, None)。

    坐标系说明：CGEventGetLocation 和 CGWindowListCopyWindowInfo
    都使用 Quartz 坐标系（屏幕左上角为原点，Y 轴向下），无需转换。
    """
    try:
        event = CGEventCreate(None)
        mouse = CGEventGetLocation(event)
        mx, my = mouse.x, mouse.y
    except Exception as e:
        print(f"  [警告] 获取鼠标位置失败: {e}")
        return None, None, None

    try:
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
    except Exception as e:
        print(f"  [警告] 获取窗口列表失败: {e}")
        return None, None, None

    if not windows:
        return None, None, None

    for win in windows:
        try:
            bounds = win.get(kCGWindowBounds) or {}
            x = bounds.get("X", 0)
            y = bounds.get("Y", 0)
            w = bounds.get("Width", 0)
            h = bounds.get("Height", 0)

            if w <= 0 or h <= 0:
                continue
            if not (x <= mx <= x + w and y <= my <= y + h):
                continue

            app_name = win.get(kCGWindowOwnerName) or None
            window_title = win.get(kCGWindowName) or None
            pid = win.get(kCGWindowOwnerPID)
            return app_name, window_title, pid
        except Exception:
            continue

    return None, None, None  # 鼠标在桌面，无窗口命中


def _browser_active_tab_script(browser: str) -> str:
    """生成获取浏览器当前活跃标签 URL 和标题的 AppleScript。"""
    if browser == "Safari":
        return """
if application "Safari" is running then
    tell application "Safari"
        if (count of windows) > 0 then
            set t to current tab of front window
            return (URL of t) & "|||" & (name of t)
        end if
    end tell
end if
return ""
"""
    if browser == "Google Chrome":
        return """
if application "Google Chrome" is running then
    tell application "Google Chrome"
        if (count of windows) > 0 then
            set t to active tab of front window
            return (URL of t) & "|||" & (title of t)
        end if
    end tell
end if
return ""
"""
    if browser == "Microsoft Edge":
        return """
if application "Microsoft Edge" is running then
    tell application "Microsoft Edge"
        if (count of windows) > 0 then
            set t to active tab of front window
            return (URL of t) & "|||" & (title of t)
        end if
    end tell
end if
return ""
"""
    return 'return ""'


def get_browser_active_tab(browser: str) -> tuple[str | None, str | None]:
    """返回浏览器当前标签的 (url, title)，拿不到时返回 (None, None)。"""
    try:
        output = run_applescript(_browser_active_tab_script(browser))
        if output and "|||" in output:
            url, title = output.split("|||", 1)
            return url.strip(), title.strip()
    except Exception as e:
        print(f"  [警告] 获取 {browser} 当前标签失败: {e}")
    return None, None


def detect_main_activity(
    app_name: str | None, window_title: str | None, bundle_id: str | None
) -> dict:
    """
    检测主活动，返回：
      display  显示文字
      type     "video" | "browser" | "app" | "idle" | "dock"
      url      当前标签 URL（仅浏览器有效，否则为 None）
    """
    if app_name is None:
        return {"display": "idle", "type": "idle", "url": None}

    browser_name = BROWSER_BUNDLE_IDS.get(bundle_id) if bundle_id else None
    if browser_name:
        url, page_title = get_browser_active_tab(browser_name)
        label = page_title or window_title or app_name

        if url:
            if any(p in url for p in VIDEO_PATTERNS):
                short = url.split("?")[0]
                return {
                    "display": f"{app_name} — {label}（{short}）→ 看视频",
                    "type": "video",
                    "url": url,
                }
            display_url = (url[:70] + "…") if len(url) > 70 else url
            return {
                "display": f"{app_name} — {label}（{display_url}）",
                "type": "browser",
                "url": url,
            }
        title_part = f" — {window_title}" if window_title else ""
        return {"display": f"{app_name}{title_part}", "type": "browser", "url": None}

    title_part = f" — {window_title}" if window_title else ""
    return {"display": f"{app_name}{title_part}", "type": "app", "url": None}


# ── 背景音乐检测 ──────────────────────────────────────────────────────────────
def check_local_music() -> str | None:
    """检测 Apple Music / Spotify，返回 '来源 — 歌曲 / 艺术家' 或 None。"""
    apple_music_script = """
if application "Music" is running then
    tell application "Music"
        if player state is playing then
            return "playing|||" & (name of current track) & "|||" & (artist of current track)
        end if
    end tell
end if
return "stopped"
"""
    try:
        out = run_applescript(apple_music_script)
        if out and out.startswith("playing|||"):
            _, track, artist = out.split("|||", 2)
            return f"Apple Music — {track} / {artist}"
    except Exception as e:
        print(f"  [警告] Apple Music 检测失败: {e}")

    spotify_script = """
if application "Spotify" is running then
    tell application "Spotify"
        if player state is playing then
            return "playing|||" & (name of current track) & "|||" & (artist of current track)
        end if
    end tell
end if
return "stopped"
"""
    try:
        out = run_applescript(spotify_script)
        if out and out.startswith("playing|||"):
            _, track, artist = out.split("|||", 2)
            return f"Spotify — {track} / {artist}"
    except Exception as e:
        print(f"  [警告] Spotify 检测失败: {e}")

    return None


def _all_tabs_script(browser: str) -> str:
    """生成获取浏览器全部标签 URL 和标题的 AppleScript。"""
    if browser == "Safari":
        return """
if application "Safari" is running then
    tell application "Safari"
        set out to ""
        repeat with w in windows
            repeat with t in tabs of w
                set out to out & (URL of t) & "|||" & (name of t) & (ASCII character 10)
            end repeat
        end repeat
        return out
    end tell
end if
return ""
"""
    if browser == "Google Chrome":
        return """
if application "Google Chrome" is running then
    tell application "Google Chrome"
        set out to ""
        repeat with w in windows
            repeat with t in tabs of w
                set out to out & (URL of t) & "|||" & (title of t) & (ASCII character 10)
            end repeat
        end repeat
        return out
    end tell
end if
return ""
"""
    if browser == "Microsoft Edge":
        return """
if application "Microsoft Edge" is running then
    tell application "Microsoft Edge"
        set out to ""
        repeat with w in windows
            repeat with t in tabs of w
                set out to out & (URL of t) & "|||" & (title of t) & (ASCII character 10)
            end repeat
        end repeat
        return out
    end tell
end if
return ""
"""
    return 'return ""'


def get_all_browser_tabs(browser: str) -> list[tuple[str, str]]:
    """返回浏览器所有标签的 [(url, title)] 列表。"""
    try:
        output = run_applescript(_all_tabs_script(browser), timeout=6)
        if not output:
            return []
        tabs = []
        for line in output.splitlines():
            line = line.strip()
            if "|||" in line:
                url, title = line.split("|||", 1)
                tabs.append((url.strip(), title.strip()))
        return tabs
    except Exception as e:
        print(f"  [警告] 获取 {browser} 标签列表失败: {e}")
        return []


def check_browser_background_music(front_bundle_id: str | None) -> str | None:
    """
    扫描所有浏览器后台标签，推断是否有背景音乐播放。
    判断依据为 URL 匹配，无法确认媒体真正在播放。
    """
    for bundle_id, browser_name in BROWSER_BUNDLE_IDS.items():
        try:
            for url, title in get_all_browser_tabs(browser_name):
                is_video = any(p in url for p in VIDEO_PATTERNS)
                is_music_site = any(p in url for p in MUSIC_SITE_PATTERNS)
                browser_is_front = front_bundle_id == bundle_id

                if is_video and not browser_is_front:
                    return f"{browser_name}（后台视频）— {title}"

                if is_music_site:
                    return f"{browser_name}（在线音乐）— {title}"

        except Exception as e:
            print(f"  [警告] 检查 {browser_name} 背景媒体失败: {e}")

    return None


def detect_background_music(front_bundle_id: str | None, main_type: str) -> str:
    """综合检测背景音乐，返回显示字符串。"""
    local = check_local_music()
    if local:
        return local

    if main_type == "video":
        return "已计入主活动"

    browser_bg = check_browser_background_music(front_bundle_id)
    if browser_bg:
        return browser_bg

    return "无"


# ── 数据库 ───────────────────────────────────────────────────────────────────
def init_db():
    """初始化数据库表结构（如不存在则创建）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            date          TEXT NOT NULL,
            app_name      TEXT,
            window_title  TEXT,
            activity_type TEXT,
            url           TEXT,
            bg_music      TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON activity_log(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app  ON activity_log(app_name)")
    conn.commit()
    conn.close()


def _last_record(conn: sqlite3.Connection) -> dict | None:
    """取最后一条记录，返回 dict 或 None。"""
    row = conn.execute(
        """SELECT timestamp, date, app_name, window_title, activity_type, url, bg_music
           FROM activity_log ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    return {
        "timestamp": row[0], "date": row[1], "app_name": row[2],
        "window_title": row[3], "activity_type": row[4],
        "url": row[5], "bg_music": row[6],
    }


def save_to_db(
    conn: sqlite3.Connection,
    timestamp: str,
    date: str,
    app_name: str | None,
    window_title: str | None,
    main: dict,
    bg: str,
) -> None:
    """
    写入数据库。
    合并策略：如果最后一条记录与当前活动的 (app_name, window_title, activity_type, url) 完全相同，
    则跳过插入（不新增行）。这样相同活动只留一行，统计时用 LEAD(timestamp) 算真实时长。
    """
    last = _last_record(conn)
    new_key = (app_name or "", window_title or "", main.get("type", ""), main.get("url") or "")

    if last is not None:
        last_key = (last["app_name"] or "", last["window_title"] or "",
                     last["activity_type"] or "", last["url"] or "")
        if last_key == new_key:
            # 完全相同，跳过插入（合并）
            return

    try:
        conn.execute(
            """INSERT INTO activity_log
               (timestamp, date, app_name, window_title, activity_type, url, bg_music)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                date,
                app_name or "",
                window_title or "",
                main.get("type", ""),
                main.get("url") or "",
                bg,
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"  [警告] 写入数据库失败: {e}")


def calc_duration_seconds(conn: sqlite3.Connection, dates: list[str]) -> dict[str, float]:
    """
    用 LEAD() 窗口函数计算指定日期列表中每一条记录的真实时长（秒）。
    休眠间隔（两条记录间隔 > SLEEP_DETECT_THRESHOLD）不计入任何应用。
    自动过滤 FILTER_APPS 中的系统应用。
    参数：
        dates: ["2026-06-14", "2026-06-13", ...]
    返回 {app_name: total_seconds}。
    """
    placeholders = ",".join(["?"] * len(dates))
    sql = f"""
        WITH with_next AS (
            SELECT
                app_name,
                timestamp AS start_ts,
                LEAD(timestamp) OVER (ORDER BY timestamp) AS next_ts
            FROM activity_log
            WHERE date IN ({placeholders})
        )
        SELECT app_name, start_ts, next_ts
        FROM with_next
        WHERE next_ts IS NOT NULL
    """
    cur = conn.cursor()
    cur.execute(sql, dates)
    rows = cur.fetchall()

    app_seconds: dict[str, float] = {}
    for app_name, start_ts, next_ts in rows:
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            start = datetime.strptime(start_ts, fmt)
            end = datetime.strptime(next_ts, fmt)
            gap = (end - start).total_seconds()
            if gap > SLEEP_DETECT_THRESHOLD:
                continue
            name = app_name or "未知"
            if name in FILTER_APPS:
                continue
            app_seconds[name] = app_seconds.get(name, 0.0) + gap
        except Exception:
            continue

    return app_seconds


def _trunc(s: str, max_len: int = 8) -> str:
    """截断字符串，超过max_len用…替代"""
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


def _display_width(s: str) -> int:
    """计算字符串在等宽字体中的显示宽度（中文/全角算2，英文算1）"""
    w = 0
    for c in s:
        if ord(c) > 127:
            w += 2
        else:
            w += 1
    return w


def _format_stats(app_seconds: dict[str, float], date: str = "", conn: sqlite3.Connection = None) -> str:
    """格式化统计文本，分隔线与最长内容行等宽。"""
    if not app_seconds:
        return "暂无数据"

    total_seconds = sum(app_seconds.values())
    total_h = int(total_seconds // 3600)
    total_m = int((total_seconds % 3600) // 60)

    # ── Top 应用行 ──
    top_lines: list[str] = []
    for name, secs in sorted(app_seconds.items(), key=lambda x: -x[1]):
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        tstr = (f"{h}h{m:02d}m" if h > 0 else f"{m}m")
        pct = int(secs / total_seconds * 100) if total_seconds > 0 else 0
        short = _trunc(name, 8)
        top_lines.append("  " + short.ljust(8) + "  " + tstr.rjust(6) + "  " + str(pct).rjust(3) + "%")

    # ── 活跃时间段 ──
    active_line = ""
    if conn and date:
        try:
            row = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM activity_log WHERE date = ?",
                (date,)
            ).fetchone()
            if row and row[0]:
                active_line = "活跃  " + str(row[0][11:16]) + " ~ " + str(row[1][11:16])
        except Exception:
            pass

    # ── 组装所有行，计算最大显示宽度 ──
    all_lines: list[str] = []
    total_str = (f"{total_h}h {total_m:02d}m" if total_h > 0 else f"{total_m}m")
    all_lines.append("总时长  " + total_str)
    all_lines.extend(top_lines)
    if active_line:
        all_lines.append(active_line)
    all_lines.append("共 " + str(len(app_seconds)) + " 个应用")

    max_width = max(_display_width(line) for line in all_lines)

    # ── 输出（分隔线与最长行等宽，用半角-保证视觉对齐） ──
    sep = "-" * max_width
    out: list[str] = []
    out.append(all_lines[0])       # 总时长
    out.append(sep)
    out.append("")
    out.extend(all_lines[1:-1])   # Top 应用 + 活跃时间段
    out.append(sep)
    out.append("")
    out.append(all_lines[-1])      # 底部信息
    return "\n".join(out)


# ── 状态栏 App ────────────────────────────────────────────────────────────────
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")

class TrackerStatusBarApp(rumps.App):
    def __init__(self):
        super().__init__(name="XiaoDan", title="启动中…", quit_button="退出小蛋")
        # 加载自定义图标（彩色模式，亮色蛋形）
        if os.path.exists(ICON_PATH):
            self.icon = ICON_PATH
        self.top1_item = rumps.MenuItem("…")
        self.top2_item = rumps.MenuItem("…")
        self.top3_item = rumps.MenuItem("…")
        self.menu = [
            self.top1_item,
            self.top2_item,
            self.top3_item,
            None,
            rumps.MenuItem("今日统计（完整）", callback=self.show_today_stats),
            rumps.MenuItem("本周统计", callback=self.show_week_stats),
        ]
        self._refresh_title(None)
        self.timer = rumps.Timer(self._refresh_title, POLL_INTERVAL)
        self.timer.start()

    def _refresh_title(self, _sender):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        app_seconds = calc_duration_seconds(conn, [today])
        conn.close()

        if not app_seconds:
            self.title = "暂无数据"
            for item in [self.top1_item, self.top2_item, self.top3_item]:
                item.title = ""
                item.color = None
            return

        ranked = sorted(app_seconds.items(), key=lambda x: -x[1])

        top_name, top_secs = ranked[0]
        h, m = int(top_secs // 3600), int((top_secs % 3600) // 60)
        label = f"{top_name} " + (f"{h}h {m:02d}m" if h > 0 else f"{m}m")
        self.title = label

        DEEP_BLUE = (0.0, 0.18, 0.65, 1.0)
        for i, item in enumerate([self.top1_item, self.top2_item, self.top3_item]):
            if i < len(ranked):
                name, secs = ranked[i]
                h, m = int(secs // 3600), int((secs % 3600) // 60)
                tstr = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
                item.title = f"{i + 1}. {name}  {tstr}"
                item.color = DEEP_BLUE
            else:
                item.title = ""
                item.color = None

    def show_today_stats(self, _sender):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        app_seconds = calc_duration_seconds(conn, [today])
        msg = _format_stats(app_seconds, date=today, conn=conn)
        conn.close()
        rumps.alert(title=f"今日统计 — {today}", message=msg)

    def show_week_stats(self, _sender):
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        dates = []
        d = monday
        while d.date() <= today.date():
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        conn = sqlite3.connect(DB_PATH)
        app_seconds = calc_duration_seconds(conn, dates)
        week_label = f"{dates[0]} ~ {dates[-1]}"
        msg = _format_stats(app_seconds, date=week_label)
        conn.close()
        rumps.alert(title=f"本周统计 — {week_label}", message=msg)


# ── 追踪线程 ──────────────────────────────────────────────────────────────────
def tracking_loop():
    """后台追踪循环，更新共享状态并写入数据库。"""
    conn = sqlite3.connect(DB_PATH)
    last_loop_time = time.time()

    while True:
        try:
            now = datetime.now()
            now_ts = time.time()
            timestamp_db = now.strftime("%Y-%m-%d %H:%M:%S")
            date_db = now.strftime("%Y-%m-%d")

            # 休眠检测：上次循环到这次超过阈值，视为系统睡过
            gap = now_ts - last_loop_time
            just_woke = gap > SLEEP_DETECT_THRESHOLD
            if just_woke:
                # 刚唤醒，跳过这次写入（不把睡眠时间算进任何应用）
                last_loop_time = now_ts
                time.sleep(POLL_INTERVAL)
                continue

            # 主活动：优先用鼠标所在窗口
            app_name, window_title, pid = get_window_under_mouse()
            bundle_id: str | None = None

            if app_name is not None:
                bundle_id = get_bundle_id(pid) if pid else None

                is_system_overlay = bundle_id == DOCK_BUNDLE_ID or app_name == "Window Server"
                if is_system_overlay:
                    is_from_dock = bundle_id == DOCK_BUNDLE_ID
                    front_name, front_pid, front_bundle = get_frontmost_app()
                    _not_real = {DOCK_BUNDLE_ID, "com.apple.finder", None}
                    if front_bundle not in _not_real and front_name != "Window Server":
                        app_name, pid, bundle_id = front_name, front_pid, front_bundle
                        window_title = get_focused_window_title(pid) if pid else None
                        if bundle_id == "com.tencent.xinWeChat":
                            window_title = "微信"
                        main = detect_main_activity(app_name, window_title, bundle_id)
                    elif is_from_dock:
                        main = {"display": "Dock（未计入活跃时间）", "type": "dock", "url": None}
                    else:
                        main = {"display": "idle", "type": "idle", "url": None}
                else:
                    if window_title is None and pid is not None:
                        window_title = get_focused_window_title(pid)
                    if bundle_id == "com.tencent.xinWeChat":
                        window_title = "微信"
                    main = detect_main_activity(app_name, window_title, bundle_id)
            else:
                app_name, pid, bundle_id = get_frontmost_app()
                window_title = get_focused_window_title(pid) if pid else None
                if bundle_id == "com.tencent.xinWeChat":
                    window_title = "微信"
                main = detect_main_activity(app_name, window_title, bundle_id)

            bg = detect_background_music(bundle_id, main["type"])

            display_name = (app_name or "空闲")
            state.update(display_name, window_title or "", main["type"])

            save_to_db(conn, timestamp_db, date_db, app_name, window_title, main, bg)

        except Exception:
            pass

        last_loop_time = time.time()
        time.sleep(POLL_INTERVAL)


# ── 入口 ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    print(f"📦 小蛋数据库已就绪：{DB_PATH}")

    t = threading.Thread(target=tracking_loop, daemon=True)
    t.start()
    print("🥚 小蛋已启动，开始记录你的一天（状态栏常驻）")

    app = TrackerStatusBarApp()
    app.run()


if __name__ == "__main__":
    main()
