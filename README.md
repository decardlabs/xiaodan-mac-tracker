# 🥚 小蛋 — Mac 使用时间监控 v0.78

本地运行的 macOS 使用时间追踪工具，每 5 秒记录一次你在做什么，数据存在本机 SQLite 数据库，不上传任何信息。

## 功能

### 后台记录
- **主活动检测**：通过鼠标位置识别当前操作的窗口和应用
- **浏览器深度识别**：获取当前标签页 URL 和标题，自动区分「看视频」和「普通浏览」
- **背景音乐检测**：识别 Apple Music、Spotify，以及浏览器后台播放的视频/音乐页面
- **窗口标题获取**：通过 macOS 辅助功能 API 读取精确窗口标题
- **自动分类**：每 5 分钟对新记录打标签（自主学习 / 学校学习 / 娱乐 / 其他），支持域名规则 + 标题规则 + LLM 兜底
- **AI 分类开关**：可在报告窗口设置页关闭 LLM 调用（不影响规则分类），断网或节省 API 配额时使用

### 菜单栏 UI
- 状态栏实时显示今日总时长（或 🥚 待机图标）
- 点击展开菜单：今日各分类时长卡片 + 堆叠条形图
- 学习目标 / 娱乐上限进度条（GoalRowView）
- 健康提醒卡片：随机显示伸展 / 喝水 / 眼睛休息等建议
- 日期翻页（‹/›）查看历史某天数据
- **起身提醒**：累计活跃使用 N 分钟后弹出 NSAlert 原生弹窗（含自定义图标），间隔可在设置中调整
- **设置持久化**：图表类型、健康提醒开关、简报时间、起身提醒设置均写入本地 JSON，重启后保留

### 周报 / 月报窗口
- 基于 WKWebView 渲染，侧边栏导航历史周 / 月
- **设置页**：侧边栏底部入口，可切换「启用 AI 简报」开关（持久化到 settings.json）
- **周报**：总览（堆叠柱状图 + 分类卡片）/ 时间明细（环形图 + 二级分类进度条）/ 读书笔记
- **月报**：
  - 总览：按周聚合柱状图 + 分类卡片 + **时段热力图**（早/午/晚 × 当月每天，蓝色系动态色阶）
  - 时间排行：平滑折线图（右侧胶囊按钮切换分类）+ 二级分类进度条
- 周感想 / 月感想：窗口内直接编辑并保存
- Chart.js 本地内嵌，无网络依赖

### 读书笔记
- 记录书名 / 作者 / 阅读日期 / 标签 / 笔记正文
- 支持新增 / 编辑 / 删除，自定义确认弹窗防误删

## 支持的浏览器

- Safari
- Google Chrome
- Microsoft Edge

## 环境要求

- macOS（Apple Silicon / Intel）
- Python 3.10+（推荐 3.12+）
- pyobjc

## 安装依赖

```bash
pip install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices pyobjc-framework-Quartz
```

LLM 分类功能需要设置环境变量（可选，不设置则跳过 AI 分类）：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

也可以在报告窗口的设置页直接关闭「启用 AI 简报」，无需修改环境变量。

## 权限配置

前往**系统设置 → 隐私与安全性 → 辅助功能**，将你使用的终端（Terminal / iTerm2）添加到允许列表。未授权时辅助功能 API 无法读取窗口标题。

## 运行

```bash
cd 桌面监控程序
python3 tracker.py
```

## 开机自启动（launchd）

将 `com.user.mactracker.plist` 复制到 `~/Library/LaunchAgents/`，然后：

```bash
launchctl load ~/Library/LaunchAgents/com.user.mactracker.plist
```

> 注意：plist 中路径为开发机绝对路径（launchd 不展开 `${HOME}`），迁移到新机器需手动修改。

日志输出到 `~/Library/Logs/XiaoDan/tracker.log`。

## 打包为 .app（可选）

```bash
pip install py2app
./build.sh
# 产物在 dist/小蛋.app
```

`build.sh` 自动执行：清理旧产物 → py2app 打包 → ad-hoc 代码签名（Apple Silicon 上的必须步骤，否则启动时被系统 kill）。

> 注意：当前为 ad-hoc 签名，仅供本机使用，不可分发。

## 数据库结构

```sql
-- 主记录表
CREATE TABLE activity_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT,
    date          TEXT,
    app_name      TEXT,
    window_title  TEXT,
    activity_type TEXT,   -- "app" | "browser" | "video" | "idle" | "dock"
    url           TEXT,
    bg_music      TEXT,
    category      TEXT,   -- "自主学习/编程" 等二级分类
    classified_at TEXT
);

-- 其他表
-- domain_categories   域名分类缓存
-- daily_reports       LLM 日报
-- book_notes          读书笔记
-- weekly_reflections  周感想
-- monthly_reflections 月感想
```

## 文件说明

```
桌面监控程序/
├── tracker.py              # 后台记录主程序
├── ui.py                   # 菜单栏 UI（AppKit）
├── report_window.py        # 周报/月报/设置窗口（WKWebView）
├── classifier.py           # 活动分类引擎
├── analyzer.py             # 数据聚合 + LLM 日报/周报/月报
├── wellness.py             # 健康提醒数据
├── settings.py             # 设置持久化（JSON 读写）
├── standup_reminder.py     # 起身提醒计时器
├── wellness_activities.json
├── chart.umd.min.js        # Chart.js 本地副本（WKWebView 离线渲染）
├── standup_icon.png        # 起身提醒弹窗自定义图标
├── XiaoDan.icns            # 应用图标（由 logo.iconset 编译）
├── logo.iconset/           # 图标源文件（PNG，10 种尺寸）
├── setup.py                # py2app 打包配置
├── build.sh                # 打包脚本（py2app + codesign，推荐使用）
└── launch_tracker.sh       # 手动启动脚本（调试用）

~/Library/LaunchAgents/
└── com.user.mactracker.plist   # launchd 自启动配置（开发机专用）

~/Library/Application Support/XiaoDan/
├── activity.db             # SQLite 数据库（本地，不上传）
└── settings.json           # 用户设置持久化

~/Library/Logs/XiaoDan/
└── tracker.log             # 运行日志（launchd 重定向）
```
