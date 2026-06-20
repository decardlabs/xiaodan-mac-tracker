"""
py2app 打包配置 — 小蛋桌面监控程序

打包步骤：
    pip install py2app
    python setup.py py2app

生成产物在 dist/小蛋.app
"""

from setuptools import setup

APP = ["tracker.py"]

DATA_FILES = [
    # 资源文件统一放到 Contents/Resources/，打包后用 RESOURCEPATH 环境变量定位
    # （见 wellness.py / report_window.py 的 sys.frozen 分支）
    ("", ["wellness_activities.json", "chart.umd.min.js", "standup_icon.png"]),
    ("web", ["web/app.py"]),
]

OPTIONS = {
    # menubar-only 应用必须关掉 argv_emulation
    "argv_emulation": False,
    "excludes": ["tkinter"],
    "iconfile": "XiaoDan.icns",
    "packages": [
        "AppKit",
        "Foundation",
        "ApplicationServices",
        "Quartz",
        "objc",
        "analyzer",
        "classifier",
        "settings",
        "standup_reminder",
        "wellness",
        "report_window",
        "ui",
        "anthropic",
        "dotenv",
    ],
    "plist": {
        "CFBundleName": "小蛋",
        "CFBundleDisplayName": "小蛋",
        "CFBundleIdentifier": "com.xiaodan.desktoptracker",
        "CFBundleVersion": "0.77",
        "CFBundleShortVersionString": "0.77",
        "CFBundleIconFile": "XiaoDan",
        # 菜单栏应用：隐藏 Dock 图标（等价于 app.setActivationPolicy_(1)）
        "LSUIElement": True,
        # 权限说明文字（macOS 隐私提示）
        "NSAccessibilityUsageDescription": "小蛋需要辅助功能权限以读取当前活动窗口标题。",
        "NSAppleEventsUsageDescription": "小蛋需要发送 Apple 事件以获取应用信息。",
    },
}

setup(
    name="小蛋",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

# 打包后资源定位约定（wellness.py 和 report_window.py 均已采用）：
#   if getattr(sys, "frozen", False):
#       _base = os.environ["RESOURCEPATH"]   # py2app 设置的 Contents/Resources/ 路径
#   else:
#       _base = os.path.dirname(os.path.abspath(__file__))
