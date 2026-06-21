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
    "api_key": "",
    "api_base_url": "",
    "custom_categories": DEFAULT_CATEGORY_PRESETS,
    "onboarding_completed": False,  # 新用户默认 False，触发首次启动引导
}


def load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        # 方案 A：文件存在但字段缺失 → 老用户升级，视为已完成引导，跳过弹窗
        if "onboarding_completed" not in data:
            merged["onboarding_completed"] = True
    except (FileNotFoundError, json.JSONDecodeError):
        merged = dict(_DEFAULTS)  # 全新安装，onboarding_completed 保持 False
    # 防止调用方直接持有 DEFAULT_CATEGORY_PRESETS 引用而意外写穿（浅复制只复制一层）
    if merged.get("custom_categories") is DEFAULT_CATEGORY_PRESETS:
        merged["custom_categories"] = {k: list(v) for k, v in DEFAULT_CATEGORY_PRESETS.items()}
    return merged


def is_custom_categories_active() -> bool:
    """如果用户的 custom_categories 与 DEFAULT_CATEGORY_PRESETS 存在实质性差异，返回 True。
    比较规则：大类名称集合相同、且每个大类下的子类集合相同，则视为"未自定义"。
    顺序不影响比较结果。
    """
    user_cats = load_settings().get("custom_categories", DEFAULT_CATEGORY_PRESETS)
    if set(user_cats.keys()) != set(DEFAULT_CATEGORY_PRESETS.keys()):
        return True
    return any(
        set(user_cats.get(c, [])) != set(DEFAULT_CATEGORY_PRESETS[c])
        for c in DEFAULT_CATEGORY_PRESETS
    )


def get_api_credentials() -> tuple[str, str]:
    """返回 (api_key, base_url)。api_key 优先读 settings，再回退 ANTHROPIC_API_KEY 环境变量。
    base_url 为空字符串表示使用 Anthropic 官方端点。"""
    s = load_settings()
    api_key = s.get("api_key", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = s.get("api_base_url", "").strip()
    return api_key, base_url


def save_settings(settings: dict) -> None:
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[settings] 保存失败: {e}")
