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
    # wellness_activities.json 由 wellness.py 通过 __file__ 相对路径加载
    # py2app 会把它放到 Contents/Resources/，需配合 sys._MEIPASS 或
    # resourcepath() 读取；打包后如有路径问题请参考注释 [1]。
    ("", ["wellness_activities.json"]),
    ("web", ["web/app.py"]),
]

OPTIONS = {
    # menubar-only 应用必须关掉 argv_emulation
    "argv_emulation": False,
    "excludes": ["tkinter"],
    "iconfile": "xiaodan_icon.icns",
    "packages": [
        "AppKit",
        "Foundation",
        "ApplicationServices",
        "Quartz",
        "objc",
        "analyzer",
        "classifier",
        "wellness",
        "report_window",
        "ui",
    ],
    "plist": {
        "CFBundleName": "小蛋",
        "CFBundleDisplayName": "小蛋",
        "CFBundleIdentifier": "com.xiaodan.desktoptracker",
        "CFBundleVersion": "0.62",
        "CFBundleShortVersionString": "0.62",
        "CFBundleIconFile": "xiaodan_icon",
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

# [1] 打包后资源路径说明：
#     wellness.py 当前用 os.path.dirname(os.path.abspath(__file__)) 拼路径，
#     在 .app 包内 __file__ 指向 Contents/Resources/lib/pythonX.Y/...，
#     不等于 Contents/Resources/。如打包后报 FileNotFoundError，
#     把 wellness.py 第5行改为：
#
#         import sys
#         _BASE = getattr(sys, "_MEIPASS", None) or \
#                 os.path.join(os.path.dirname(sys.executable), "..", "Resources")
#         _DATA_PATH = os.path.join(_BASE, "wellness_activities.json")
