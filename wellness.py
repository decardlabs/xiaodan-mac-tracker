import json
import random
import os
import sys

if getattr(sys, "frozen", False):
    # py2app 打包环境：RESOURCEPATH 环境变量直接指向 Contents/Resources/
    _base = os.environ["RESOURCEPATH"]
else:
    _base = os.path.dirname(os.path.abspath(__file__))

_DATA_PATH = os.path.join(_base, "wellness_activities.json")

_cache: dict | None = None


def _load() -> dict | None:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_DATA_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        categories = raw.get("categories")
        if not isinstance(categories, dict):
            raise ValueError("缺少 categories 字段或格式错误")
        _cache = categories
        return _cache
    except FileNotFoundError:
        print(f"[wellness] 文件不存在：{_DATA_PATH}")
    except Exception as e:
        print(f"[wellness] 加载失败：{e}")
    return None


def get_categories() -> list[str]:
    data = _load()
    return list(data.keys()) if data else []


def get_random_activity(category: str | None = None) -> dict | None:
    data = _load()
    if not data:
        return None

    if category is not None:
        bucket = data.get(category)
        if not bucket:
            print(f"[wellness] 类别不存在或为空：{category}")
            return None
        items = [(category, item) for item in bucket.get("activities", [])]
    else:
        items = [
            (cat, item)
            for cat, bucket in data.items()
            for item in bucket.get("activities", [])
        ]

    if not items:
        return None

    cat, item = random.choice(items)
    return {
        "id":       item.get("id", ""),
        "text":     item.get("text", ""),
        "duration": item.get("duration", 5),
        "category": cat,
    }
