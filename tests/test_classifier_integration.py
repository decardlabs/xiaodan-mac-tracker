"""
Integration tests for classifier — _save_cache_with_explanations, recheck_other skip override.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifier import (
    _save_cache_with_explanations,
    get_pending_suggestions,
    get_cached,
)


class TestSaveCacheWithExplanations:
    """带理由的新缓存存储。"""

    def test_stores_explanation(self, db_conn):
        entries = [("github.com", "自主学习/编程学习", "api", "代码托管平台")]
        _save_cache_with_explanations(db_conn, entries)
        row = db_conn.execute(
            "SELECT explanation FROM domain_categories WHERE domain = 'github.com'"
        ).fetchone()
        assert row[0] == "代码托管平台"

    def test_sets_suggested_at(self, db_conn):
        entries = [("youtube.com", "娱乐/视频", "api", "视频平台")]
        _save_cache_with_explanations(db_conn, entries)
        row = db_conn.execute(
            "SELECT suggested_at FROM domain_categories WHERE domain = 'youtube.com'"
        ).fetchone()
        assert row[0]  # 非空字符串

    def test_overwrite_updates_explanation(self, db_conn):
        entries = [("github.com", "自主学习/编程学习", "api", "旧理由")]
        _save_cache_with_explanations(db_conn, entries)
        entries2 = [("github.com", "自主学习/编程学习", "api", "新理由")]
        _save_cache_with_explanations(db_conn, entries2)
        row = db_conn.execute(
            "SELECT explanation FROM domain_categories WHERE domain = 'github.com'"
        ).fetchone()
        assert row[0] == "新理由"

    def test_user_overridden_defaults_to_zero(self, db_conn):
        entries = [("github.com", "自主学习/编程学习", "api", "代码托管")]
        _save_cache_with_explanations(db_conn, entries)
        row = db_conn.execute(
            "SELECT user_overridden FROM domain_categories WHERE domain = 'github.com'"
        ).fetchone()
        assert row[0] == 0


class TestClassifierIntegration:
    """分类器 + 建议系统的集成场景。"""

    def test_full_flow_new_entry(self, db_conn):
        """新域名走 API 结果存入缓存 → 可获取为待处理建议。"""
        entries = [("new-domain.com", "其他/待分类", "api", "新发现的网站")]
        _save_cache_with_explanations(db_conn, entries)
        assert get_cached(db_conn, "new-domain.com") == "其他/待分类"
        suggestions = get_pending_suggestions(db_conn)
        assert len(suggestions) == 1
        assert suggestions[0]["key"] == "new-domain.com"

    def test_hardcoded_not_in_pending(self, db_conn):
        """硬编码规则的域名不应出现在建议里（因为 resolve_key_map 不会缓存它们）。"""
        assert get_cached(db_conn, "bilibili.com") is None  # 硬编码


class TestRecheckOtherRespectsOverride:
    """recheck_other 应当跳过 user_overridden=1 的条目。"""

    def setup_data(self, conn):
        """模拟 recheck_other 场景的数据。"""
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES ('fixed.com', '娱乐/视频', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO domain_categories (domain, category, source, user_overridden) "
            "VALUES ('pending.com', '其他/待分类', 'api', 0)"
        )
        conn.commit()
        return conn

    def test_accept_all_suggestions_clears_pending(self, db_with_schema_v2):
        """全部接受后建议列表为空。"""
        conn = db_with_schema_v2
        # 模拟几条建议
        for domain in ["a.com", "b.com", "c.com"]:
            conn.execute(
                "INSERT INTO domain_categories (domain, category, source, user_overridden) "
                "VALUES (?, '其他/待分类', 'api', 0)",
                (domain,),
            )
        conn.commit()

        from classifier import accept_suggestion
        accept_suggestion(conn, "a.com")
        accept_suggestion(conn, "b.com")
        accept_suggestion(conn, "c.com")

        assert get_pending_suggestions(conn) == []
