# 小蛋 — Mac 使用时间监控

本地运行的 macOS 使用时间追踪工具，每 30 秒记录一次你在做什么，数据存在本机 SQLite 数据库，不上传任何信息。

## ✨ 功能

- **主活动检测**：通过鼠标位置识别当前操作的窗口和应用
- **浏览器深度识别**：获取当前标签页 URL 和标题，自动区分「看视频」和「普通浏览」
- **背景音乐检测**：识别 Apple Music、Spotify，以及浏览器后台播放的视频/音乐页面
- **窗口标题获取**：通过 macOS 辅助功能 API 读取精确窗口标题
- **系统覆盖层处理**：自动跳过 Dock、Window Server 等系统层，回退到真实前台应用
- **macOS 状态栏显示**：在顶部状态栏展示小蛋图标 + 今日最长应用 + 时长，每 30 秒自动刷新
- **本地 SQLite 存储**：所有记录写入 `~/Library/Application Support/XiaoDan/activity.db`
- **开机自启动**：通过 LaunchAgent 后台常驻运行
- **一键打包**：自带 `build.sh` 生成 `.app` + `.dmg`，普通用户双击安装

## 🍳 安装（普通用户，推荐）

1. 从 [Releases](https://github.com/decardlabs/xiaodan-mac-tracker/releases) 下载 `XiaoDan-v0.3.0.dmg`
2. 双击挂载，把 **小蛋** 拖入 **Applications** 文件夹
3. 在 Applications 中找到「小蛋」→ **右键 → 打开**（首次需绕过 Gatekeeper）
4. 启动后，菜单栏出现 🥚 图标，点击 → 「安装辅助功能权限」→ 在系统设置中勾选「小蛋」
5. （可选）点击菜单栏蛋图标 → 安装 LaunchAgent（开机自启）

> 💡 数据库在 `~/Library/Application Support/XiaoDan/`，符合 macOS 标准。
> 💡 如果你之前用 v0.2.0 装过，旧数据库会自动迁移到新位置。

## 🛠 安装（开发者，从源码）

```bash
git clone https://github.com/decardlabs/xiaodan-mac-tracker.git
cd xiaodan-mac-tracker
python3 -m venv venv
venv/bin/pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices \
    pyobjc-framework-Quartz rumps Pillow
venv/bin/python3 tracker.py
```

## 📦 打包

```bash
./build.sh              # 生成 dist/XiaoDan.app + dist/XiaoDan-v0.3.0.dmg
./build.sh --no-dmg     # 只生成 .app
```

打包脚本会自动：
- 复制 venv（用符号链接节省体积）
- 生成 `Info.plist`（菜单栏 app，不出现在 Dock）
- ad-hoc 签名（避免 Gatekeeper 警告）
- 用 `hdiutil` 创建 `.dmg`，附带 `/Applications` 快捷方式

## 🔓 权限配置

前往 **系统设置 → 隐私与安全性 → 辅助功能**，将运行小蛋的 Python 解释器（或 `.app`）添加到允许列表。
未授权时辅助功能 API 无法读取窗口标题，菜单里点击「安装辅助功能权限…」可直接跳转设置。

## 🚀 开机自启动（LaunchAgent）

```bash
# 安装：注册到当前用户的 launchd
venv/bin/python3 install_launchagent.py install

# 状态
venv/bin/python3 install_launchagent.py status

# 卸载
venv/bin/python3 install_launchagent.py uninstall
```

安装后下次开机自动运行，日志写入 `~/Library/Logs/XiaoDan/`。

## 📊 数据库结构

数据库位置：`~/Library/Application Support/XiaoDan/activity.db`

```sql
CREATE TABLE activity_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT,   -- "2025-01-01 12:00:00"
    date          TEXT,   -- "2025-01-01"
    app_name      TEXT,   -- 应用名称
    window_title  TEXT,   -- 窗口/标签页标题
    activity_type TEXT,   -- "app" | "browser" | "video" | "idle" | "dock"
    url           TEXT,   -- 浏览器当前标签 URL
    bg_music      TEXT    -- 背景音乐信息
);
```

### 查询示例

```bash
sqlite3 ~/Library/Application\ Support/XiaoDan/activity.db
```

```sql
-- 今天各应用使用时长（每条记录 = 30 秒）
SELECT app_name, COUNT(*) * 30 / 60 AS minutes
FROM activity_log
WHERE date = date('now', 'localtime')
GROUP BY app_name
ORDER BY minutes DESC;

-- 今天看了哪些视频
SELECT window_title, url, COUNT(*) * 30 / 60 AS minutes
FROM activity_log
WHERE date = date('now', 'localtime') AND activity_type = 'video'
GROUP BY url
ORDER BY minutes DESC;
```

## 📁 文件清单

```
xiaodan-mac-tracker/
├── tracker.py                    # 主程序
├── install_launchagent.py        # LaunchAgent 安装/卸载脚本
├── build.sh                      # 打包脚本（生成 .app + .dmg）
├── icon.png / icon@2x.png        # 状态栏图标（1x/2x）
├── build_assets/
│   ├── source_icon_1024.png      # 1024px 高清图标源
│   ├── icon.iconset/             # 多尺寸 PNG 集合
│   └── icon.icns                 # macOS 应用的 .icns 图标
└── dist/                         # 打包产物（不上传）
    ├── XiaoDan.app               # 可分发的 .app
    └── XiaoDan-v0.3.0.dmg        # 可分发的 .dmg
```

## 📜 版本

- **v0.3.0** — 可分发 `.app` + `.dmg`、LaunchAgent 自启、辅助功能权限引导、新版图标、数据库迁移到 `~/Library/Application Support/`
- **v0.2.0** — 新增状态栏 + 自定义小蛋图标 + 30秒刷新；新增 Top 3 菜单 + 今日/本周统计弹窗

## 📄 许可

MIT License
