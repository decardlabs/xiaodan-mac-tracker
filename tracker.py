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

import time
import sqlite3
import subprocess
from datetime import datetime

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
DEBUG = True             # 开启后，Dock 检测时额外打印鼠标坐标和窗口信息
DB_PATH = "activity.db"  # SQLite 数据库文件路径

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
                }
            display_url = (url[:70] + "…") if len(url) > 70 else url
            return {
                "display": f"{app_name} — {label}（{display_url}）",
                "type": "browser",
                "url": url,
            }
        # 拿不到 URL 时退化为普通应用显示
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
    conn.commit()
    return conn


def save_to_db(
    conn: sqlite3.Connection,
    timestamp: str,
    date: str,
    app_name: str | None,
    window_title: str | None,
    main: dict,
    bg: str,
) -> None:
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


# ── 主循环 ────────────────────────────────────────────────────────────────────
def poll(conn: sqlite3.Connection):
    now = datetime.now()
    ts = now.strftime("%H:%M:%S")
    timestamp_db = now.strftime("%Y-%m-%d %H:%M:%S")
    date_db = now.strftime("%Y-%m-%d")

    # 主活动：优先用鼠标所在窗口
    app_name, window_title, pid = get_window_under_mouse()
    bundle_id: str | None = None

    if app_name is not None:
        bundle_id = get_bundle_id(pid) if pid else None

        # Dock / Window Server 都是系统覆盖层，以 frontmost app 为准
        is_system_overlay = bundle_id == DOCK_BUNDLE_ID or app_name == "Window Server"
        if is_system_overlay:
            is_from_dock = bundle_id == DOCK_BUNDLE_ID
            front_name, front_pid, front_bundle = get_frontmost_app()
            _not_real = {DOCK_BUNDLE_ID, "com.apple.finder", None}
            if front_bundle not in _not_real and front_name != "Window Server":
                # 真实 app 在前台，用它
                app_name, pid, bundle_id = front_name, front_pid, front_bundle
                window_title = get_focused_window_title(pid) if pid else None
                if bundle_id == "com.tencent.xinWeChat":
                    window_title = "微信"
                main = detect_main_activity(app_name, window_title, bundle_id)
            elif is_from_dock:
                main = {"display": "Dock（未计入活跃时间）", "type": "dock", "url": None}
            else:
                # Window Server，无真实前台应用 → idle
                main = {"display": "idle", "type": "idle", "url": None}
        else:
            if window_title is None and pid is not None:
                window_title = get_focused_window_title(pid)
            if bundle_id == "com.tencent.xinWeChat":
                window_title = "微信"
            main = detect_main_activity(app_name, window_title, bundle_id)
    else:
        # 鼠标在桌面，退化为键盘焦点兜底
        app_name, pid, bundle_id = get_frontmost_app()
        window_title = get_focused_window_title(pid) if pid else None
        if bundle_id == "com.tencent.xinWeChat":
            window_title = "微信"
        main = detect_main_activity(app_name, window_title, bundle_id)

    bg = detect_background_music(bundle_id, main["type"])

    # 写入数据库
    save_to_db(conn, timestamp_db, date_db, app_name, window_title, main, bg)

    print(f"\n[{ts}]")
    print(f"  主活动：{main['display']}")
    if DEBUG and (main["type"] == "dock" or "Dock" in (app_name or "")):
        mx, my = _get_mouse_pos()
        screen_h = NSScreen.mainScreen().frame().size.height
        print(f"  [DEBUG] 鼠标坐标 (Quartz): ({mx:.0f}, {my:.0f})")
        print(f"  [DEBUG] 检测到: 应用 = {app_name!r}  窗口 = {window_title!r}")
        print(f"  [DEBUG] 主屏幕高度: {screen_h:.0f}pt（Dock 阈值约 Y > {screen_h - 80:.0f}）")
    print(f"  背景音乐：{bg}")


def main():
    conn = init_db()
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== 小蛋启动于 {start_time} ===")
    print(f"📦 小蛋的数据库已就绪：{DB_PATH}")
    print("🥚 小蛋已启动，开始记录你的一天（每5秒检测一次，Ctrl+C退出）")
    try:
        while True:
            poll(conn)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n\n监控已停止。")
        conn.close()


if __name__ == "__main__":
    main()
