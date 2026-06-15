#!/usr/bin/env python3
"""
小蛋 — Mac使用时间监控工具
=======================================================
依赖安装：
    pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz

系统权限要求：
    请前往「系统设置 → 隐私与安全性 → 辅助功能」，
    将 Terminal（或 iTerm2 等终端）添加到允许列表。
    未授权时辅助功能 API 无法读取窗口标题，该字段将显示为空。
"""

import os
import shutil
import time
import threading
import sqlite3
import subprocess
from datetime import datetime, date, timedelta
import webbrowser

# ── 依赖导入 ──────────────────────────────────────────────────────────────────
import objc
from Foundation import NSObject, NSTimer

try:
    from AppKit import (
        NSWorkspace, NSRunningApplication, NSScreen,
        NSView, NSBezierPath, NSColor, NSFont, NSAttributedString,
        NSFontAttributeName, NSForegroundColorAttributeName,
        NSApplication, NSStatusBar, NSMenu, NSMenuItem,
        NSVariableStatusItemLength,
    )
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
POLL_INTERVAL = 5        # 检测间隔（秒）
SLEEP_DETECT_THRESHOLD = POLL_INTERVAL * 120  # 间隔超过此值视为系统休眠（10分钟）
DEBUG = True             # 开启后，Dock 检测时额外打印鼠标坐标和窗口信息

# 用户数据目录（macOS 标准位置）
APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/XiaoDan")
os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
DB_PATH = os.path.join(APP_SUPPORT_DIR, "activity.db")

# 旧数据库自动迁移：项目目录下的 activity.db → 新路径（仅首次）
_OLD_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity.db")
if os.path.exists(_OLD_DB) and not os.path.exists(DB_PATH):
    try:
        shutil.copy2(_OLD_DB, DB_PATH)
        print(f"[迁移] 数据库已复制：{_OLD_DB} → {DB_PATH}")
    except Exception as _e:
        print(f"[迁移] 复制失败：{_e}")

# 统计时过滤掉的系统应用（不计入使用时长）
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


# ── 线程安全共享状态 ──────────────────────────────────────────────────────────
class SharedState:
    """tracking 线程与其他模块之间的共享状态。"""
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

CLASSIFIER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "classifier.py")
_classifier_running = False


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
      type     "video" | "browser" | "app" | "idle"
      url      当前标签 URL（仅浏览器有效，否则为 None）
    """
    if app_name is None:
        return {"display": "idle", "type": "idle", "url": None}

    # window_title 可能因权限或 CGWindowList 限制而为 None，不视为 idle
    # 用 bundle ID 判断浏览器，避免中文系统名匹配失败
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
                    "page_title": page_title,
                }
            display_url = (url[:70] + "…") if len(url) > 70 else url
            return {
                "display": f"{app_name} — {label}（{display_url}）",
                "type": "browser",
                "url": url,
                "page_title": page_title,
            }
        # 拿不到 URL 时退化为普通应用显示
        title_part = f" — {window_title}" if window_title else ""
        return {"display": f"{app_name}{title_part}", "type": "browser", "url": None, "page_title": None}

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

                # 视频网站在后台 → 归为背景音乐
                if is_video and not browser_is_front:
                    return f"{browser_name}（后台视频）— {title}"

                # 专属音乐网站（无论前后台）→ 背景音乐
                if is_music_site:
                    return f"{browser_name}（在线音乐）— {title}"

        except Exception as e:
            print(f"  [警告] 检查 {browser_name} 背景媒体失败: {e}")

    return None


def detect_background_music(front_bundle_id: str | None, main_type: str) -> str:
    """综合检测背景音乐，返回显示字符串。"""
    # 1. 本地音乐客户端（优先级最高）
    local = check_local_music()
    if local:
        return local

    # 2. 主活动已是视频 → 不单独计入背景音乐
    if main_type == "video":
        return "无（已计入主活动）"

    # 3. 浏览器后台媒体（URL 推断）
    browser_bg = check_browser_background_music(front_bundle_id)
    if browser_bg:
        return browser_bg

    return "无"


# ── 数据库 ───────────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    """初始化数据库，返回连接。"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            date          TEXT NOT NULL,
            app_name      TEXT,
            window_title  TEXT,
            activity_type TEXT,
            url           TEXT,
            bg_music      TEXT,
            category      TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE activity_log ADD COLUMN category TEXT")
    except Exception:
        pass
    conn.commit()
    return conn


