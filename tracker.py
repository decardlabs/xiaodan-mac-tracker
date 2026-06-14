#!/usr/bin/env python3
"""
小蛋 — Mac使用时间监控工具（含状态栏）
=======================================================
依赖安装：
    pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz

系统权限要求：
    请前往「系统设置 → 隐私与安全性 → 辅助功能」，
    将运行此脚本的 Python 解释器添加到允许列表。
    未授权时辅助功能 API 无法读取窗口标题，该字段将显示为空。

架构说明：
    状态栏 UI 使用纯 PyObjC（不依赖 rumps），
    因为 macOS 26 (Tahoe) 下 rumps 在 py2app 打包后无法注册菜单栏项。
"""

import time
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta

try:
    import setproctitle
    setproctitle.setproctitle("XiaoDan")
except ImportError:
    pass  # 没装 setproctitle 也不致命，只是在 ps 里显示成 Python

# ── PyObjC 导入（状态栏 UI 必需） ────────────────────────────────────────────
from AppKit import (
    NSApplication, NSStatusBar, NSStatusItem, NSMenu, NSMenuItem,
    NSImage, NSAlert, NSScreen, NSWorkspace, NSRunningApplication,
    NSVariableStatusItemLength, NSApplicationActivationPolicyAccessory,
    NSUserNotification, NSUserNotificationCenter, NSAutoreleasePool,
    NSObject,
)
from Foundation import (
    NSTimer, NSThread, NSDate,
    NSLog,
)
from PyObjCTools import AppHelper
import objc  # @objc.python_method 和 super() 需要

# ── 其他依赖导入 ──────────────────────────────────────────────────────────────
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

# ── 路径配置（兼容 .app Bundle 和源码运行） ────────────────────────────────
_RES_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = (
    os.path.dirname(os.path.dirname(_RES_DIR))   # .app/Contents
    if os.path.basename(_RES_DIR) == "Resources"
    else os.path.dirname(_RES_DIR)                # 源码运行：项目根
)
RES_DIR = _RES_DIR

# 用户数据目录（macOS 标准位置）
APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/XiaoDan")
os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
DB_PATH = os.path.join(APP_SUPPORT_DIR, "activity.db")
# 旧数据库自动迁移（如有）
_OLD_DB = os.path.expanduser("~/Documents/xiaodan-mac-tracker/activity.db")
if os.path.exists(_OLD_DB) and not os.path.exists(DB_PATH):
    import shutil
    try:
        shutil.copy2(_OLD_DB, DB_PATH)
    except Exception:
        pass

# 统计时过滤掉的系统的应用（不计入使用时长）
FILTER_APPS = {
    "通知中心", "系统设置", "程序坞", "控制中心",
    "universalAccessAuthWarn", "Window Server", "Spotlight",
    "查找", "启动台", "", "空闲",
}

# bundle ID → AppleScript 英文名
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

