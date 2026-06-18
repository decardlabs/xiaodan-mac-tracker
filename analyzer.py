"""
小蛋 — 日报生成器
从 activity_log 聚合当日数据，调用 LLM 生成自然语言简报，存入 daily_reports 表。
"""

import os
import sqlite3
from collections import Counter, defaultdict
import calendar
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.path.expanduser("~/Library/Application Support/XiaoDan/activity.db")
MODEL = "deepseek-v4-flash"

# 与 tracker.py 保持一致：间隔超过此值视为休眠，不计入时长
_SLEEP_DETECT_THRESHOLD = 600  # 5s * 120 = 10分钟

_FILTER_APPS = {
    "通知中心", "系统设置", "程序坞", "控制中心",
    "universalAccessAuthWarn", "Window Server", "Spotlight",
    "查找", "启动台", "", "空闲",
}

SYSTEM_PROMPT = """你是用户的个人时间助手。
根据用户今天的电脑使用记录，用简短自然的中文写一段今日总结。
要求：
- 不提具体时长和数字（图表已经显示了）
- 重点说在做什么，比如学了什么、看了什么、主要在忙什么
- 如果能看出规律或有趣的点可以提一句
- 语气自然，像朋友帮你回顾今天，不要像报告
- 控制在80字以内
- 不要用标题、分点、加粗等格式，就是一段话
- 不要使用波折号（～）、破折号（——）或任何横线符号
- 说完做了什么就直接结束，不要在结尾加任何总结句、评价句或感想"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            date         TEXT PRIMARY KEY,
            content      TEXT,
            generated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reflections (
            year       INTEGER,
            week       INTEGER,
            content    TEXT,
            updated_at TEXT,
            PRIMARY KEY (year, week)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            author     TEXT,
            date_read  TEXT,
            tags       TEXT,
            content    TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_summaries (
            year         INTEGER,
            month        INTEGER,
            content      TEXT,
            generated_at TEXT,
            PRIMARY KEY (year, month)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_reflections (
            year       INTEGER,
            month      INTEGER,
            content    TEXT,
            updated_at TEXT,
            PRIMARY KEY (year, month)
        )
    """)
    conn.commit()
    return conn


def get_daily_activities(date_str: str) -> str | None:
    conn = _get_conn()
    try:
        # LEAD() 算法：与 tracker.py 的 calc_duration_seconds / get_category_stats 完全一致
        rows = conn.execute("""
            WITH with_next AS (
                SELECT
                    app_name,
                    window_title,
                    url,
                    timestamp AS start_ts,
                    LEAD(timestamp) OVER (PARTITION BY date ORDER BY timestamp) AS next_ts
                FROM activity_log
                WHERE date = ?
                  AND activity_type NOT IN ('idle', 'dock')
            )
            SELECT
                app_name,
                window_title,
                url,
                (julianday(next_ts) - julianday(start_ts)) * 86400.0 AS secs
            FROM with_next
            WHERE next_ts IS NOT NULL
        """, (date_str,)).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    # Python 侧聚合：按 (app_name, url) 分组，收集 window_title 候选
    groups: dict = defaultdict(lambda: {"total": 0.0, "titles": []})
    for app_name, window_title, url, secs in rows:
        if secs is None or secs <= 0 or secs > _SLEEP_DETECT_THRESHOLD:
            continue
        if (app_name or "") in _FILTER_APPS:
            continue
        key = (app_name or "", url or "")
        groups[key]["total"] += secs
        if window_title and window_title.strip():
            groups[key]["titles"].append(window_title.strip())

    entries = [
        (key, data)
        for key, data in groups.items()
        if data["total"] > 60
    ]
    if not entries:
        return None

    entries.sort(key=lambda x: -x[1]["total"])

    lines = []
    for (app_name, url), data in entries:
        mins = round(data["total"] / 60)
        if url and data["titles"]:
            title = Counter(data["titles"]).most_common(1)[0][0]
            lines.append(f"应用：{app_name}，页面：{title}，时长：{mins}分钟")
        else:
            lines.append(f"应用：{app_name}，时长：{mins}分钟")

    return "\n".join(lines)