def _last_record(conn: sqlite3.Connection) -> dict | None:
    """取最后一条记录，返回 dict 或 None。"""
    row = conn.execute(
        """SELECT app_name, window_title, activity_type, url
           FROM activity_log ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    return {
        "app_name": row[0], "window_title": row[1],
        "activity_type": row[2], "url": row[3],
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
    # 合并策略：与最后一条记录完全相同则跳过，统计时用 LEAD(timestamp) 算真实时长
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
    用 LEAD() 窗口函数计算指定日期列表中每条记录的真实时长（秒）。
    两条记录间隔超过 SLEEP_DETECT_THRESHOLD 的视为休眠，不计入任何应用。
    自动过滤 FILTER_APPS 中的系统应用。
    """
    placeholders = ",".join(["?"] * len(dates))
    sql = f"""
        WITH with_next AS (
            SELECT
                app_name,
                timestamp AS start_ts,
                LEAD(timestamp) OVER (PARTITION BY date ORDER BY timestamp) AS next_ts
            FROM activity_log
            WHERE date IN ({placeholders})
        )
        SELECT app_name, start_ts, next_ts
        FROM with_next
        WHERE next_ts IS NOT NULL
    """
    rows = conn.execute(sql, dates).fetchall()

    app_seconds: dict[str, float] = {}
    for app_name, start_ts, next_ts in rows:
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            gap = (datetime.strptime(next_ts, fmt) - datetime.strptime(start_ts, fmt)).total_seconds()
            if gap <= 0 or gap > SLEEP_DETECT_THRESHOLD:
                continue
            name = app_name or "未知"
            if name in FILTER_APPS:
                continue
            app_seconds[name] = app_seconds.get(name, 0.0) + gap
        except Exception:
            continue

    return app_seconds


def get_category_stats(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    查询指定日期各分类时长（秒），基于 activity_log.category 字段（格式：一级/二级）。
    date_str 格式：'YYYY-MM-DD'

    返回结构：
        {
            "自主学习": {"total": 7500.0, "subs": {"编程学习": 4800.0, "AI工具": 2700.0}},
            "学校学习": {"total": 4320.0, "subs": {...}},
            ...
        }

    与 calc_duration_seconds() 使用相同的 LEAD() 算法和过滤规则：
    - 间隔超过 SLEEP_DETECT_THRESHOLD 的视为休眠，丢弃
    - FILTER_APPS 中的系统应用丢弃
    - category 为 NULL 或不含斜杠的记录归入「其他」
    """
    sql = """
        WITH with_next AS (
            SELECT
                app_name,
                category,
                timestamp AS start_ts,
                LEAD(timestamp) OVER (PARTITION BY date ORDER BY timestamp) AS next_ts
            FROM activity_log
            WHERE date = ?
        )
        SELECT app_name, category, start_ts, next_ts
        FROM with_next
        WHERE next_ts IS NOT NULL
    """
    rows = conn.execute(sql, [date_str]).fetchall()

    stats: dict = {}
    fmt = "%Y-%m-%d %H:%M:%S"

    for app_name, category, start_ts, next_ts in rows:
        try:
            gap = (
                datetime.strptime(next_ts, fmt) - datetime.strptime(start_ts, fmt)
            ).total_seconds()
        except Exception:
            continue
        if gap <= 0 or gap > SLEEP_DETECT_THRESHOLD:
            continue
        if (app_name or "") in FILTER_APPS:
            continue

        if category and "/" in category:
            l1, l2 = category.split("/", 1)
        elif category:
            l1, l2 = category, ""
        else:
            l1, l2 = "其他", ""

        entry = stats.setdefault(l1, {"total": 0.0, "subs": {}})
        entry["total"] += gap
        if l2:
            entry["subs"][l2] = entry["subs"].get(l2, 0.0) + gap

    return stats


# ── 图表视图 ──────────────────────────────────────────────────────────────────

CATEGORY_ORDER = ["学校学习", "自主学习", "娱乐", "其他"]

_CAT_RGB = {
    "学校学习": (0x5B / 255, 0x8D / 255, 0xEF / 255),
    "自主学习": (0x9B / 255, 0x72 / 255, 0xCF / 255),
    "娱乐":     (0x4E / 255, 0xCD / 255, 0xC4 / 255),
    "其他":     (0xB8 / 255, 0xBC / 255, 0xC8 / 255),
}


def _fmt_dur(secs: float) -> str:
    """将秒数格式化为「Xh Ym」（≥1h）或「Ym」（<1h）。"""
    total_m = max(0, int(secs)) // 60
    h, m = divmod(total_m, 60)
    return f"{h}h {m:02d}m" if h > 0 else f"{m}m"


def _nscolor(cat: str) -> "NSColor":
    r, g, b = _CAT_RGB.get(cat, _CAT_RGB["其他"])
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


class DonutChartView(NSView):
    """
    环形图（240×120pt）。
    用法：view._stats = get_today_category_stats(); view.setNeedsDisplay_(True)
    """

    def initWithFrame_(self, frame):
        self = objc.super(DonutChartView, self).initWithFrame_(frame)
        if self is not None:
            self._stats = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        stats = getattr(self, "_stats", {})
        total = sum(v["total"] for v in stats.values()) if stats else 0.0

        # ── 圆环参数（坐标原点左下角，y 向上）────────────────────────────────
        cx, cy = 62.0, 60.0
        outer_r = 36.0
        ring_w = round(outer_r * 0.35)        # ≈ 13pt，圆环宽度约为外径的35%
        mid_r = outer_r - ring_w / 2.0        # stroke 弧线半径

        # 灰色背景环（完整圆）
        bg_arc = NSBezierPath.bezierPath()
        bg_arc.setLineWidth_(ring_w)
        bg_arc.setLineCapStyle_(0)             # NSLineCapStyleButt = 0，端点不延伸
        bg_arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), mid_r, 90.0, -270.0, True
        )
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0).setStroke()
        bg_arc.stroke()

        # 各分类扇形（顺时针，从12点方向开始）
        if total > 0:
            angle = 90.0
            for cat in CATEGORY_ORDER:
                secs = stats.get(cat, {}).get("total", 0.0)
                if secs <= 0:
                    continue
                sweep = secs / total * 360.0
                end_angle = angle - sweep
                seg = NSBezierPath.bezierPath()
                seg.setLineWidth_(ring_w)
                seg.setLineCapStyle_(0)
                seg.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    (cx, cy), mid_r, angle, end_angle, True
                )
                _nscolor(cat).setStroke()
                seg.stroke()
                angle = end_angle

        # ── 圆心两行文字 ─────────────────────────────────────────────────────
        study = (stats.get("学校学习", {}).get("total", 0.0)
                 + stats.get("自主学习", {}).get("total", 0.0))
        pct = int(round(study / total * 100)) if total > 0 else 0
        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        lbl = NSAttributedString.alloc().initWithString_attributes_(
            "学习",
            {NSFontAttributeName: NSFont.systemFontOfSize_(9.0),
             NSForegroundColorAttributeName: gray},
        )
        pct_t = NSAttributedString.alloc().initWithString_attributes_(
            f"{pct}%",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(14.0),
             NSForegroundColorAttributeName: dark},
        )
        # 上行「学习」baseline 在圆心上方 3pt，下行「XX%」baseline 在圆心下方 13pt
        lbl.drawAtPoint_((cx - lbl.size().width / 2.0, cy + 3.0))
        pct_t.drawAtPoint_((cx - pct_t.size().width / 2.0, cy - 13.0))

        # ── 右侧图例 ─────────────────────────────────────────────────────────
        lx = 118.0            # 图例区左边缘 x
        box_top0 = 104.0      # 第一个色块顶部 y（从底部算）
        row_gap = 22.0        # 行间距（色块顶到下一行色块顶）

        txt_attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(10.0),
                     NSForegroundColorAttributeName: gray}

        for i, cat in enumerate(CATEGORY_ORDER):
            box_top = box_top0 - i * row_gap
            box_bot = box_top - 7.0            # 色块底部（7pt 高）

            # 彩色方块（7×7，圆角 1.5pt）
            _nscolor(cat).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((lx, box_bot), (7.0, 7.0)), 1.5, 1.5
            ).fill()

            # 短名（2字）+ 时长
            secs = stats.get(cat, {}).get("total", 0.0)
            line_t = NSAttributedString.alloc().initWithString_attributes_(
                f"{cat[:2]}  {_fmt_dur(secs)}", txt_attrs
            )
            line_t.drawAtPoint_((lx + 10.0, box_bot))


class BarChartView(NSView):
    """
    横向条形图（240×120pt），按时长降序排列。
    用法：view._stats = get_today_category_stats(); view.setNeedsDisplay_(True)
    """

    def initWithFrame_(self, frame):
        self = objc.super(BarChartView, self).initWithFrame_(frame)
        if self is not None:
            self._stats = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        stats = getattr(self, "_stats", {})

        # 按时长降序排序
        cats = sorted(CATEGORY_ORDER,
                      key=lambda c: stats.get(c, {}).get("total", 0.0),
                      reverse=True)
        max_secs = max((stats.get(c, {}).get("total", 0.0) for c in cats), default=0.0)
        if max_secs <= 0:
            max_secs = 1.0

        n = len(cats)
        bar_h = 6.0
        inner_gap = 3.0           # 标签到条形的垂直间距
        label_h = 11.0            # 11pt 字体的视觉行高（baseline 到 cap-top）
        row_h = label_h + inner_gap + bar_h   # 22pt
        inter_gap = 9.0           # 行与行之间的间距

        block_h = n * row_h + (n - 1) * inter_gap   # ≈ 115pt
        bot_margin = (120.0 - block_h) / 2.0         # 上下各 ≈ 2.5pt

        pad_l, pad_r = 8.0, 8.0
        avail_w = 240.0 - pad_l - pad_r              # 224pt

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        bg_col = NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0)
        txt_attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                     NSForegroundColorAttributeName: gray}

        for i, cat in enumerate(cats):
            # 行 0（最长）在顶部 → 最大 y 值；NSView y 从底部向上
            bar_bot = bot_margin + (n - 1 - i) * (row_h + inter_gap)
            lbl_baseline = bar_bot + bar_h + inner_gap

            secs = stats.get(cat, {}).get("total", 0.0)
            fill_w = (secs / max_secs) * avail_w

            # 背景灰条
            bg_col.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((pad_l, bar_bot), (avail_w, bar_h)), 3.0, 3.0
            ).fill()

            # 彩色填充条
            if fill_w >= 1.0:
                _nscolor(cat).set()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((pad_l, bar_bot), (fill_w, bar_h)), 3.0, 3.0
                ).fill()

            # 分类名（左对齐）
            cat_t = NSAttributedString.alloc().initWithString_attributes_(cat, txt_attrs)
            cat_t.drawAtPoint_((pad_l, lbl_baseline))

            # 时长（右对齐）
            dur_t = NSAttributedString.alloc().initWithString_attributes_(
                _fmt_dur(secs), txt_attrs
            )
            dur_t.drawAtPoint_((240.0 - pad_r - dur_t.size().width, lbl_baseline))


# ── 菜单栏视图 & 委托 ─────────────────────────────────────────────────────────

# 目标硬编码（后续可持久化）
_GOALS = [
    ("自主学习", 3 * 3600, "目标"),
    ("学校学习", 2 * 3600, "目标"),
    ("娱乐",     2 * 3600, "上限"),
]


class BriefRowView(NSView):
    """
    简报分类行（260 × 46pt 含二级分类 / 32pt 无二级分类）。
    使用前设置 _cat / _total / _subs，再调用 setNeedsDisplay_(True)。
    """

    def initWithFrame_(self, frame):
        self = objc.super(BriefRowView, self).initWithFrame_(frame)
        if self is not None:
            self._cat = ""
            self._total = 0.0
            self._subs = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        cat   = getattr(self, "_cat",   "")
        total = getattr(self, "_total", 0.0)
        subs  = getattr(self, "_subs",  {})

        H        = self.frame().size.height   # 46 或 32
        has_subs = bool(subs)
        cat_y    = H - 20.0                   # 分类名 baseline（非翻转，从底部算）
        dot_cy   = cat_y + 4.0               # 圆点中心 y（与分类名视觉对齐）

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        # ── 彩色圆点（8pt 直径） ─────────────────────────────────────────────
        _nscolor(cat).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            ((14.0, dot_cy - 4.0), (8.0, 8.0))
        ).fill()

        # ── 分类名 ───────────────────────────────────────────────────────────
        name_t = NSAttributedString.alloc().initWithString_attributes_(
            cat,
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: gray if cat == "其他" else dark},
        )
        name_t.drawAtPoint_((34.0, cat_y))

        # ── 时长（右对齐） ───────────────────────────────────────────────────
        dur_t = NSAttributedString.alloc().initWithString_attributes_(
            _fmt_dur(total),
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: gray},
        )
        dur_t.drawAtPoint_((260.0 - 14.0 - dur_t.size().width, cat_y))

        # ── 二级分类小字（11pt 灰色） ─────────────────────────────────────────
        if has_subs:
            sub_str = " · ".join(
                f"{k} {_fmt_dur(v)}"
                for k, v in sorted(subs.items(), key=lambda x: -x[1])
            )
            sub_t = NSAttributedString.alloc().initWithString_attributes_(
                sub_str,
                {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                 NSForegroundColorAttributeName: gray},
            )
            sub_t.drawAtPoint_((34.0, 9.0))


class GoalRowView(NSView):
    """
    目标进度行（260 × 50pt）。
    使用前设置 _cat / _actual / _goal_secs / _goal_label / _pct。
    """

    def initWithFrame_(self, frame):
        self = objc.super(GoalRowView, self).initWithFrame_(frame)
        if self is not None:
            self._cat       = ""
            self._actual    = 0.0
            self._goal_secs = 3600.0
            self._goal_label = "目标"
            self._pct       = 0.0
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        cat        = getattr(self, "_cat",        "")
        actual     = getattr(self, "_actual",     0.0)
        goal_secs  = getattr(self, "_goal_secs",  3600.0)
        goal_label = getattr(self, "_goal_label", "目标")
        pct        = getattr(self, "_pct",        0.0)

        gray  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)
        color = _nscolor(cat)
        pad   = 14.0

        # ── 分类名（baseline ≈ 34pt from bottom） ────────────────────────────
        name_t = NSAttributedString.alloc().initWithString_attributes_(
            cat,
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: dark},
        )
        name_t.drawAtPoint_((pad, 34.0))

        # ── 百分比（右对齐，同行，彩色） ─────────────────────────────────────
        pct_t = NSAttributedString.alloc().initWithString_attributes_(
            f"{int(round(pct * 100))}%",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(11.0),
             NSForegroundColorAttributeName: color},
        )
        pct_t.drawAtPoint_((260.0 - pad - pct_t.size().width, 34.0))

        # ── 进度说明（baseline ≈ 20pt from bottom） ──────────────────────────
        prog_t = NSAttributedString.alloc().initWithString_attributes_(
            f"今日 {_fmt_dur(actual)} / {goal_label} {_fmt_dur(goal_secs)}",
            {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
             NSForegroundColorAttributeName: gray},
        )
        prog_t.drawAtPoint_((pad, 20.0))

        # ── 进度条（bottom=8pt，height=3pt） ─────────────────────────────────
        bar_w = 260.0 - pad * 2

        NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((pad, 8.0), (bar_w, 3.0)), 1.5, 1.5
        ).fill()

        fill_w = max(0.0, min(bar_w, pct * bar_w))
        if fill_w >= 1.0:
            color.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((pad, 8.0), (fill_w, 3.0)), 1.5, 1.5
            ).fill()


class DateNavView(NSView):
    """横向日期导航条：‹  日期  ›，嵌入单个 NSMenuItem。"""

    def initWithFrame_(self, frame):
        self = objc.super(DateNavView, self).initWithFrame_(frame)
        if self is not None:
            self._delegate   = None
            self._date_label = ""
            self._is_today   = True
        return self

    @objc.python_method
    def setup(self, delegate, date_label, is_today):
        self._delegate   = delegate
        self._date_label = date_label
        self._is_today   = is_today
        return self

    def drawRect_(self, dirty_rect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(self.bounds())

        dark  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)
        gray  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        faded = NSColor.colorWithSRGBRed_green_blue_alpha_(0.75,  0.75,  0.77,  1.0)

        arrow_font = NSFont.systemFontOfSize_(15.0)
        label_font = NSFont.systemFontOfSize_(11.0)

        def _draw_centered(text, color, font, rx, ry, rw, rh):
            ns_str = NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font,
                       NSForegroundColorAttributeName: color})
            sz = ns_str.size()
            ns_str.drawAtPoint_((rx + (rw - sz.width)  / 2.0,
                                 ry + (rh - sz.height) / 2.0))

        _draw_centered("‹", dark, arrow_font, 0, 0, 36, 36)
        _draw_centered(self._date_label, gray, label_font, 36, 0, 188, 36)
        _draw_centered("›",
                       faded if self._is_today else dark,
                       arrow_font, 224, 0, 36, 36)

    def mouseUp_(self, event):
        if self._delegate is None:
            return
        x = self.convertPoint_fromView_(event.locationInWindow(), None).x
        if x < 36:
            self._delegate.prevDay_(None)
        elif x > 224 and not self._is_today:
            self._delegate.nextDay_(None)

    def acceptsFirstMouse_(self, event):
        return True

    def isOpaque(self):
        return False


class XiaoDanDelegate(NSObject):
    """
    NSApplicationDelegate — 管理状态栏图标、菜单构建与页面切换。
    追踪逻辑在独立后台线程运行，本类只负责 UI。
    """

    # ── 初始化 ───────────────────────────────────────────────────────────────
    def init(self):
        self = objc.super(XiaoDanDelegate, self).init()
        if self is not None:
            self._conn        = None    # sqlite3.Connection，由 main() 注入
            self._status_item = None
            self._chart_mode  = "donut"
            self._view_date   = date.today()
        return self

    # ── NSApplicationDelegate ────────────────────────────────────────────────
    def applicationDidFinishLaunching_(self, notification):
        bar = NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        self._status_item.button().setTitle_("…")
        self._show_home()
        self.refreshTitle_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            float(POLL_INTERVAL), self, "refreshTitle:", None, True
        )

    # ── ObjC 可见方法（NSTimer 回调 & NSMenuItem actions） ──────────────────
    def refreshTitle_(self, timer):
        try:
            if not self._conn:
                return
            today_str = date.today().strftime("%Y-%m-%d")
            stats = get_category_stats(self._conn, today_str)
            total = sum(v["total"] for v in stats.values())
            self._status_item.button().setTitle_(_fmt_dur(total) if total > 0 else "小蛋")
        except Exception:
            self._status_item.button().setTitle_("🥚")

    def showHome_(self, sender):
        self._show_home()

    def showBrief_(self, sender):
        self._show_brief()

    def showGoals_(self, sender):
        self._show_goals()

    def toggleChart_(self, sender):
        self._chart_mode = "bar" if self._chart_mode == "donut" else "donut"
        self._show_home()

    def prevDay_(self, sender):
        self._view_date -= timedelta(days=1)
        self._show_home()

    def nextDay_(self, sender):
        if self._view_date < date.today():
            self._view_date += timedelta(days=1)
            self._show_home()

    def openReport_(self, sender):
        reports = os.path.join(APP_SUPPORT_DIR, "reports")
        os.makedirs(reports, exist_ok=True)
        webbrowser.open(f"file://{reports}/")

    # ── 页面切换（Python only） ──────────────────────────────────────────────
    @objc.python_method
    def _show_home(self):
        menu = self._build_home_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    @objc.python_method
    def _show_brief(self):
        menu = self._build_brief_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    @objc.python_method
    def _show_goals(self):
        menu = self._build_goals_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    def reopenMenu_(self, _timer):
        self._status_item.button().performClick_(None)

    @objc.python_method
    def _get_stats(self):
        return get_category_stats(self._conn, str(self._view_date)) if self._conn else {}

    # ── 菜单构建 ─────────────────────────────────────────────────────────────
    @objc.python_method
    def _build_home_menu(self):
        stats = self._get_stats()
        total = sum(v["total"] for v in stats.values())
        menu  = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        # 日期翻页：‹   日期标签   ›（单行自定义 NSView）
        is_today = self._view_date == date.today()
        vd = self._view_date
        date_label = (
            f"今天 · {vd.year}年{vd.month}月{vd.day}日"
            if is_today else
            f"{vd.year}年{vd.month}月{vd.day}日"
        )

        nav_view = DateNavView.alloc().initWithFrame_(((0, 0), (260, 36)))
        nav_view.setup(self, date_label, is_today)
        self._nav_view = nav_view  # 防 GC

        nav_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        nav_item.setView_(nav_view)
        menu.addItem_(nav_item)

        # 总活跃时长（22pt 粗体）
        total_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        total_item.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(
                f"活跃 {_fmt_dur(total)}",
                {NSFontAttributeName: NSFont.boldSystemFontOfSize_(22.0),
                 NSForegroundColorAttributeName: dark},
            )
        )
        total_item.setEnabled_(False)
        menu.addItem_(total_item)

        # 图表（DonutChartView / BarChartView，放入 260pt 容器保持对齐）
        chart_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        chart_item.setEnabled_(False)
        if self._chart_mode == "donut":
            cv = DonutChartView.alloc().initWithFrame_(((10, 0), (240, 120)))
        else:
            cv = BarChartView.alloc().initWithFrame_(((10, 0), (240, 120)))
        cv._stats        = stats
        self._chart_view = cv
        container        = NSView.alloc().initWithFrame_(((0, 0), (260, 120)))
        container.addSubview_(cv)
        chart_item.setView_(container)
        menu.addItem_(chart_item)

        # 切换图表
        tog = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "切换图表", "toggleChart:", ""
        )
        tog.setTarget_(self)
        menu.addItem_(tog)

        menu.addItem_(NSMenuItem.separatorItem())

        # 三个导航入口
        for title, action in [
            ("今日简报 ›", "showBrief:"),
            ("目标设定 ›", "showGoals:"),
            ("查看周报 ›", "openReport:"),
        ]:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self)
            menu.addItem_(item)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出小蛋", "terminate:", "q"
        )
        quit_item.setTarget_(NSApplication.sharedApplication())
        menu.addItem_(quit_item)

        return menu

    @objc.python_method
    def _build_brief_menu(self):
        stats = self._get_stats()
        menu  = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        back = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "‹ 返回", "showHome:", ""
        )
        back.setTarget_(self)
        menu.addItem_(back)
        menu.addItem_(NSMenuItem.separatorItem())

        for cat in CATEGORY_ORDER:
            cat_stats = stats.get(cat, {})
            secs = cat_stats.get("total", 0.0)
            subs = cat_stats.get("subs", {})
            h    = 46.0 if subs else 32.0
            row  = BriefRowView.alloc().initWithFrame_(((0, 0), (260, h)))
            row._cat   = cat
            row._total = secs
            row._subs  = subs
            row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            row_item.setEnabled_(False)
            row_item.setView_(row)
            menu.addItem_(row_item)

        menu.addItem_(NSMenuItem.separatorItem())

        report = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "查看完整日报 ›", "openReport:", ""
        )
        report.setTarget_(self)
        menu.addItem_(report)

        return menu

    @objc.python_method
    def _build_goals_menu(self):
        stats = self._get_stats()
        menu  = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        back = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "‹ 返回", "showHome:", ""
        )
        back.setTarget_(self)
        menu.addItem_(back)
        menu.addItem_(NSMenuItem.separatorItem())

        for cat, goal_secs, label in _GOALS:
            actual = stats.get(cat, {}).get("total", 0.0)
            pct    = min(1.0, actual / goal_secs) if goal_secs > 0 else 0.0
            row    = GoalRowView.alloc().initWithFrame_(((0, 0), (260, 50)))
            row._cat        = cat
            row._actual     = actual
            row._goal_secs  = goal_secs
            row._goal_label = label
            row._pct        = pct
            row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            row_item.setEnabled_(False)
            row_item.setView_(row)
            menu.addItem_(row_item)

        menu.addItem_(NSMenuItem.separatorItem())

        add_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "＋ 添加目标", None, ""
        )
        add_item.setEnabled_(False)
        menu.addItem_(add_item)

        return menu


# ── 主循环 ────────────────────────────────────────────────────────────────────
def tracking_loop(conn: sqlite3.Connection) -> None:
    """主追踪循环：含休眠检测和共享状态更新。"""
    global _classifier_running
    last_loop_time = time.time()
    record_counter = 0

    while True:
        try:
            now = datetime.now()
            now_ts = time.time()
            timestamp_db = now.strftime("%Y-%m-%d %H:%M:%S")
            date_db = now.strftime("%Y-%m-%d")
            ts = now.strftime("%H:%M:%S")

            # 休眠检测：距上次循环超过阈值，跳过本帧
            gap = now_ts - last_loop_time
            if gap > SLEEP_DETECT_THRESHOLD:
                last_loop_time = now_ts
                time.sleep(POLL_INTERVAL)
                continue

            # 主活动：优先用鼠标所在窗口（与 poll() 逻辑一致）
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

            # 更新共享状态
            state.update(app_name or "空闲", window_title or "", main["type"])

            db_title = main.get("page_title") or window_title
            save_to_db(conn, timestamp_db, date_db, app_name, db_title, main, bg)
            record_counter += 1
            if record_counter >= 60:
                record_counter = 0
                if not _classifier_running:
                    _classifier_running = True
                    _today = date.today()
                    _dates = [
                        (_today - timedelta(days=1)).strftime("%Y-%m-%d"),
                        _today.strftime("%Y-%m-%d"),
                    ]

                    def _run_classifier(p=CLASSIFIER_PATH, dates=_dates):
                        global _classifier_running
                        _log_path = os.path.join(APP_SUPPORT_DIR, "classifier_error.log")
                        try:
                            for d in dates:
                                try:
                                    result = subprocess.run(
                                        ["python3", p, "--date", d, "--no-api"],
                                        capture_output=True,
                                        timeout=120,
                                    )
                                    if result.returncode != 0:
                                        with open(_log_path, "a") as f:
                                            f.write(f"{datetime.now()} ERROR --date {d}\n")
                                            f.write(result.stderr.decode(errors="replace") + "\n")
                                except subprocess.TimeoutExpired:
                                    with open(_log_path, "a") as f:
                                        f.write(f"{datetime.now()} TIMEOUT --date {d}\n")
                        finally:
                            _classifier_running = False

                    threading.Thread(target=_run_classifier, daemon=True).start()

            print(f"\n[{ts}]")
            print(f"  主活动：{main['display']}")
            if DEBUG and (main["type"] == "dock" or "Dock" in (app_name or "")):
                mx, my = _get_mouse_pos()
                screen_h = NSScreen.mainScreen().frame().size.height
                print(f"  [DEBUG] 鼠标坐标 (Quartz): ({mx:.0f}, {my:.0f})")
                print(f"  [DEBUG] 检测到: 应用 = {app_name!r}  窗口 = {window_title!r}")
                print(f"  [DEBUG] 主屏幕高度: {screen_h:.0f}pt（Dock 阈值约 Y > {screen_h - 80:.0f}）")
            print(f"  背景音乐：{bg}")

        except Exception as e:
            print(f"  [警告] tracking_loop 异常: {e}")

        last_loop_time = time.time()
        time.sleep(POLL_INTERVAL)


def main():
    # 确保表结构存在，然后关闭这个临时连接
    init_db().close()
    print(f"=== 小蛋启动于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📦 数据库就绪：{DB_PATH}")

    # 追踪线程持有自己的连接（SQLite 连接不跨线程共享）
    def _tracking_thread():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        tracking_loop(conn)

    t = threading.Thread(target=_tracking_thread, daemon=True)
    t.start()

    # 菜单栏 UI（主线程）— 独立连接，只用于读取统计
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)   # NSApplicationActivationPolicyAccessory，无 Dock 图标

    delegate = XiaoDanDelegate.alloc().init()
    delegate._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    delegate._conn.execute("PRAGMA journal_mode=WAL")
    app.setDelegate_(delegate)
    app.run()   # 阻塞在此直到 terminate: 被调用


if __name__ == "__main__":
    main()
