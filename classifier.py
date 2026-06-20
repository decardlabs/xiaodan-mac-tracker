#!/usr/bin/env python3
from __future__ import annotations
"""
小蛋 — 活动分类器
================================================
分类逻辑：硬编码规则 → domain_categories 缓存 → Claude API
API 只在遇到全新域名/应用时调用一次，结果写入缓存。

依赖安装：
    pip3 install anthropic

用法：
    python3 classifier.py                        # 分类所有未分类的 activity_log 记录
    python3 classifier.py --date 2026-06-14      # 只处理某天
    python3 classifier.py --since "2026-06-14 09:00:00"
    python3 classifier.py --limit 500
    python3 classifier.py --import-safari        # 导入 Safari 历史并分类
    python3 classifier.py --no-api               # 只用硬编码规则和缓存，不调 API
"""

import argparse
import json
import os
import shutil
import sqlite3
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv 未安装时忽略，env var 仍可手动设置


# ── 配置 ──────────────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/Library/Application Support/XiaoDan/activity.db")
MODEL = "deepseek-v4-flash"  # 代理可用模型（proxy: api.decard.cc）
DOMAIN_BATCH = 30            # 每次 API 调用最多分类多少个域名/应用
COCOA_EPOCH = 978307200      # Cocoa 纪元偏移量（秒），visit_time + 978307200 = Unix 时间戳

# ── 分类体系 ──────────────────────────────────────────────────────────────────
# 一级：学校学习 / 自主学习 / 娱乐 / 其他
# 格式：一级/二级
CATEGORIES_GUIDE = """\
四大类别（格式：一级/二级）：

  学校学习：文献管理 | Notion笔记 | 课程/作业
  自主学习：编程学习 | AI工具 | 读书
  娱乐：视频 | 游戏 | 社交闲逛 | 音乐
  其他：系统后台 | 工具/搜索 | 待分类"""

# ── 硬编码规则（不消耗 API）────────────────────────────────────────────────────
HARDCODED_DOMAINS: dict[str, str] = {
    # 娱乐
    "bilibili.com":            "娱乐/视频",
    "youtube.com":             "娱乐/视频",
    "m.youtube.com":           "娱乐/视频",
    "weibo.com":               "娱乐/社交闲逛",
    "xiaohongshu.com":         "娱乐/社交闲逛",
    "rednote.com":             "娱乐/社交闲逛",
    "store.steampowered.com":  "娱乐/游戏",
    # 自主学习
    "codefirstgirls.com":      "自主学习/编程学习",
    "github.com":              "自主学习/编程学习",
    "gist.github.com":         "自主学习/编程学习",
    "claude.ai":               "自主学习/AI工具",
    "chatgpt.com":             "自主学习/AI工具",
    "gemini.google.com":       "自主学习/AI工具",
    "app.speechify.com":       "自主学习/读书",
    # 学校学习
    "notion.so":               "学校学习/Notion笔记",
    "outlook.office365.com":   "学校学习/课程/作业",
    "mail.google.com":         "学校学习/课程/作业",
    "moodle.ucl.ac.uk":        "学校学习/课程/作业",
    # 其他
    "translate.google.com":    "学校学习/工具",
    "google.com":              "其他/工具/搜索",
    "linkedin.com":            "其他/工具/搜索",
    "app.trading212.com":      "其他/工具",
    "trading212.com":          "其他/工具",
    # Outlook（多个子域）
    "outlook.cloud.microsoft": "学校学习/工具",
    "outlook.live.com":        "学校学习/工具",
    "outlook.office.com":      "学校学习/工具",
}

SYSTEM_PROMPT = f"""你是一个个人时间管理分析助手，对用户的 Mac 上网和应用使用记录分类。

{CATEGORIES_GUIDE}

选择最贴切的二级类别，回复格式：JSON 数组，每项对应输入列表中的一条，\
值为"一级/二级"字符串（如"自主学习/编程学习"）。只输出 JSON，不要其他文字。"""


# ── 工具函数 ──────────────────────────────────────────────────────────────────
# 一级分类白名单
_VALID_L1 = {"学校学习", "自主学习", "娱乐", "其他"}

# 异形 → 标准映射（在白名单校验之前应用）
_CATEGORY_ALIASES: dict[str, str] = {
    "其他/工具搜索":    "其他/工具/搜索",
    "学校学习/课程作业": "学校学习/课程/作业",
    "娱乐/读书":       "自主学习/读书",
}


