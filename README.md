# 🥚 小蛋 — Mac 使用时间监控

本地运行的 macOS 使用时间追踪工具，每 5 秒记录一次你在做什么，数据存在本机 SQLite 数据库，不上传任何信息。

## 功能

- **主活动检测**：通过鼠标位置识别当前操作的窗口和应用
- **浏览器深度识别**：获取当前标签页 URL 和标题，自动区分「看视频」和「普通浏览」
- **背景音乐检测**：识别 Apple Music、Spotify，以及浏览器后台播放的视频/音乐页面
- **窗口标题获取**：通过 macOS 辅助功能 API 读取精确窗口标题
- **系统覆盖层处理**：自动跳过 Dock、Window Server 等系统层，回退到真实前台应用
- **本地 SQLite 存储**：所有记录写入 `activity.db`，方便自行查询分析
- **开机自启动**：通过 launchd 后台常驻运行

## 支持的浏览器

- Safari
- Google Chrome
- Microsoft Edge

## 环境要求

- macOS（Apple Silicon / Intel）
- Python 3.10+（推荐 3.12+）
- pyobjc

## 安装

```bash
pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz
```

## 权限配置

前往**系统设置 → 隐私与安全性 → 辅助功能**，将你使用的终端（Terminal / iTerm2）添加到允许列表。未授权时辅助功能 API 无法读取窗口标题。

## 运行

```bash
cd 桌面监控程序
python3 tracker.py
```

按 `Ctrl+C` 停止。

## 开机自启动（launchd）

将 `com.user.mactracker.plist` 复制到 `~/Library/LaunchAgents/`，然后：

```bash
launchctl load ~/Library/LaunchAgents/com.user.mactracker.plist
```

日志输出到 `tracker.log` 和 `tracker_error.log`。

## 数据库结构

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

## 查询示例

```bash
sqlite3 activity.db
```

```sql
-- 今天各应用使用时长（每条记录 = 5 秒）
SELECT app_name, COUNT(*) * 5 / 60 AS minutes
FROM activity_log
WHERE date = date('now', 'localtime')
GROUP BY app_name
ORDER BY minutes DESC;

-- 今天看了哪些视频
SELECT window_title, url, COUNT(*) * 5 / 60 AS minutes
FROM activity_log
WHERE date = date('now', 'localtime') AND activity_type = 'video'
GROUP BY url
ORDER BY minutes DESC;
```

## 文件说明

```
桌面监控程序/
├── tracker.py                    # 主程序
├── activity.db                   # 数据库（本地，不上传）
├── launch_tracker.sh             # 手动启动脚本
├── tracker.log                   # 运行日志（本地，不上传）
└── tracker_error.log             # 错误日志（本地，不上传）

~/Library/LaunchAgents/
└── com.user.mactracker.plist     # launchd 自启动配置
```