def get_report(date_str: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT content FROM daily_reports WHERE date = ?", (date_str,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def save_report(date_str: str, content: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports (date, content, generated_at) VALUES (?, ?, ?)",
            (date_str, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def generate_report(date_str: str) -> str | None:
    activities_text = get_daily_activities(date_str)
    if activities_text is None:
        return None

    import anthropic  # 懒加载：仅在实际需要 API 时导入
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"这是我今天（{date_str}）的电脑使用记录：\n\n{activities_text}",
        }],
    )
    content = next(
        (blk.text for blk in msg.content if hasattr(blk, "text")), ""
    ).strip()
    content = content.replace("——", "").replace("～", "").replace("~", "").strip()

    save_report(date_str, content)
    return content


# ── 分类辅助 ──────────────────────────────────────────────────────────────────

def parse_category(category_str):
    """把 "自主学习/编程学习" 拆成 ("自主学习", "编程学习")。"""
    if category_str is None:
        return ("其他", "待分类")
    parts = category_str.split("/", 1)
    if len(parts) < 2:
        return (category_str, "其他")
    return (parts[0], parts[1])


def _week_date_range(year: int, week: int):
    d_start = date.fromisocalendar(year, week, 1)   # 周一
    d_end   = d_start + timedelta(days=6)            # 周日
    return d_start, d_end


def _lead_query(conn, date_list: list[str]) -> list:
    """对给定日期列表执行 LEAD() 时长查询，返回原始行。"""
    placeholders = ",".join(["?"] * len(date_list))
    return conn.execute(f"""
        WITH with_next AS (
            SELECT
                date,
                app_name,
                window_title,
                url,
                category,
                timestamp AS start_ts,
                LEAD(timestamp) OVER (PARTITION BY date ORDER BY timestamp) AS next_ts
            FROM activity_log
            WHERE date IN ({placeholders})
              AND activity_type NOT IN ('idle', 'dock')
        )
        SELECT
            date,
            app_name,
            window_title,
            url,
            category,
            (julianday(next_ts) - julianday(start_ts)) * 86400.0 AS secs
        FROM with_next
        WHERE next_ts IS NOT NULL
    """, date_list).fetchall()


# ── 周报数据 ───────────────────────────────────────────────────────────────────

def get_week_stats(year: int, week: int) -> dict:
    d_start, d_end = _week_date_range(year, week)
    date_list = [str(d_start + timedelta(days=i)) for i in range(7)]

    conn = _get_conn()
    try:
        rows = _lead_query(conn, date_list)
    finally:
        conn.close()

    by_category: dict = {}
    by_day: dict = {d: {"total": 0} for d in date_list}
    total_seconds = 0.0

    for row_date, app_name, _title, _url, category, secs in rows:
        if secs is None or secs <= 0 or secs > _SLEEP_DETECT_THRESHOLD:
            continue
        if (app_name or "") in _FILTER_APPS:
            continue
        l1, l2 = parse_category(category)
        if l1 not in by_category:
            by_category[l1] = {"seconds": 0.0, "sub": {}}
        by_category[l1]["seconds"] += secs
        by_category[l1]["sub"][l2] = by_category[l1]["sub"].get(l2, 0.0) + secs
        if row_date in by_day:
            by_day[row_date]["total"] = by_day[row_date].get("total", 0.0) + secs
            by_day[row_date][l1]      = by_day[row_date].get(l1,    0.0) + secs
        total_seconds += secs

    for cat_data in by_category.values():
        cat_data["seconds"] = int(cat_data["seconds"])
        cat_data["sub"] = {k: int(v) for k, v in cat_data["sub"].items()}
    for day_data in by_day.values():
        for k in list(day_data):
            day_data[k] = int(day_data[k])

    return {
        "year":         year,
        "week":         week,
        "date_start":   str(d_start),
        "date_end":     str(d_end),
        "total_seconds": int(total_seconds),
        "by_category":  by_category,
        "by_day":       by_day,
    }


def get_all_weeks() -> list:
    """返回有数据的所有 ISO 周，倒序，格式 (year, week, date_start, date_end)。"""
    conn = _get_conn()
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM activity_log WHERE date IS NOT NULL ORDER BY date DESC"
        ).fetchall()]
    finally:
        conn.close()

    seen: set = set()
    result = []
    for d_str in dates:
        try:
            d = date.fromisoformat(d_str)
        except Exception:
            continue
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        if key in seen:
            continue
        seen.add(key)
        ds, de = _week_date_range(iso[0], iso[1])
        result.append((iso[0], iso[1], str(ds), str(de)))
    return result


# ── 月报数据 ───────────────────────────────────────────────────────────────────