def normalize_category(cat: str) -> str:
    """标准化 API 返回的类别字符串。"""
    cat = (cat or "").strip()
    # 修正已知异形
    cat = _CATEGORY_ALIASES.get(cat, cat)
    # 必须含至少一个斜杠且一级分类合法
    parts = cat.split("/", 1)
    if len(parts) < 2 or not parts[1] or parts[0] not in _VALID_L1:
        return "其他/待分类"
    return cat


def extract_domain(url: str) -> str:
    """从 URL 提取去除 www. 前缀的域名。"""
    if not url:
        return ""
    try:
        if "://" not in url:
            url = "https://" + url
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


_DISSERTATION_KEYWORDS = [
    "dissertation", "毕业论文", "学位论文", "thesis",
    "literature review", "文献综述", "开题", "答辩", "论文-",
]


_AI_TOOL_TITLE_MARKERS = [
    "- claude",       # Claude.ai（URL 有时为空）
    "- chatgpt",      # ChatGPT
    "- gemini",       # Google Gemini
    "claude.ai",
]

_BILIBILI_TITLE_MARKERS = [
    "哔哩哔哩",
    "bilibili",
]


def get_hardcoded_category(
    domain: str, app_name: str, activity_type: str, window_title: str = "", url: str = ""
) -> str | None:
    """硬编码规则优先匹配，返回类别字符串或 None。"""
    if "Safari" in (app_name or "") and not (url or "").strip() and not (window_title or "").strip():
        return "其他/系统后台"
    title_lower = window_title.lower()
    if any(kw in title_lower for kw in _DISSERTATION_KEYWORDS):
        return "学校学习/论文相关"
    if activity_type in ("idle", "dock"):
        return "其他/系统后台"
    if url.startswith("favorites://"):
        return "其他/系统后台"
    if window_title in ("起始页", "Start Page") and not url and not domain:
        return "其他/系统后台"
    if any(m in title_lower for m in _AI_TOOL_TITLE_MARKERS):
        return "自主学习/AI工具"
    if any(m in title_lower for m in _BILIBILI_TITLE_MARKERS):
        return "娱乐/视频"
    app = app_name or ""
    if "Zotero" in app:
        return "学校学习/文献管理"
    if "微信读书" in app:
        return "自主学习/读书"
    if "Steam" in app:
        return "娱乐/游戏"
    if any(x in app for x in ("Pages", "Numbers", "Keynote", "Word")):
        return "学校学习/写作"
    if domain in HARDCODED_DOMAINS:
        return HARDCODED_DOMAINS[domain]
    return None


def cache_key(domain: str, app_name: str) -> str:
    """生成缓存键：有域名用域名，否则用 'app:应用名'。"""
    if domain:
        return domain
    return f"app:{app_name}" if app_name else "app:unknown"


# ── 数据库初始化 ──────────────────────────────────────────────────────────────
def setup_db(conn: sqlite3.Connection) -> None:
    """确保所有必要的表和列存在。"""
    # activity_log.category 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(activity_log)")}
    if "category" not in cols:
        conn.execute("ALTER TABLE activity_log ADD COLUMN category TEXT")
        print("[初始化] activity_log 已添加 category 列")

    # 域名分类缓存表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_categories (
            domain   TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            source   TEXT NOT NULL DEFAULT 'api'
        )
    """)

    # Safari 历史导入表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS safari_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            url        TEXT NOT NULL,
            title      TEXT,
            visit_time TEXT NOT NULL,
            domain     TEXT,
            category   TEXT,
            UNIQUE(url, visit_time)
        )
    """)
    conn.commit()