# AX 属性名
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

    return None, None, None


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
            return

    try:
        conn.execute(
            """INSERT INTO activity_log
               (timestamp, date, app_name, window_title, activity_type, url, bg_music)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp, date, app_name or "", window_title or "",
                main.get("type", ""), main.get("url") or "", bg,
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


# ══════════════════════════════════════════════════════════════════════════════
#  状态栏 UI — 纯 PyObjC 实现（替代 rumps）
# ══════════════════════════════════════════════════════════════════════════════

def _find_icon():
    """查找图标文件路径"""
    candidates = [
        os.path.join(RES_DIR, "icon.png"),
        os.path.join(RES_DIR, "icon@2x.png"),
        os.path.join(RES_DIR, "icon.icns"),
        os.path.join(APP_ROOT, "icon.png"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

ICON_PATH = _find_icon()


class XiaoDanDelegate(NSObject):
    """NSApplication 的 delegate，管理状态栏和菜单。"""

    def init(self):
        self = objc.super(XiaoDanDelegate, self).init()
        if self is None:
            return None
        self.status_item = None
        self.top1_item = None
        self.top2_item = None
        self.top3_item = None
        self.timer = None
        self.tracker = None   # 引用 TrackerApp 实例
        return self

    def applicationDidFinishLaunching_(self, notification):
        """应用启动完成——创建状态栏项、设置菜单、启动定时器。"""
        NSLog("[XiaoDan] applicationDidFinishLaunching_")
        self.tracker = _tracker_instance

        # ── 创建状态栏项 ──
        bar = NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.setHighlightMode_(True)
        self.status_item.setToolTip_("小蛋 — Mac 使用时间追踪")

        # 设置图标（template 模式，系统自动着色适配亮/暗模式）
        icon_set = False
        if ICON_PATH and os.path.exists(ICON_PATH):
            nsimg = NSImage.alloc().initByReferencingFile_(ICON_PATH)
            if nsimg and nsimg.isValid():
                nsimg.setTemplate_(True)  # 关键！macOS 26 要求 template image
                nsimg.setSize_((18, 18))
                self.status_item.setImage_(nsimg)
                icon_set = True
                NSLog(f"[XiaoDan] Icon loaded: {ICON_PATH}, template=True")

        # 如果没图标就设文字 fallback
        if not icon_set:
            self.status_item.setTitle_("XD")
            NSLog("[XiaoDan] No icon found, using text fallback")

        self.status_item.setVisible_(True)

        # ── 构建菜单 ──
        menu = NSMenu.alloc().init()

        # Top 1-3 动态条目
        self.top1_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("…", None, "")
        self.top2_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("…", None, "")
        self.top3_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("…", None, "")

        menu.addItem_(self.top1_item)
        menu.addItem_(self.top2_item)
        menu.addItem_(self.top3_item)

        # 分隔线
        sep = NSMenuItem.separatorItem()
        menu.addItem_(sep)

        # 今日统计
        today_stats = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "今日统计（完整）", "todayStats:", "")
        today_stats.setTarget_(self)
        menu.addItem_(today_stats)

        # 本周统计
        week_stats = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "本周统计", "weekStats:", "")
        week_stats.setTarget_(self)
        menu.addItem_(week_stats)

        # 分隔线
        sep2 = NSMenuItem.separatorItem()
        menu.addItem_(sep2)

        # 安装辅助功能权限
        acc_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "安装辅助功能权限…", "openAccSettings:", "")
        acc_item.setTarget_(self)
        menu.addItem_(acc_item)

        # 分隔线
        sep3 = NSMenuItem.separatorItem()
        menu.addItem_(sep3)

        # 退出
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出小蛋", "terminate:", "q")
        menu.addItem_(quit_item)

        self.status_item.setMenu_(menu)

        # ── 启动定时刷新定时器 ──
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL, self, "refreshTitle:", None, True)

        # 首次立即刷新
        self.performSelector_withObject_afterDelay_("refreshTitle:", None, 0.5)

        # ── 2 秒后检查辅助功能权限 ──
        self.performSelector_withObject_afterDelay_("checkAccessibility:", None, 2.0)

        NSLog("[XiaoDan] Status bar setup complete!")

    # ── 定时器回调：刷新状态栏标题 ──
    # 注意：NSTimer 通过 selector 调用 → 方法必须对 Objective-C runtime 可见。
    # 不能用 @objc.python_method（那会让方法在 OC 侧不可见，NSTimer 静默失败）。
    # PyObjC 命名约定：带冒号的 selector 写作 "method_"，OC 调用时自动映射为 method:
    def refreshTitle_(self, timer):
        """刷新状态栏文字 + Top N 菜单项内容。NSTimer 回调。"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            app_seconds = calc_duration_seconds(conn, [today])
            conn.close()

            if not app_seconds:
                self.status_item.setTitle_("暂无数据")
                if self.top1_item:
                    self.top1_item.setTitle_("")
                if self.top2_item:
                    self.top2_item.setTitle_("")
                if self.top3_item:
                    self.top3_item.setTitle_("")
                return

            ranked = sorted(app_seconds.items(), key=lambda x: -x[1])
            top_name, top_secs = ranked[0]
            h, m = int(top_secs // 3600), int((top_secs % 3600) // 60)
            label = f"{top_name} " + (f"{h}h {m:02d}m" if h > 0 else f"{m}m")
            self.status_item.setTitle_(label)

            items = [self.top1_item, self.top2_item, self.top3_item]
            for i, item in enumerate(items):
                if item and i < len(ranked):
                    name, secs = ranked[i]
                    h2, m2 = int(secs // 3600), int((secs % 3600) // 60)
                    tstr = f"{h2}h {m2:02d}m" if h2 > 0 else f"{m2}m"
                    item.setTitle_(f"{i + 1}. {name}  {tstr}")
                elif item:
                    item.setTitle_("")
        except Exception as e:
            NSLog(f"[XiaoDan] refreshTitle error: {e}")

    # ── 菜单回调 ──

    def todayStats_(self, sender):
        """今日统计弹窗"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            app_seconds = calc_duration_seconds(conn, [today])
            msg = _format_stats(app_seconds, date=today, conn=conn)
            conn.close()

            alert = NSAlert.alloc().init()
            alert.setMessageText_(f"今日统计 — {today}")
            alert.setInformativeText_(msg)
            alert.runModal()
        except Exception as e:
            NSLog(f"[XiaoDan] todayStats error: {e}")

    def weekStats_(self, sender):
        """本周统计弹窗"""
        try:
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

            alert = NSAlert.alloc().init()
            alert.setMessageText_(f"本周统计 — {week_label}")
            alert.setInformativeText_(msg)
            alert.runModal()
        except Exception as e:
            NSLog(f"[XiaoDan] weekStats error: {e}")

    def openAccSettings_(self, sender):
        """打开辅助功能设置面板"""
        subprocess.run([
            "open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ], check=False)

    # ── 辅助功能权限检查 ──
    # OC 可见方法（无 @objc.python_method），performSelector 才能调到
    def checkAccessibility_(self, sender):
        """检测辅助功能权限，未授权则通知用户。"""
        try:
            from ApplicationServices import AXIsProcessTrusted
            trusted = AXIsProcessTrusted()
        except Exception:
            return
        if not trusted:
            # 发送系统通知
            note = NSUserNotification.alloc().init()
            note.setTitle_("小蛋需要辅助功能权限")
            note.setInformativeText_(
                "请打开「系统设置 → 隐私与安全性 → 辅助功能」，勾选「小蛋」后重新启动。"
            )
            center = NSUserNotificationCenter.defaultUserNotificationCenter()
            center.deliverNotification_(note)


# 需要用到 @objc.python_method 和 super()（已在顶部 import objc）

_tracker_instance = None


class TrackerStatusBarApp:
    """
    小蛋状态栏应用 —— 纯 PyObjC 版本（不依赖 rumps）。
    
    替代方案的原因：
      macOS 26 (Tahoe) 下 rumps 在 py2app 打包后的 .app 中无法注册菜单栏项
      （System Events 显示 0 menu bars），而纯 PyObjC 同等代码正常工作。
    """

    def __init__(self):
        global _tracker_instance
        _tracker_instance = self
        self.delegate = None

    def run(self):
        """启动 NSApplication 主循环。"""
        pool = NSAutoreleasePool.alloc().init()

        ns_app = NSApplication.sharedApplication()
        # Accessory 模式：只出现在状态栏，不进 Dock
        ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.delegate = XiaoDanDelegate.alloc().init()
        ns_app.setDelegate_(self.delegate)

        NSLog("[XiaoDan] Starting event loop...")
        AppHelper.installMachInterrupt()
        AppHelper.runEventLoop()

        del pool


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

            # 休眠检测
            gap = now_ts - last_loop_time
            just_woke = gap > SLEEP_DETECT_THRESHOLD
            if just_woke:
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
    print(f"[XiaoDan] 数据库已就绪：{DB_PATH}")

    t = threading.Thread(target=tracking_loop, daemon=True)
    t.start()
    print("[XiaoDan] 追踪线程已启动")

    app = TrackerStatusBarApp()
    app.run()


if __name__ == "__main__":
    main()
