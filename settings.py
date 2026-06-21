import json
import os

_SETTINGS_DIR = os.path.expanduser("~/Library/Application Support/XiaoDan")
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "settings.json")

# 预设标签清单（只读常量，供 UI 展示初始模板用）
# 与 settings["custom_categories"] 是两份独立的数据：
#   这里是出厂默认值，settings 里是用户当前生效配置（可能已被修改）
DEFAULT_CATEGORY_PRESETS: dict[str, list[str]] = {
    "自主学习": ["编程学习", "看教程视频", "阅读技术资料", "读书"],
    "学校学业": ["写论文/报告", "阅读文献", "课程作业", "上网课"],
    "工作/项目": ["写代码", "写文档", "项目沟通", "规划"],
    "娱乐":     ["看视频/影视", "玩游戏", "听音乐", "漫画/小说"],
    "社交通讯": ["即时通讯", "视频通话", "浏览社交媒体"],
    "生活事务": ["网购", "金融/财务", "信息查找", "计划整理"],
    "其他":     ["系统操作", "待分类"],
}

_DEFAULTS = {
    "chart_mode": "donut",
    "wellness_enabled": False,
    "report_time": [19, 0],
    "standup_reminder_enabled": False,
    "standup_interval_minutes": 45,
    "api_enabled": True,
    "custom_categories": DEFAULT_CATEGORY_PRESETS,
}


def load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        merged = dict(_DEFAULTS)
    # 防止调用方直接持有 DEFAULT_CATEGORY_PRESETS 引用而意外写穿（浅复制只复制一层）
    if merged.get("custom_categories") is DEFAULT_CATEGORY_PRESETS:
        merged["custom_categories"] = {k: list(v) for k, v in DEFAULT_CATEGORY_PRESETS.items()}
    return merged


def save_settings(settings: dict) -> None:
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[settings] 保存失败: {e}")
