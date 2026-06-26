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


# ── API Key 失效状态（进程内共享，供 report_window 读取）────────────────────────
_api_key_invalid: bool = False


def mark_api_key_invalid() -> None:
    global _api_key_invalid
    _api_key_invalid = True


def is_api_key_invalid() -> bool:
    return _api_key_invalid


def clear_api_key_invalid() -> None:
    global _api_key_invalid
    _api_key_invalid = False


# ── 服务格式不兼容状态（进程内共享，供 report_window 读取）─────────────────────
_api_format_error: bool = False


def mark_api_format_error() -> None:
    global _api_format_error
    _api_format_error = True


def is_api_format_error() -> bool:
    return _api_format_error


def clear_api_format_error() -> None:
    global _api_format_error
    _api_format_error = False


# ── 配置 ──────────────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/Library/Application Support/XiaoDan/activity.db")
MODEL = "deepseek-v4-flash"  # 默认模型名，实际运行时从 settings 读取
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
每条为 {{"类别": "一级/二级", "理由": "一句话说明"}}。理由用中文 10-20 字。只输出 JSON。"""


# ── 工具函数 ──────────────────────────────────────────────────────────────────
# 一级分类白名单
_VALID_L1 = {"学校学习", "自主学习", "娱乐", "其他"}

# 异形 → 标准映射（在白名单校验之前应用）
_CATEGORY_ALIASES: dict[str, str] = {
    "其他/工具搜索":    "其他/工具/搜索",
    "学校学习/课程作业": "学校学习/课程/作业",
    "娱乐/读书":       "自主学习/读书",
}


def normalize_category(cat: str, valid_l1: frozenset | None = None) -> str:
    """标准化 API 返回的类别字符串。
    valid_l1: 自定义大类名称集合（使用自定义分类时传入），None 时使用默认白名单。
    """
    cat = (cat or "").strip()
    l1_set = valid_l1 if valid_l1 is not None else _VALID_L1
    # 仅在使用默认分类时应用已知别名修正（别名是针对默认类别的）
    if valid_l1 is None:
        cat = _CATEGORY_ALIASES.get(cat, cat)
    # 自定义分类的大类名可能包含 "/"（如 "工作/项目"），用前缀匹配而非 split
    if valid_l1 is not None:
        matched = any(cat.startswith(l1 + "/") for l1 in valid_l1)
        if not matched:
            return "其他/待分类"
        return cat
    parts = cat.split("/", 1)
    if len(parts) < 2 or not parts[1] or parts[0] not in l1_set:
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
def _parse_api_response(
    raw: str, keys: list[str], valid_l1: frozenset | None = None
) -> list[str] | None:
    """解析 API 返回的 JSON，成功返回与 keys 等长的类别列表，失败返回 None。

    支持三种格式（按优先级）：
      1. [{"类别":"一级/二级", "理由":"..."}, ...]  — 新格式含理由
      2. [{"一级":"...", "二级":"..."}, ...]         — 旧 dict 格式
      3. ["一级/二级", ...]                           — 旧 string 格式
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or len(parsed) != len(keys):
        return None
    cats: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            cats.append(normalize_category(item, valid_l1=valid_l1))
        elif isinstance(item, dict):
            # 优先使用 "类别" 键（含理由的新格式）
            if "类别" in item:
                raw_cat = str(item["类别"])
            else:
                raw_cat = f"{item.get('一级', '其他')}/{item.get('二级', '待分类')}"
            cats.append(normalize_category(raw_cat, valid_l1=valid_l1))
        else:
            cats.append("其他/待分类")
    return cats


