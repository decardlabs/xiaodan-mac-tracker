"""
pytest fixtures for XiaoDan test suite.
"""
import os
import sys
import sqlite3
import pytest

# 确保能 import 项目模块
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def db_conn():
    """提供内存 SQLite 数据库，预建 activity_log 表 + domain_categories 表。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT,
            window_title TEXT,
            activity_type TEXT,
            url TEXT,
            timestamp TEXT,
            date TEXT,
            category TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_categories (
            domain   TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            source   TEXT NOT NULL DEFAULT 'api'
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def db_with_schema_v2(db_conn):
    """db_conn + domain_categories 拥有全部 v2 列（explanation, user_overridden, suggested_at）。"""
    conn = db_conn
    # 模拟 setup_db 后的状态
    conn.executescript("""
        ALTER TABLE domain_categories ADD COLUMN explanation TEXT DEFAULT '';
        ALTER TABLE domain_categories ADD COLUMN user_overridden INTEGER DEFAULT 0;
        ALTER TABLE domain_categories ADD COLUMN suggested_at TEXT DEFAULT '';
    """)
    conn.commit()
    return conn
