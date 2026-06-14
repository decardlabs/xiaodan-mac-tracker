#!/usr/bin/env python3
"""
install_launchagent.py — 安装/卸载小蛋的 LaunchAgent（开机自启）

用法：
    venv/bin/python3 install_launchagent.py install
    venv/bin/python3 install_launchagent.py uninstall
    venv/bin/python3 install_launchagent.py status
"""
import os
import plistlib
import subprocess
import sys

LAUNCH_AGENT_LABEL = "com.decard.xiaodan"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist")


def _resolve_executable():
    """决定 LaunchAgent 实际执行的命令。
    - 如果是从 .app 启动的：执行 /Applications/XiaoDan.app/Contents/MacOS/launcher.sh
    - 否则：执行当前 venv 下的 python + tracker.py
    """
    # 优先 .app 路径
    app_launcher = "/Applications/XiaoDan.app/Contents/MacOS/launcher.sh"
    if os.path.exists(app_launcher):
        return [app_launcher]

    # 回退到源码运行
    project_root = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(project_root, "venv", "bin", "python3")
    tracker_py = os.path.join(project_root, "tracker.py")
    if os.path.exists(venv_python) and os.path.exists(tracker_py):
        return [venv_python, "-u", tracker_py]

    print("错误：找不到可执行的小蛋程序")
    sys.exit(1)


def install():
    if os.path.exists(PLIST_PATH):
        # 先卸载旧的
        subprocess.run(["launchctl", "unload", PLIST_PATH], check=False)

    program_args = _resolve_executable()
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": program_args,
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "StandardOutPath": os.path.expanduser("~/Library/Logs/XiaoDan/stdout.log"),
        "StandardErrorPath": os.path.expanduser("~/Library/Logs/XiaoDan/stderr.log"),
        "WorkingDirectory": os.path.dirname(program_args[0]) if program_args[0].endswith(".sh") else os.path.dirname(os.path.abspath(__file__)),
    }
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    os.makedirs(os.path.expanduser("~/Library/Logs/XiaoDan"), exist_ok=True)
    subprocess.run(["launchctl", "load", PLIST_PATH], check=True)
    print(f"✅ LaunchAgent 已安装：{PLIST_PATH}")
    print(f"   执行命令：{' '.join(program_args)}")
    print("   下次开机时将自动启动小蛋。")


def uninstall():
    if os.path.exists(PLIST_PATH):
        subprocess.run(["launchctl", "unload", PLIST_PATH], check=False)
        os.remove(PLIST_PATH)
        print(f"✅ LaunchAgent 已卸载：{PLIST_PATH}")
    else:
        print("LaunchAgent 未安装。")


def status():
    if os.path.exists(PLIST_PATH):
        print(f"已安装：{PLIST_PATH}")
        with open(PLIST_PATH, "rb") as f:
            plist = plistlib.load(f)
        print(f"  执行：{' '.join(plist['ProgramArguments'])}")
    else:
        print("未安装。")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall", "status"):
        print(__doc__)
        sys.exit(1)
    {"install": install, "uninstall": uninstall, "status": status}[sys.argv[1]]()