def get_month_stats(year: int, month: int) -> dict:
    _, last_day = calendar.monthrange(year, month)
    d_start = date(year, month, 1)
    d_end   = date(year, month, last_day)
    date_list = [str(d_start + timedelta(days=i)) for i in range(last_day)]

    conn = _get_conn()
    try:
        rows = _lead_query(conn, date_list)
    finally:
        conn.close()

    by_category: dict = {}
    by_week_map: dict = {}
    page_times:  dict = {}
    total_seconds = 0.0

    for row_date, app_name, window_title, url, category, secs in rows:
        if secs is None or secs <= 0 or secs > _SLEEP_DETECT_THRESHOLD:
            continue
        if (app_name or "") in _FILTER_APPS:
            continue
        l1, l2 = parse_category(category)

        if l1 not in by_category:
            by_category[l1] = {"seconds": 0.0, "sub": {}}
        by_category[l1]["seconds"] += secs
        by_category[l1]["sub"][l2] = by_category[l1]["sub"].get(l2, 0.0) + secs

        try:
            iso = date.fromisoformat(row_date).isocalendar()
            wkey = (iso[0], iso[1])
        except Exception:
            wkey = (year, 0)
        if wkey not in by_week_map:
            ds, _ = _week_date_range(wkey[0], wkey[1])
            by_week_map[wkey] = {"week": wkey[1], "date_start": str(ds), "total": 0.0}
        by_week_map[wkey]["total"] = by_week_map[wkey].get("total", 0.0) + secs
        by_week_map[wkey][l1]      = by_week_map[wkey].get(l1,    0.0) + secs

        if url and url.strip():
            pkey = (app_name or "", url.strip(), (window_title or "").strip())
            page_times[pkey] = page_times.get(pkey, 0.0) + secs

        total_seconds += secs

    for cat_data in by_category.values():
        cat_data["seconds"] = int(cat_data["seconds"])
        cat_data["sub"] = {k: int(v) for k, v in cat_data["sub"].items()}

    by_week = []
    for wkey in sorted(by_week_map.keys()):
        wd = by_week_map[wkey]
        by_week.append({k: int(v) if isinstance(v, float) else v for k, v in wd.items()})

    top_pages = [
        {"app": app, "url": url, "title": title, "seconds": int(s)}
        for (app, url, title), s in sorted(page_times.items(), key=lambda x: -x[1])[:10]
    ]

    return {
        "year":          year,
        "month":         month,
        "total_seconds": int(total_seconds),
        "by_category":   by_category,
        "by_week":       by_week,
        "top_pages":     top_pages,
    }


