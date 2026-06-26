"""
Tests for domain_categories data layer — schema migration, suggestions, reclassification.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifier import (
    get_pending_suggestions,
    accept_suggestion,
    reclassify_suggestion,
    save_to_cache,
    _save_cache_with_explanations,
    get_cached,
)


class TestSchemaMigration:
    """数据库升级兼容性。"""

    def test_migration_adds_missing_columns(self, db_conn):
        """_save_cache_with_explanations 自动补全缺失的列。"""
        entries = [("github.com", "自主学习/编程学习", "api", "代码托管平台")]
        _save_cache_with_explanations(db_conn, entries)
        cols = {r[1] for r in db_conn.execute("PRAGMA table_info(domain_categories)")}
        assert "explanation" in cols
        assert "user_overridden" in cols
        assert "suggested_at" in cols

    def test_old_save_to_cache_still_works(self, db_conn):
        """旧的 save_to_cache 仍然兼容（3列格式）。"""
        save_to_cache(db_conn, [("github.com", "自主学习/编程学习", "api")])
        assert get_cached(db_conn, "github.com") == "自主学习/编程学习"

    def test_migration_idempotent(self, db_conn):
        """多次调用迁移不报错。"""
        entries = [("a.com", "娱乐/视频", "api", "视频站")]
        _save_cache_with_explanations(db_conn, entries)
        _save_cache_with_explanations(db_conn, entries)  # 第二次调用


class TestPendingSuggestions:
    """获取未处理的分类建议。"""

    def test_empty_when_no_api_entries(self, db_with_schema_v2):
        conn = db_with_schema_v2
        assert get_pending_suggestions(conn) == []

    def test_returns_api_entries_with_overridden_0(self, db_with_schema_v2):
        conn = db_with_schema_v2
        db_with_schema_v2.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES (?, ?, ?, 0)",
            ("github.com", "自主学习/编程学习", "api"),
        )
        db_with_schema_v2.commit()
        suggestions = get_pending_suggestions(conn)
        assert len(suggestions) == 1
        assert suggestions[0]["key"] == "github.com"
        assert suggestions[0]["category"] == "自主学习/编程学习"

    def test_skips_user_overridden(self, db_with_schema_v2):
        conn = db_with_schema_v2
        for domain, overridden in [("a.com", 0), ("b.com", 1), ("c.com", 0)]:
            conn.execute(
                "INSERT INTO domain_categories (domain, category, source, user_overridden) "
                "VALUES (?, '娱乐/视频', 'api', ?)",
                (domain, overridden),
            )
        conn.commit()
        suggestions = get_pending_suggestions(conn)
        assert len(suggestions) == 2
        keys = {s["key"] for s in suggestions}
        assert "b.com" not in keys

    def test_returns_explanation_and_time(self, db_with_schema_v2):
        conn = db_with_schema_v2
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, explanation, user_overridden, suggested_at) "
            "VALUES (?, ?, 'api', ?, 0, '2026-06-26 12:00:00')",
            ("github.com", "自主学习/编程学习", "代码托管平台"),
        )
        conn.commit()
        s = get_pending_suggestions(conn)[0]
        assert s["explanation"] == "代码托管平台"
        assert s["suggested_at"] == "2026-06-26 12:00:00"

    def test_empty_when_column_missing(self, db_conn):
        """旧数据库（无 user_overridden 列）返回空列表，不崩溃。"""
        assert get_pending_suggestions(db_conn) == []


class TestAcceptSuggestion:
    """接受建议 → 标记 override。"""

    def test_marks_user_overridden(self, db_with_schema_v2):
        conn = db_with_schema_v2
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES ('github.com', '自主学习/编程学习', 'api', 0)",
        )
        conn.commit()
        accept_suggestion(conn, "github.com")
        row = conn.execute(
            "SELECT user_overridden FROM domain_categories WHERE domain = 'github.com'"
        ).fetchone()
        assert row[0] == 1

    def test_accept_removes_from_pending(self, db_with_schema_v2):
        conn = db_with_schema_v2
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES ('github.com', '自主学习/编程学习', 'api', 0)",
        )
        accept_suggestion(conn, "github.com")
        assert get_pending_suggestions(conn) == []


class TestReclassifySuggestion:
    """用户手动重分类。"""

    def test_updates_cache_and_marks_override(self, db_with_schema_v2):
        conn = db_with_schema_v2
        # 先有条 API 分类的缓存
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES ('youtube.com', '娱乐/视频', 'api', 0)",
        )
        conn.commit()

        reclassify_suggestion(conn, "youtube.com", "自主学习/看教程视频", "用户修正")

        row = conn.execute(
            "SELECT category, source, user_overridden FROM domain_categories WHERE domain = 'youtube.com'"
        ).fetchone()
        assert row[0] == "自主学习/看教程视频"
        assert row[1] == "user"
        assert row[2] == 1

    def test_updates_activity_log_by_domain(self, db_with_schema_v2):
        conn = db_with_schema_v2
        conn.executemany(
            "INSERT INTO activity_log (app_name, url, category, date, timestamp) "
            "VALUES (?, ?, ?, '2026-06-26', '2026-06-26 10:00:00')",
            [
                ("Safari", "https://youtube.com/watch?v=abc", "娱乐/视频"),
                ("Safari", "https://youtube.com/watch?v=def", "娱乐/视频"),
            ],
        )
        reclassify_suggestion(conn, "youtube.com", "自主学习/看教程视频")

        rows = conn.execute(
            "SELECT category FROM activity_log WHERE url LIKE '%youtube.com%'"
        ).fetchall()
        assert all(r[0] == "自主学习/看教程视频" for r in rows)

    def test_updates_activity_log_by_app(self, db_with_schema_v2):
        conn = db_with_schema_v2
        conn.executemany(
            "INSERT INTO activity_log (app_name, url, category, date, timestamp) "
            "VALUES (?, ?, ?, '2026-06-26', '2026-06-26 10:00:00')",
            [
                ("VSCode", "", "自主学习/编程学习"),
                ("VSCode", "", "自主学习/编程学习"),
            ],
        )
        reclassify_suggestion(conn, "app:VSCode", "工作/项目/写代码")

        rows = conn.execute(
            "SELECT category FROM activity_log WHERE app_name = 'VSCode'"
        ).fetchall()
        assert all(r[0] == "工作/项目/写代码" for r in rows)
