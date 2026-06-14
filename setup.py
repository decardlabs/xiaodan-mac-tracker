"""
py2app 打包配置 —— 将 XiaoDan 打包为原生 macOS .app bundle
使用: python setup.py py2app
"""
from setuptools import setup

APP = ["tracker.py"]
DATA_FILES = [
    "icon.png",
    "icon@2x.png",
]
OPTIONS = {
    "plist": {
        "CFBundleName": "XiaoDan",
        "CFBundleDisplayName": "小蛋",
        "CFBundleIdentifier": "com.decard.xiaodan",
        "CFBundleVersion": "0.4.0",
        "CFBundleShortVersionString": "0.4.0",
        "CFBundleIconFile": "icon",
        "CFBundleIconName": "icon",
        # 状态栏应用（无 Dock 图标）
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription":
            "小蛋需要访问窗口标题来追踪应用使用时长。",
        "NSHumanReadableCopyright": "MIT License",
    },
    # 不指定 packages，让 py2app 自动分析依赖
    # 排除不需要的大包
    "excludes": [
        "tkinter", "matplotlib", "numpy", "pandas",
        "scipy", "pytest", "setuptools",
        "pip", "wheel", "packaging",
    ],
    # 不创建 DMG，只生成 .app
    "dist_dir": "dist",
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    name="XiaoDan",
    version="0.4.0",
)