# ── 缓存读写 ──────────────────────────────────────────────────────────────────
def get_cached(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT category FROM domain_categories WHERE domain = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def save_to_cache(conn: sqlite3.Connection, entries: list[tuple[str, str, str]]) -> None:
    """entries: [(key, category, source), ...]"""
    conn.executemany(
        "INSERT OR REPLACE INTO domain_categories (domain, category, source) VALUES (?, ?, ?)",
        entries,
    )
    conn.commit()


# ── API 分类 ──────────────────────────────────────────────────────────────────
def _parse_api_response(raw: str, keys: list[str]) -> list[str] | None:
    """解析 API 返回的 JSON，成功返回与 keys 等长的类别列表，失败返回 None。"""
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or len(parsed) != len(keys):
        return None
    cats: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            cats.append(normalize_category(item))
        elif isinstance(item, dict):
            raw_cat = f"{item.get('一级', '其他')}/{item.get('二级', '待分类')}"
            cats.append(normalize_category(raw_cat))
        else:
            cats.append("其他/待分类")
    return cats


def classify_keys_via_api(client: anthropic.Anthropic, keys: list[str]) -> dict[str, str]:
    """
    批量分类域名或应用键，返回 {key: category}。
    失败自动重试最多 3 次（间隔 1 秒），全部失败归入「其他/待分类」。
    """
    lines = [
        f"{i}. {'应用：' + key[4:] if key.startswith('app:') else '域名：' + key}"
        for i, key in enumerate(keys, 1)
    ]
    prompt = "\n".join(lines)

    for attempt in range(1, 4):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            # 部分模型（如 DeepSeek）先返回 ThinkingBlock，取第一个 TextBlock
            raw = next(
                (blk.text for blk in msg.content if hasattr(blk, "text")), ""
            ).strip()
            if not raw:
                raise ValueError("API 返回空响应")
            cats = _parse_api_response(raw, keys)
            if cats is not None:
                return dict(zip(keys, cats))
            print(f"  [警告] 第{attempt}次：返回长度不符")
        except Exception as e:
            print(f"  [警告] 第{attempt}次失败：{e}")
        if attempt < 3:
            time.sleep(1)

    return {k: "其他/待分类" for k in keys}


def resolve_key_map(
    conn: sqlite3.Connection,
    client: anthropic.Anthropic | None,
    keys: set[str],
) -> dict[str, str]:
    """
    给定一组缓存键，返回 {key: category}。
    顺序：DB 缓存 → API（批量）。
    硬编码的键不应出现在 keys 里（由调用方提前过滤）。
    """
    key_map: dict[str, str] = {}
    unknown: list[str] = []

    for key in keys:
        cached = get_cached(conn, key)
        if cached:
            key_map[key] = cached
        else:
            unknown.append(key)

    if not unknown:
        return key_map

    if client is None:
        for k in unknown:
            key_map[k] = "其他/待分类"
        return key_map

    print(f"  [API] {len(unknown)} 个新域名/应用待分类…")
    new_entries: list[tuple[str, str, str]] = []
    for i in range(0, len(unknown), DOMAIN_BATCH):
        batch = unknown[i : i + DOMAIN_BATCH]
        result = classify_keys_via_api(client, batch)
        key_map.update(result)
        new_entries.extend((k, result[k], "api") for k in batch)

    if new_entries:
        save_to_cache(conn, new_entries)

    return key_map


# ── 活动日志分类 ──────────────────────────────────────────────────────────────
def fetch_unclassified_log(
    conn: sqlite3.Connection,
    date: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    q = """
        SELECT id, app_name, window_title, activity_type, url
        FROM activity_log
        WHERE category IS NULL
    """
    params: list = []
    if date:
        q += " AND date = ?"
        params.append(date)
    if since:
        q += " AND timestamp >= ?"
        params.append(since)
    q += " ORDER BY rowid ASC"
    if limit:
        q += " LIMIT ?"
        params.append(limit)

    return [
        {
            "id": r[0],
            "app_name": r[1] or "",
            "window_title": r[2] or "",
            "activity_type": r[3] or "",
            "url": r[4] or "",
        }
        for r in conn.execute(q, params).fetchall()
    ]


def classify_activity_log(
    conn: sqlite3.Connection,
    client: anthropic.Anthropic | None,
    date: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> int:
    records = fetch_unclassified_log(conn, date=date, since=since, limit=limit)
    if not records:
        print("activity_log：没有待分类的记录。")
        return 0

    print(f"activity_log：{len(records)} 条待分类…")

    # 收集需要查缓存/API 的键（硬编码已知的跳过）
    keys_for_lookup: set[str] = set()
    for r in records:
        domain = extract_domain(r["url"])
        if get_hardcoded_category(domain, r["app_name"], r["activity_type"], r["window_title"], r["url"]) is None:
            keys_for_lookup.add(cache_key(domain, r["app_name"]))

    key_map = resolve_key_map(conn, client, keys_for_lookup)

    # 应用分类结果
    updates: list[tuple[str, int]] = []
    for r in records:
        domain = extract_domain(r["url"])
        hc = get_hardcoded_category(domain, r["app_name"], r["activity_type"], r["window_title"], r["url"])
        if hc:
            cat = hc
        else:
            cat = key_map.get(cache_key(domain, r["app_name"]), "其他/待分类")
        updates.append((cat, r["id"]))

    conn.executemany("UPDATE activity_log SET category = ? WHERE id = ?", updates)
    conn.commit()
    print(f"  ✓ 完成 {len(updates)} 条")
    try:
        from Foundation import NSDistributedNotificationCenter
        NSDistributedNotificationCenter.defaultCenter() \
            .postNotificationName_object_userInfo_(
                "XiaoDanClassifierDone", None, None
            )
    except Exception:
        pass
    return len(updates)


# ── Safari 历史导入 ────────────────────────────────────────────────────────────
def import_safari_history(
    conn: sqlite3.Connection,
    client: anthropic.Anthropic | None,
    since_date: str | None = None,
) -> int:
    src = os.path.expanduser("~/Library/Safari/History.db")
    if not os.path.exists(src):
        print("未找到 ~/Library/Safari/History.db，跳过 Safari 导入。")
        return 0

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp = f.name
    shutil.copy2(src, tmp)
    try:
        safari = sqlite3.connect(tmp)
        try:
            q = """
                SELECT hi.url, COALESCE(hv.title, ''), hv.visit_time
                FROM history_visits hv
                JOIN history_items hi ON hv.history_item = hi.id
                WHERE hv.visit_time > 0
            """
            params: list = []
            if since_date:
                # Cocoa 时间戳 = Unix 时间戳 - COCOA_EPOCH
                since_unix = datetime.strptime(since_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                ).timestamp()
                params.append(since_unix - COCOA_EPOCH)
                q += " AND hv.visit_time >= ?"
            rows = safari.execute(q, params).fetchall()
        finally:
            safari.close()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)  # 用完立即删除，不留存浏览历史

    if not rows:
        print("Safari 历史：没有可导入的记录。")
        return 0

    print(f"Safari 历史：读取到 {len(rows)} 条访问记录，导入中…")
    inserted = 0
    for url, title, cocoa_time in rows:
        unix_ts = cocoa_time + COCOA_EPOCH
        ts_str = datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        domain = extract_domain(url)
        cur = conn.execute(
            "INSERT OR IGNORE INTO safari_history (url, title, visit_time, domain) VALUES (?, ?, ?, ?)",
            (url, title, ts_str, domain),
        )
        inserted += cur.rowcount

    conn.commit()
    print(f"  新增 {inserted} 条（重复跳过）")

    # 对未分类的 safari_history 行分类
    uncat = conn.execute(
        "SELECT id, url, domain FROM safari_history WHERE category IS NULL"
    ).fetchall()

    if not uncat:
        return inserted

    print(f"  分类 {len(uncat)} 条 Safari 记录…")
    records = [
        {"id": r[0], "url": r[1], "app_name": "", "activity_type": "browser"}
        for r in uncat
    ]

    keys_for_lookup: set[str] = set()
    for r in records:
        domain = extract_domain(r["url"])
        if get_hardcoded_category(domain, "", "browser", url=r["url"]) is None:
            keys_for_lookup.add(cache_key(domain, ""))

    key_map = resolve_key_map(conn, client, keys_for_lookup)

    updates: list[tuple[str, int]] = []
    for r in records:
        domain = extract_domain(r["url"])
        hc = get_hardcoded_category(domain, "", "browser", url=r["url"])
        cat = hc if hc else key_map.get(cache_key(domain, ""), "其他/待分类")
        updates.append((cat, r["id"]))

    conn.executemany("UPDATE safari_history SET category = ? WHERE id = ?", updates)
    conn.commit()
    print("  ✓ Safari 历史分类完成")

    return inserted


# ── 统计输出 ──────────────────────────────────────────────────────────────────
def print_distribution(conn: sqlite3.Connection) -> None:
    print("\n── 活动日志类别分布 ──")
    rows = conn.execute(
        "SELECT category, COUNT(*) as n FROM activity_log "
        "WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC"
    ).fetchall()
    if rows:
        max_n = max(n for _, n in rows)
        for cat, n in rows:
            bar_len = round(n / max_n * 30) if max_n else 0
            print(f"  {(cat or '未分类'):<18} {n:5d}  {'█' * bar_len}")
    else:
        print("  （无数据）")

    safari_total = conn.execute(
        "SELECT COUNT(*) FROM safari_history"
    ).fetchone()[0]
    if safari_total:
        print(f"\n── Safari 历史类别分布（共 {safari_total} 条）──")
        rows = conn.execute(
            "SELECT category, COUNT(*) as n FROM safari_history "
            "WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC"
        ).fetchall()
        if rows:
            max_n = max(n for _, n in rows)
            for cat, n in rows:
                bar_len = round(n / max_n * 30) if max_n else 0
                print(f"  {(cat or '未分类'):<18} {n:5d}  {'█' * bar_len}")


# ── 主程序 ────────────────────────────────────────────────────────────────────
def recheck_other_category(
    conn: sqlite3.Connection,
    client: "anthropic.Anthropic | None",
    date_str: str | None = None,
) -> int:
    """重新检查 category LIKE '其他%'（排除系统后台）的记录。
    先走更新后的硬编码规则，再对剩余记录强制调 API。
    只有新分类不以「其他」开头时才更新数据库。
    """
    target_date = date_str or str(date.today())
    rows = conn.execute(
        """SELECT id, app_name, window_title, url, activity_type
           FROM activity_log
           WHERE date = ?
             AND category LIKE '其他%'
             AND category != '其他/系统后台'""",
        (target_date,),
    ).fetchall()

    if not rows:
        print(f"没有需要重新检查的「其他」记录（{target_date}）。")
        return 0

    print(f"重新检查 {len(rows)} 条「其他」记录…")

    # 第一轮：硬编码规则（规则可能已更新）
    hard_updates: list[tuple[str, int]] = []
    remaining = []
    for rid, app, title, url, atype in rows:
        domain = extract_domain(url or "")
        hc = get_hardcoded_category(domain, app, atype, title, url or "")
        if hc and not hc.startswith("其他"):
            hard_updates.append((hc, rid))
        else:
            remaining.append((rid, app, title, url, atype))

    if hard_updates:
        conn.executemany("UPDATE activity_log SET category = ? WHERE id = ?", hard_updates)
        conn.commit()
        print(f"  硬编码规则更新: {len(hard_updates)} 条")

    if not remaining:
        return len(hard_updates)

    if client is None:
        print(f"  [提示] 无 API 客户端，跳过剩余 {len(remaining)} 条 API 重分类")
        return len(hard_updates)

    # 第二轮：清除缓存后强制走 API
    keys: set[str] = set()
    for rid, app, title, url, atype in remaining:
        keys.add(cache_key(extract_domain(url or ""), app))

    for k in keys:
        conn.execute("DELETE FROM domain_categories WHERE domain = ?", (k,))
    conn.commit()

    key_map = resolve_key_map(conn, client, keys)

    api_updates: list[tuple[str, int]] = []
    for rid, app, title, url, atype in remaining:
        new_cat = key_map.get(cache_key(extract_domain(url or ""), app), "其他/待分类")
        if not new_cat.startswith("其他"):
            api_updates.append((new_cat, rid))

    if api_updates:
        conn.executemany("UPDATE activity_log SET category = ? WHERE id = ?", api_updates)
        conn.commit()
        print(f"  API 重分类更新: {len(api_updates)} 条")
    else:
        print("  API 重分类：无变化")

    return len(hard_updates) + len(api_updates)


def main() -> None:
    parser = argparse.ArgumentParser(description="小蛋活动分类器（Claude Haiku 4.5）")
    parser.add_argument("--date", help="只处理指定日期 YYYY-MM-DD")
    parser.add_argument("--since", help="只处理此时刻之后的记录 'YYYY-MM-DD HH:MM:SS'")
    parser.add_argument("--limit", type=int, help="最多处理 N 条记录（仅影响 activity_log）")
    parser.add_argument("--db", default=DB_PATH, help=f"数据库路径（默认 {DB_PATH}）")
    parser.add_argument(
        "--import-safari", action="store_true",
        help="导入 Safari 浏览历史到 safari_history 表并分类",
    )
    parser.add_argument(
        "--no-api", action="store_true",
        help="跳过 API 调用，只使用硬编码规则和 domain_categories 缓存",
    )
    parser.add_argument(
        "--recheck-other", action="store_true",
        help="重新检查今天（或 --date 指定日期）category LIKE '其他%%' 的记录，尝试重新分类",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    setup_db(conn)

    # 建立 API 客户端（除非 --no-api）
    client = None
    if not args.no_api:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            print("[提示] 未设置 ANTHROPIC_API_KEY，将只使用硬编码规则和缓存（等同于 --no-api）。")

    if args.recheck_other:
        recheck_other_category(conn, client, date_str=args.date)
        print_distribution(conn)
        conn.close()
        return

    if args.import_safari:
        import_safari_history(conn, client, since_date=args.date)

    classify_activity_log(
        conn, client,
        date=args.date,
        since=args.since,
        limit=args.limit,
    )

    print_distribution(conn)
    conn.close()


if __name__ == "__main__":
    main()