def _extract_explanations(raw: str, keys: list[str]) -> list[str | None] | None:
    """从新格式 API 响应中提取理由列表。与 keys 等长，无理由的条目为 None。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or len(parsed) != len(keys):
        return None
    result: list[str | None] = []
    for item in parsed:
        if isinstance(item, dict) and "理由" in item:
            result.append(str(item["理由"]).strip() or None)
        else:
            result.append(None)
    return result


def _build_custom_system_prompt(custom_categories: dict) -> str:
    """根据用户自定义分类构造 system prompt。"""
    lines = []
    for cat, subs in custom_categories.items():
        if subs:
            lines.append(f"  {cat}：{' | '.join(subs)}")
        else:
            lines.append(f"  {cat}：（无子分类）")
    guide = "用户自定义分类标签：\n\n" + "\n".join(lines)
    return (
        "你是一个个人时间管理分析助手，对用户的 Mac 上网和应用使用记录分类。\n\n"
        + guide
        + "\n\n选择最贴切的子分类，回复格式：JSON 数组，每项对应输入列表中的一条，"
        '每条为 {"类别": "大类/子类", "理由": "一句话说明"}。理由用中文 10-20 字。只输出 JSON。'
    )


def classify_keys_via_api(
    client: anthropic.Anthropic,
    keys: list[str],
    custom_categories: dict | None = None,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """
    批量分类域名或应用键，返回 (category_dict, explanation_dict)。
    category_dict: {key: "一级/二级"}
    explanation_dict: {key: "理由" | None}
    custom_categories: 用户自定义分类（不为 None 时使用自定义 prompt 和类别校验）。
    失败自动重试最多 3 次（间隔 1 秒），全部失败归入「其他/待分类」。
    """
    lines = [
        f"{i}. {'应用：' + key[4:] if key.startswith('app:') else '域名：' + key}"
        for i, key in enumerate(keys, 1)
    ]
    prompt = "\n".join(lines)

    if custom_categories is not None:
        system = _build_custom_system_prompt(custom_categories)
        valid_l1: frozenset | None = frozenset(custom_categories.keys())
    else:
        system = SYSTEM_PROMPT
        valid_l1 = None

    try:
        from settings import get_api_model
        model = get_api_model(default=MODEL)
    except Exception:
        model = MODEL

    for attempt in range(1, 4):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            # 部分模型（如 DeepSeek）先返回 ThinkingBlock，取第一个 TextBlock
            raw = next(
                (blk.text for blk in msg.content if hasattr(blk, "text")), ""
            ).strip()
            if not raw:
                raise ValueError("API 返回空响应")
            cats = _parse_api_response(raw, keys, valid_l1=valid_l1)
            if cats is not None:
                exps = _extract_explanations(raw, keys)
                exp_map: dict[str, str | None] = {}
                for k, e in zip(keys, exps) if exps else zip(keys, [None] * len(keys)):
                    exp_map[k] = e
                return dict(zip(keys, cats)), exp_map
            print(f"  [警告] 第{attempt}次：返回长度不符")
        except anthropic.AuthenticationError:
            mark_api_key_invalid()
            print("  [错误] API Key 认证失败，分类将回退到默认结果")
            break  # 认证错误无需重试
        except anthropic.APIResponseValidationError as e:
            mark_api_format_error()
            print(f"  [错误] 服务响应格式异常，可能不兼容 Anthropic SDK：{e}")
            break  # 格式错误无需重试
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            print(f"  [警告] 第{attempt}次网络连接失败：{e}")
        except Exception as e:
            print(f"  [警告] 第{attempt}次失败：{e}")
        if attempt < 3:
            time.sleep(1)

    fallback_cats = {k: "其他/待分类" for k in keys}
    return fallback_cats, {k: None for k in keys}


def resolve_key_map(
    conn: sqlite3.Connection,
    client: anthropic.Anthropic | None,
    keys: set[str],
    custom_categories: dict | None = None,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """
    给定一组缓存键，返回 (key→category, key→explanation)。
    顺序：DB 缓存 → API（批量）。
    硬编码的键不应出现在 keys 里（由调用方提前过滤）。
    """
    key_map: dict[str, str] = {}
    exp_map: dict[str, str | None] = {}
    unknown: list[str] = []

    for key in keys:
        cached = get_cached(conn, key)
        if cached:
            key_map[key] = cached
            exp_map[key] = None  # 缓存条目暂存无理由
        else:
            unknown.append(key)

    if not unknown:
        return key_map, exp_map

    if client is None:
        for k in unknown:
            key_map[k] = "其他/待分类"
            exp_map[k] = None
        return key_map, exp_map

    print(f"  [API] {len(unknown)} 个新域名/应用待分类…")
    new_entries: list[tuple[str, str, str, str]] = []  # (key, category, source, explanation)
    for i in range(0, len(unknown), DOMAIN_BATCH):
        batch = unknown[i : i + DOMAIN_BATCH]
        result_cats, result_exps = classify_keys_via_api(client, batch, custom_categories=custom_categories)
        key_map.update(result_cats)
        exp_map.update(result_exps)
        for k in batch:
            new_entries.append((k, result_cats[k], "api", result_exps.get(k, "") or ""))

    if new_entries:
        _save_cache_with_explanations(conn, new_entries)

    return key_map, exp_map


def _save_cache_with_explanations(
    conn: sqlite3.Connection,
    entries: list[tuple[str, str, str, str]],
) -> None:
    """entries: [(key, category, source, explanation), ...]
    先确保 domain_categories 表有 explanation 列。
    """
    _ensure_domain_categories_schema(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO domain_categories (domain, category, source, explanation, suggested_at) "
        "VALUES (?, ?, ?, ?, datetime('now', 'localtime'))",
        [(k, cat, src, exp) for k, cat, src, exp in entries],
    )
    conn.commit()


# ── Schema 迁移 ──────────────────────────────────────────────────────────────
def _ensure_domain_categories_schema(conn: sqlite3.Connection) -> None:
    """确保 domain_categories 表包含所有新列，兼容旧数据库。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(domain_categories)")}
    for col, dtype in [("explanation", "TEXT DEFAULT ''"),
                       ("user_overridden", "INTEGER DEFAULT 0"),
                       ("suggested_at", "TEXT DEFAULT ''")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE domain_categories ADD COLUMN {col} {dtype}")
    # 迁移旧 API 条目：把 source='api' 且 user_overridden IS NULL 的旧记录设为 user_overridden=0
    # （SQLite ALTER 加的 DEFAULT 只对新行生效，旧行 user_overridden 仍为 NULL）
    conn.execute(
        "UPDATE domain_categories SET explanation = '', user_overridden = 0, "
        "suggested_at = COALESCE(suggested_at, datetime('now', 'localtime')) "
        "WHERE source = 'api' AND user_overridden IS NULL"
    )
    conn.commit()


# ── 建议/重分类 API ──────────────────────────────────────────────────────────
def get_pending_suggestions(conn: sqlite3.Connection) -> list[dict]:
    """返回所有未处理的分类建议（user_overridden=0 的 API 分类结果）。
    每个条目: {key, category, explanation, suggested_at}。
    """
    _ensure_domain_categories_schema(conn)
    rows = conn.execute(
        "SELECT domain, category, explanation, suggested_at "
        "FROM domain_categories "
        "WHERE source = 'api' AND user_overridden = 0 "
        "ORDER BY suggested_at DESC"
    ).fetchall()
    return [
        {"key": r[0], "category": r[1], "explanation": r[2] or "", "suggested_at": r[3]}
        for r in rows
    ]


def accept_suggestion(conn: sqlite3.Connection, key: str) -> None:
    """接受建议：标记 user_overridden=1，后续自动分类跳过此条目。"""
    conn.execute("UPDATE domain_categories SET user_overridden = 1 WHERE domain = ?", (key,))
    conn.commit()


def reclassify_suggestion(
    conn: sqlite3.Connection,
    key: str,
    new_category: str,
    explanation: str = "",
) -> None:
    """用户手动重分类：更新 category + 标记 user_overridden=1。
    同时更新 activity_log 中该域名/应用的所有历史记录的 category 字段。
    """
    _ensure_domain_categories_schema(conn)

    conn.execute(
        "INSERT OR REPLACE INTO domain_categories (domain, category, source, explanation, user_overridden, suggested_at) "
        "VALUES (?, ?, 'user', ?, 1, datetime('now', 'localtime'))",
        (key, new_category, explanation),
    )

    # 追溯更新 activity_log
    if key.startswith("app:"):
        app_name = key[4:]
        conn.execute(
            "UPDATE activity_log SET category = ? WHERE app_name = ?",
            (new_category, app_name),
        )
    else:
        # 域名匹配：直接用 domain 或 url LIKE %domain%
        conn.execute(
            "UPDATE activity_log SET category = ? WHERE url LIKE ?",
            (new_category, f"%{key}%"),
        )
    conn.commit()


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
    custom_categories: dict | None = None,
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

    key_map, _ = resolve_key_map(conn, client, keys_for_lookup, custom_categories=custom_categories)

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

    key_map, _ = resolve_key_map(conn, client, keys_for_lookup)

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
    custom_categories: dict | None = None,
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

    key_map, _ = resolve_key_map(conn, client, keys, custom_categories=custom_categories)

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


def run_classification(date_str: str | None = None, *, use_api: bool = True, recheck_other: bool = False) -> None:
    """同进程调用入口，供 tracker.py 在后台线程中调用，避免 .app 包内路径问题。"""
    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)
    _ensure_domain_categories_schema(conn)
    client = None
    custom_categories = None
    if use_api:
        from settings import load_settings, is_custom_categories_active, get_api_credentials
        s = load_settings()
        if s.get("api_enabled", True):
            api_key, base_url = get_api_credentials()
            if api_key:
                client = anthropic.Anthropic(api_key=api_key, **({"base_url": base_url} if base_url else {}))
            # 第三层判断：只有用户真正自定义了分类，才把自定义标签传给 API
            if is_custom_categories_active():
                custom_categories = s.get("custom_categories")
    try:
        if recheck_other:
            recheck_other_category(conn, client, date_str=date_str, custom_categories=custom_categories)
        else:
            classify_activity_log(conn, client, date=date_str, custom_categories=custom_categories)
    finally:
        conn.close()


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
        from settings import get_api_credentials
        api_key, base_url = get_api_credentials()
        if api_key:
            client = anthropic.Anthropic(api_key=api_key, **({"base_url": base_url} if base_url else {}))
        else:
            print("[提示] 未配置 API Key，将只使用硬编码规则和缓存（等同于 --no-api）。")

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