def get_all_months() -> list:
    """返回有数据的所有月份，倒序，格式 (year, month)。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT substr(date,1,7) FROM activity_log WHERE date IS NOT NULL ORDER BY date DESC"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for (ym,) in rows:
        try:
            result.append((int(ym[:4]), int(ym[5:7])))
        except Exception:
            continue
    return result


# ── 周记 CRUD ──────────────────────────────────────────────────────────────────

def get_reflection(year: int, week: int) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT content FROM weekly_reflections WHERE year = ? AND week = ?", (year, week)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def save_reflection(year: int, week: int, content: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO weekly_reflections (year, week, content, updated_at) VALUES (?, ?, ?, ?)",
            (year, week, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


# ── 书单 CRUD ──────────────────────────────────────────────────────────────────

def get_book_notes() -> list:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, author, date_read, tags, content, created_at, updated_at "
            "FROM book_notes ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    keys = ("id", "title", "author", "date_read", "tags", "content", "created_at", "updated_at")
    return [dict(zip(keys, r)) for r in rows]


def save_book_note(title: str, author: str, date_read: str, tags: str, content: str) -> int:
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur = conn.execute(
            "INSERT INTO book_notes (title, author, date_read, tags, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, author, date_read, tags, content, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_book_note(note_id: int, title: str, author: str, date_read: str, tags: str, content: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE book_notes SET title=?, author=?, date_read=?, tags=?, content=?, updated_at=? WHERE id=?",
            (title, author, date_read, tags, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_book_note(note_id: int) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM book_notes WHERE id = ?", (note_id,))
        conn.commit()
    finally:
        conn.close()


# ── 月报 AI 总结 ──────────────────────────────────────────────────────────────

_MONTHLY_SYSTEM_PROMPT = """你是用户的个人月度回顾助手。
根据用户这个月的电脑使用记录和读书笔记，用中文写一段月度总结。
要求：
- 不提具体时长和数字
- 说清楚这个月的重心在哪里，主要在做什么
- 如果有读书笔记，自然地提到读了什么书、大致方向
- 只描述事实，不做评价、判断或情绪表达
- 不使用带倾向性的形容词
- 说完就结束，不加总结句或评价句
- 控制在150字以内
- 不要用标题、分点、加粗等格式，就是一段话
- 不要使用波折号、破折号或任何横线符号"""


def get_monthly_summary(year: int, month: int) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT content FROM monthly_summaries WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def save_monthly_summary(year: int, month: int, content: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO monthly_summaries (year, month, content, generated_at) "
            "VALUES (?, ?, ?, ?)",
            (year, month, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def get_monthly_reflection(year: int, month: int) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT content FROM monthly_reflections WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def save_monthly_reflection(year: int, month: int, content: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO monthly_reflections (year, month, content, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (year, month, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def get_month_activities_text(year: int, month: int) -> str | None:
    import calendar as _calendar
    last_day = _calendar.monthrange(year, month)[1]
    date_start = f"{year}-{month:02d}-01"
    date_end   = f"{year}-{month:02d}-{last_day:02d}"

    conn = _get_conn()
    try:
        rows = conn.execute("""
            WITH with_next AS (
                SELECT
                    app_name,
                    window_title,
                    url,
                    timestamp AS start_ts,
                    LEAD(timestamp) OVER (PARTITION BY date ORDER BY timestamp) AS next_ts
                FROM activity_log
                WHERE date >= ? AND date <= ?
                  AND activity_type NOT IN ('idle', 'dock')
            )
            SELECT
                app_name,
                window_title,
                url,
                (julianday(next_ts) - julianday(start_ts)) * 86400.0 AS secs
            FROM with_next
            WHERE next_ts IS NOT NULL
        """, (date_start, date_end)).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    groups: dict = defaultdict(lambda: {"total": 0.0, "titles": []})
    for app_name, window_title, url, secs in rows:
        if secs is None or secs <= 0 or secs > _SLEEP_DETECT_THRESHOLD:
            continue
        if (app_name or "") in _FILTER_APPS:
            continue
        key = (app_name or "", url or "")
        groups[key]["total"] += secs
        if window_title and window_title.strip():
            groups[key]["titles"].append(window_title.strip())

    entries = [
        (key, data)
        for key, data in groups.items()
        if data["total"] > 600  # 月维度：超过10分钟才保留
    ]
    if not entries:
        return None

    entries.sort(key=lambda x: -x[1]["total"])
    entries = entries[:30]

    lines = []
    for (app_name, url), data in entries:
        hours = round(data["total"] / 3600, 1)
        if url and data["titles"]:
            title = Counter(data["titles"]).most_common(1)[0][0]
            lines.append(f"应用：{app_name}，页面：{title}，累计时长：{hours}小时")
        else:
            lines.append(f"应用：{app_name}，累计时长：{hours}小时")

    return "\n".join(lines)


def get_month_book_notes_text(year: int, month: int) -> str | None:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT title, author, content FROM book_notes "
            "WHERE created_at LIKE ? ORDER BY created_at ASC",
            (f"{year}-{month:02d}%",),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    lines = []
    for title, author, content in rows:
        header = f"《{title}》" + (f"（{author}）" if author else "")
        lines.append(header)
        if content and content.strip():
            lines.append(f"感想：{content.strip()}")
        lines.append("")

    return "\n".join(lines).strip()


def generate_monthly_summary(year: int, month: int) -> str | None:
    activities_text = get_month_activities_text(year, month)
    book_notes_text = get_month_book_notes_text(year, month)

    if activities_text is None and book_notes_text is None:
        return None

    import anthropic  # 懒加载：仅在实际需要 API 时导入
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")

    user_msg = f"{year}年{month}月电脑使用记录：\n\n{activities_text or '（无数据）'}"
    if book_notes_text:
        user_msg += f"\n\n本月读书笔记：\n\n{book_notes_text}"

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=_MONTHLY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    content = next(
        (blk.text for blk in msg.content if hasattr(blk, "text")), ""
    ).strip()
    content = content.replace("——", "").replace("～", "").replace("~", "").strip()

    save_monthly_summary(year, month, content)
    return content


if __name__ == "__main__":
    import sys
    from datetime import date
    date_str = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    print(f"正在生成 {date_str} 的简报...")
    result = generate_report(date_str)
    if result:
        print(result)
    else:
        print("当天没有活动记录")
