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

import fcntl
import os
import shutil
import sys
import time
import threading
import sqlite3
import subprocess
from datetime import datetime, date, timedelta

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
POLL_INTERVAL = 5        # 检测间隔（秒）
SLEEP_DETECT_THRESHOLD = POLL_INTERVAL * 120  # 间隔超过此值视为系统休眠（10分钟）
DEBUG = True             # 开启后，Dock 检测时额外打印鼠标坐标和窗口信息

# 用户数据目录（macOS 标准位置）
APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/XiaoDan")
os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
DB_PATH   = os.path.join(APP_SUPPORT_DIR, "activity.db")
LOCK_FILE = os.path.join(APP_SUPPORT_DIR, "xiaodian.lock")

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


# ── 主循环 ────────────────────────────────────────────────────────────────────
_recheck_running = False
_last_recheck_hour: int | None = None  # 同一小时内只触发一次


def _check_other_category() -> None:
    """检查今天「其他」类别是否超过30分钟，超过则触发 --recheck-other。"""
    global _recheck_running, _last_recheck_hour
    current_hour = datetime.now().hour
    if _last_recheck_hour == current_hour:
        return
    if _recheck_running:
        return
    _recheck_running = True

    def _run():
        global _recheck_running, _last_recheck_hour
        _log_path = os.path.join(APP_SUPPORT_DIR, "classifier_error.log")
        try:
            db_conn = sqlite3.connect(DB_PATH)
            today = date.today().strftime("%Y-%m-%d")
            rows = db_conn.execute(
                """WITH with_next AS (
                       SELECT category,
                              timestamp AS start_ts,
                              LEAD(timestamp) OVER (ORDER BY timestamp) AS next_ts
                       FROM activity_log
                       WHERE date = ?
                         AND category LIKE '其他%'
                         AND category != '其他/系统后台'
                   )
                   SELECT COALESCE(SUM(
                       MIN(
                           (strftime('%s', next_ts) - strftime('%s', start_ts)),
                           600
                       )
                   ), 0)
                   FROM with_next
                   WHERE next_ts IS NOT NULL""",
                (today,),
            ).fetchone()
            db_conn.close()
            other_secs = rows[0] if rows else 0
            if other_secs < 1800:  # 不足30分钟，不触发
                return
            result = subprocess.run(
                [sys.executable, CLASSIFIER_PATH, "--recheck-other", "--date", today],
                capture_output=True,
                timeout=180,
            )
            if result.returncode != 0:
                with open(_log_path, "a") as f:
                    f.write(f"{datetime.now()} ERROR --recheck-other\n")
                    f.write(result.stderr.decode(errors="replace") + "\n")
            else:
                _last_recheck_hour = current_hour
        except Exception as e:
            with open(_log_path, "a") as f:
                f.write(f"{datetime.now()} EXCEPTION _check_other_category: {e}\n")
        finally:
            _recheck_running = False

    threading.Thread(target=_run, daemon=True).start()


def tracking_loop(conn: sqlite3.Connection) -> None:
    """主追踪循环：含休眠检测和共享状态更新。"""
    global _classifier_running
    last_loop_time = time.time()
    record_counter = 0
    recheck_counter = 0  # 用于每小时触发 _check_other_category

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
                                        [sys.executable, p, "--date", d, "--no-api"],
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

            # 每 ~720 条记录（约1小时）检查一次「其他」分类
            recheck_counter += 1
            if recheck_counter >= 720:
                recheck_counter = 0
                _check_other_category()

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


def _acquire_lock():
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        print("小蛋已经在运行中，退出。")
        sys.exit(0)


def main():
    _lock_file = _acquire_lock()  # noqa: F841 — 持有引用防止 GC 关闭文件描述符
    os.makedirs(os.path.expanduser("~/Library/Logs/XiaoDan"), exist_ok=True)
    init_db().close()
    print(f"=== 小蛋启动于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"📦 数据库就绪：{DB_PATH}")

    def _tracking_thread():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        tracking_loop(conn)

    t = threading.Thread(target=_tracking_thread, daemon=True)
    t.start()

    from ui import start_ui
    start_ui(DB_PATH)


if __name__ == "__main__":
    main()
