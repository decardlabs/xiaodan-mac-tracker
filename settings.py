import json
import os

_SETTINGS_DIR = os.path.expanduser("~/Library/Application Support/XiaoDan")
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "settings.json")

_DEFAULTS = {
    "chart_mode": "donut",
    "wellness_enabled": False,
    "report_time": [19, 0],
    "standup_reminder_enabled": False,
    "standup_interval_minutes": 45,
    "api_enabled": True,
}


def load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def save_settings(settings: dict) -> None:
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[settings] 保存失败: {e}")
