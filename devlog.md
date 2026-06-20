## 2026年6月14日 — 第三模块

### 完成内容
- 创建 classifier.py，实现域名缓存分类系统
- 新建 domain_categories 表（domain为主键，每个域名只分类一次）
- 导入 Safari 历史记录 140,086 条（safari_history 表）
- 分类优先级：硬编码规则 → 缓存 → DeepSeek API兜底
- 硬编码规则覆盖主要域名（bilibili、claude.ai、translate.google等18条）
- .env 文件管理 API Key，已加入 .gitignore
- 临时文件 /tmp/safari_history_copy.db 用完即删（安全修复）

### 问题与解决
- DeepSeek输出格式不稳定 → 加入 normalize_category 校验 + 重试3次逻辑
- Safari History.db 权限拒绝 → Terminal 开启完全磁盘访问权限
- 临时副本残留 /tmp → try/finally 保证异常时也清理

### 最终分类结果（activity_log）
- 娱乐/社交闲逛：1610条
- 自主学习（编程+AI工具）：2567条
- 娱乐/视频：435条
- 学校学习：315条
- 其他/待分类：47条（长尾，已缓存）

### 下一步
- 第四模块：分析引擎，每日/每周时间汇总统计

## 2026年6月15日 — 第四/五模块

### 完成内容
- 菜单栏UI全面升级（纯PyObjC，替换旧版）
- 三页面结构：首页/今日简报/目标设定
- 环形图+条形图切换（B色板：蓝/紫/青绿/灰）
- 日期翻页功能（‹/›），可查看历史某天数据
- classifier自动触发（每60条记录跑一次，只跑今天+昨天，--no-api）
- SLEEP_DETECT_THRESHOLD从15秒改为10分钟，时长显示恢复正常
- menuWillOpen_代理：每次打开菜单自动刷新数据

### 问题与解决
- tracker启动后数据不写入 → SQLite跨线程问题，加check_same_thread=False
- 状态栏时长严重少算 → SLEEP_DETECT_THRESHOLD太严格，15秒改为10分钟
- classifier模型名错误 → deepseek-v4-flash（不是deepseek-chat）
- LEAD()跨天计算错误 → 加PARTITION BY date
- anthropic.APIError懒加载NameError → 改为except Exception
- classifier并发冲突 → 加_classifier_running锁
- 午夜跨日记录不分类 → 自动触发时同时跑昨天+今天

### 当前状态
- tracker后台记录 ✅
- classifier每5分钟自动打标签 ✅  
- 菜单栏时长显示准确 ✅
- 分类数据约5分钟延迟（可接受）✅

### 下一步
- analyzer.py：日报/周报HTML
- 打包成.dmg发布
- 清理SharedState死代码

## 2026年6月15日 — 项目结构规划

### 模块文件职责

```
桌面监控程序/
├── tracker.py        # 第一~五模块：后台记录 + 菜单栏UI（主程序）
├── classifier.py     # 第三模块：活动分类（硬编码规则 + DeepSeek API缓存）
├── analyzer.py       # 第四模块：日报/周报统计，读取 activity_log 生成数据
├── web/
│   ├── app.py        # 第六模块：Flask后端，提供Web看板API和页面路由
│   ├── templates/    # Jinja2 HTML模板（日报、周报、看板页面）
│   └── static/       # CSS / JavaScript / 图表库资源
└── reports/          # analyzer.py 生成的静态HTML报告存放目录
```

### 说明
- 从本模块起，新功能单独建文件，tracker.py 不再增长
- analyzer.py 只读数据库，不写入，可独立运行
- web/ 模块可选启动，不影响tracker后台记录

---

## 2026年6月17~18日 — v0.6 大版本升级

### 新增文件

**ui.py — 菜单栏UI独立模块**
- 从 tracker.py 拆分出全部 AppKit UI 代码，tracker.py 回归纯后台角色
- 新增 WellnessCardView：每次打开菜单随机展示一条健康活动建议（伸展/冥想/喝水等）
- 新增 GoalRowView：学习目标/娱乐上限进度条，实时对比今日数据
- 监听 NSDistributedNotificationCenter 的 `XiaoDanClassifierDone` 通知，分类完成后自动刷新 UI，无需轮询
- 集成 analyzer 日报：菜单栏「今日简报」页展示 LLM 生成的一段话总结
- 集成 report_window：菜单栏「周报 / 月报」按钮打开独立报告窗口

**report_window.py — 周报/月报独立窗口**
- 基于 WKWebView 渲染完整 HTML 报告，支持周报、月报双视图切换
- 周/月统计：各分类时长、环比变化（↑↓%）、占比
- 周感想 / 月感想：可在窗口内直接编辑并保存到数据库
- 书单（book_notes）管理：添加书名/作者/日期/标签/笔记，支持删除
- 历史翻页：‹/› 切换历史任意一周或一月

**wellness.py + wellness_activities.json — 健康提醒**
- 从 JSON 文件加载活动数据，分「运动」「眼睛」「喝水」「呼吸」「站立」五大类
- `get_random_activity(category)` 随机返回一条建议，供 WellnessCardView 调用

**analyzer.py — LLM 日报/周报引擎（完整实现）**
- 原文件为空，本版本完整实现
- `generate_report(date_str)`：拉取当日 activity_log，用 LEAD() 算法聚合时长，调用 DeepSeek 生成80字以内自然语言日报，写入 daily_reports 表
- `get_week_stats / get_month_stats`：按周/月聚合各分类时长，计算 top-app 列表
- 书单 CRUD：`get_book_notes / save_book_note / update_book_note / delete_book_note`
- 感想读写：`get_reflection / save_reflection`（周），`get_monthly_reflection / save_monthly_reflection`（月）
- 新建数据库表：`daily_reports`、`weekly_reflections`、`monthly_summaries`、`monthly_reflections`、`book_notes`

### 已修改文件

**classifier.py — 分类规则增强**
- 新增硬编码域名：Trading212、Outlook（cloud.microsoft / live.com / office.com）
- `get_hardcoded_category()` 新增 `window_title` 和 `url` 参数，支持标题级匹配：
  - `_DISSERTATION_KEYWORDS`：检测论文相关标题（dissertation / 毕业论文 / thesis 等）→ `学校学习/论文相关`
  - `_AI_TOOL_TITLE_MARKERS`：检测 Claude / ChatGPT / Gemini 标题 → `自主学习/AI工具`
  - `_BILIBILI_TITLE_MARKERS`：检测哔哩哔哩标题 → `娱乐/视频`
  - Safari 无 URL 无标题 → `其他/系统后台`
  - Safari 收藏夹页 / 起始页 → `其他/系统后台`
- 分类完成后通过 NSDistributedNotificationCenter 发布 `XiaoDanClassifierDone` 通知，触发 UI 刷新
- Safari 历史导入改用 `tempfile.NamedTemporaryFile`，不再写死 `/tmp` 路径

**tracker.py — 后台精简 + 稳定性**
- 移除全部 AppKit UI 代码（DonutChartView、BarChartView、菜单构建等），UI 职责完全交给 ui.py
- 新增 `_acquire_lock()`：用 `fcntl.flock` 写锁文件 `xiaodian.lock`，防止多实例同时启动

**launch_tracker.sh — 可移植性修复**
- 启动目录改为 `$(dirname "$0")`，不再写死用户路径，方便迁移/重装
- Python 路径改为 `$(python3 -c 'import sys; print(sys.executable)')`，自动适配当前 venv
- 日志输出重定向到 `~/Library/Logs/XiaoDan/tracker.log`（目录自动创建）

### 当前状态
- tracker 后台记录 ✅
- classifier 每5分钟自动打标签 + 完成通知 ✅
- 菜单栏 UI（图表 + 目标 + 健康提醒 + 日报）✅
- 周报/月报独立窗口（WKWebView）✅
- 书单管理 ✅
- 周/月感想编辑 ✅
- 防重复启动锁 ✅

### 下一步
- 打包成 .app / .dmg 发布
- 数据导出功能（CSV / JSON）

---

## 2026年6月20日 — v0.78

### 新增功能

**起身提醒自定义图标**
- 新增 `standup_icon.png`（400×145px，emoji 合成图，替换 NSAlert 系统默认图标）
- `ui.py` 的 `showStandupAlert_` 用 `NSImage.alloc().initWithContentsOfFile_()` 加载图片，调用 `alert.setIcon_()` 替换系统默认图标
- 资源路径遵循统一约定：`os.environ["RESOURCEPATH"]`（打包后）/ `os.path.dirname(__file__)`（开发环境），与 `wellness.py`、`report_window.py` 保持一致
- `setup.py` DATA_FILES 新增 `standup_icon.png`，确保打包时图标随 `.app` 一同打包
- 图标加载失败时有 try/except 保护，回退至系统默认图标

### 当前状态

- tracker 后台记录 ✅
- classifier 自动分类（同进程调用，打包友好）✅
- 菜单栏 UI（图表 + 目标 + 健康提醒 + 日报）✅
- 周报/月报（标签栏导航 + 设置页）✅
- 起身提醒（自定义图标弹窗）✅
- 设置持久化（settings.json）✅
- py2app 打包（build.sh 一键打包 + 签名）✅

---

## 2026年6月20日 — v0.77

### 修复与改进

**打包修复（H1–H5 + M1 + M3）**
- **H1 Chart.js 路径**：`report_window.py` 改用 `sys.frozen` / `RESOURCEPATH` 模式定位 `chart.umd.min.js`，与 `wellness.py` 保持一致
- **H2 packages 补全**：`setup.py` packages 列表补全所有自定义模块（`analyzer`、`classifier`、`settings`、`standup_reminder`、`wellness`、`report_window`、`ui`）
- **H3 plist 路径说明**：`com.user.mactracker.plist` 加入 XML 注释，说明为开发机专用；launchd 不展开 `${HOME}`，路径须写绝对路径
- **H4 classifier 同进程调用**：`tracker.py` 不再用 `subprocess.run` 调用 `classifier.py`（`.app` 包内 `.py` 路径失效），改为 `import classifier as _classifier_module` 同进程调用；`classifier.py` 新增 `run_classification(date_str, *, use_api, recheck_other)` 入口函数
- **H5 孤立 activity.db**：删除项目根目录遗留的 672 MB `activity.db`；移除 `tracker.py` 的死代码迁移块和 `import shutil`
- **M1 wellness 路径**：`wellness.py` RESOURCEPATH 模式已正确，补全 `setup.py` 注释说明
- **M3 参数范围校验**：`report_window.py` 新增 `_valid_int(key, lo, hi)` 函数，`xd://` URL 参数（year/week/month）均加范围检查（2020–2100 / 1–53 / 1–12），非法值打印日志并忽略

**图标更新**
- 从 `logo.iconset/` 重新编译 `XiaoDan.icns`（新 logo 设计）；删除旧 `xiaodan_icon.icns`
- `setup.py` iconfile 改为 `XiaoDan.icns`

**打包自动化**
- 新增 `build.sh`：`rm -rf build dist` → `python3 setup.py py2app` → `codesign --force --deep --sign -`
- Apple Silicon 上 py2app 构建成功但签名无效（`SIGKILL Code Signature Invalid`）；`build.sh` 末尾自动 ad-hoc 签名为必须步骤

---

## 2026年6月20日 — v0.76

### 新增功能

**Chart.js 改为本地内嵌**
- `report_window.py` 不再从 CDN 加载 Chart.js（WKWebView 沙箱在部分网络环境下阻断外网请求）
- 改为读取本地 `chart.umd.min.js` 并直接内嵌到 HTML `<script>` 标签
- `setup.py` DATA_FILES 新增 `chart.umd.min.js`，打包时复制到 `Contents/Resources/`
- 资源路径同样采用 `sys.frozen` / `RESOURCEPATH` 模式，打包和开发环境均可正常加载

---

## 2026年6月20日 — v0.75

### 新增功能

**AI 简报开关 + 错误状态区分**
- `settings.py` 新增 `api_enabled` 字段（默认 `True`）
- `analyzer.py`：模块顶层 `import anthropic`；新增 `APIDisabledError` / `APIKeyMissingError` 异常类；`generate_report` / `generate_monthly_report` / `generate_monthly_summary` 在调用 API 前先检查 `api_enabled` 和 `ANTHROPIC_API_KEY`
- `ui.py`：三处 `except Exception: pass` 改为捕获具体异常并记录 `_report_last_error`；`_build_report_view` 按状态分别显示「AI 简报已关闭」/「未设置 API Key」/「暂时无法获取简报」
- `report_window.py`：侧边栏底部新增「设置」入口；设置页含「启用 AI 简报」开关，通过 `xd://save_api_enabled` 桥接写入 `settings.json`

---

## 2026年6月20日 — v0.74

### 修复与改进

**日志路径迁移**
- `com.user.mactracker.plist` 的 `StandardOutPath` / `StandardErrorPath` 改指向 `~/Library/Logs/XiaoDan/`，不再依赖项目目录路径（为打包 .app 做准备）
- `launch_tracker.sh` 添加注释，说明脚本为手动调试备用，日常由 launchd 管理

---

## 2026年6月19日 — v0.73

### 新增功能

**起身提醒（standup_reminder.py）**
- 新建 `standup_reminder.py`，`StandupTimer` 类 + 模块级单例 `timer`
  - `configure(enabled, interval_minutes)`：由 UI 层调用，设置开关和间隔
  - `add_active_seconds(seconds, activity_type)`：由 tracker 每5秒调用，`idle`/`dock` 类型自动跳过
  - 累计活跃时长达到间隔后重置计数，跨线程调 `performSelectorOnMainThread_` → `showStandupAlert:`
- `ui.py` 新增 `showStandupAlert_` ObjC 方法：弹出 NSAlert，显示「起来走动一下吧 / 你已经连续使用电脑 N 分钟了」，单按钮「好」
- `tracker.py` 在每次有效活动帧 `save_to_db` 后调用 `timer.add_active_seconds(POLL_INTERVAL, main["type"])`

**设置持久化（settings.py）**
- 新建 `settings.py`：`load_settings()` / `save_settings()`，JSON 存储到 `~/Library/Application Support/XiaoDan/settings.json`
- 持久化字段：`chart_mode`、`wellness_enabled`、`report_time`、`standup_reminder_enabled`、`standup_interval_minutes`
- 启动时自动从 JSON 加载，任意设置变更后立即写盘

**设置菜单新增两行（ui.py）**
- "显示"分组末尾新增：
  - 起身提醒 — NSButton checkbox（`toggleStandup:`）
  - 提醒间隔 — NSTextField 数字输入框 + "分钟"标签，范围 5–180，tag=2 区分 `controlTextDidEndEditing_`

### 当前状态

- tracker 后台记录 ✅
- classifier 自动分类 + 完成通知 ✅
- 菜单栏 UI（图表 + 目标 + 健康提醒 + 日报）✅
- 周报/月报（标签栏导航）✅
  - 月报总览：按周柱状图 + 分类卡片 + 时段热力图 + 月感想
  - 月报时间排行：平滑折线图（右侧按钮切换）+ 二级分类进度条 + AI 月报卡片
- 读书笔记 CRUD ✅
- 设置持久化 ✅
- 起身提醒（NSAlert 主线程弹窗）✅

### 下一步

- Apple Developer 证书正式签名，支持分发
- 数据导出功能（CSV / JSON）

---

## 2026年6月19日 — v0.71

### 新增功能

**月报总览：时段热力图（全量替换原 GitHub 风格热力图）**
- 新增数据函数 `get_month_daily_period_stats(year, month)`（`analyzer.py`）：
  - 返回每天早（6–12）/ 午（12–18）/ 晚（18–次日6）三时段的学习时长
  - 凌晨 0–6 点的记录归入前一天"晚"（含跨月处理，多取下月第1天数据）
  - 复用 `_SLEEP_DETECT_THRESHOLD` 睡眠过滤与 `_FILTER_APPS` 应用过滤逻辑
  - 全0月份有除零保护（`max_val = max(...) if any(s>0) else 1`）
- 布局：横向 3行 × N列（早/午/晚 × 当月天数），适配所有月份（28/29/30/31天）
- 颜色：蓝色系 6档动态色阶，封顶色 `#5B8DEF`（与"学校学习"色一致）
  `#F1EFE8` → `#EDF1FC` → `#D6E2F8` → `#B0C8F2` → `#84A8EB` → `#5B8DEF`
  阈值按当月最大值动态分5等分，不写死
- 格子：15×16px，圆角3px，间距2px；周末日期数字紫色高亮
- Tooltip：悬停显示"6月13日（周六）午 · 1h 43m"，复用现有 `data-htip` + IIFE addEventListener 模式
- 宽度验证：31天最宽 551px，在580px内容区内，不产生横向滚动

**月报总览：柱状图与热力图间距加倍**
- 热力图容器 `margin-top`：18px → 36px

**月报时间排行：折线图优化**
- 曲线平滑：`tension` 0.3 → 0.4
- 数据点默认隐藏，hover时显示：`pointRadius:0`、`pointHoverRadius:5`、`pointHitRadius:10`
- Y 轴动态上限：`suggestedMax` 按当月最大值向上取整到下一小时 +2h，防峰值截断
- 分类切换按钮移至图表右侧竖排（flex 布局），减轻左侧内容密度

### 当前状态

- tracker 后台记录 ✅
- classifier 自动分类 + 完成通知 ✅
- 菜单栏 UI（图表 + 目标 + 健康提醒 + 日报）✅
- 周报/月报（标签栏导航）✅
  - 月报总览：按周柱状图 + 分类卡片 + 时段热力图 + 月感想
  - 月报时间排行：平滑折线图（右侧按钮切换）+ 二级分类进度条
- 读书笔记 CRUD ✅
- 新 Logo 图标 ✅
- py2app 打包配置 ✅（本机可运行，ad-hoc 签名，不可分发）

### 下一步

- 设置页面（设置持久化到本地 JSON）
- Apple Developer 证书正式签名，支持分发
- 数据导出功能（CSV / JSON）

---

## 2026年6月19日 — v0.7

### 新增功能

**读书笔记增强（v0.61 → v0.62 补丁）**
- 修复读书笔记删除按钮无反应的 bug（objc 方法未正确注册）
- 确认弹窗从原生 `NSAlert` 改为自定义 JS 内联样式弹窗（`xdConfirm`），
  解决原生弹窗在 WKWebView 里样式异常的问题

**月报重构**
- 删除"页面使用时间"功能（`_build_top_pages_content` 整函数移除）
- 月报从"底部链接跳转"改为标签栏结构，复用周报的 `.week-tabs` / `.tab` CSS
  - 标签："总览" / "时间排行"，点击切换，不再全页替换
- 新增 `_build_month_header`：含月份导航箭头（‹/›）+ 标签栏
- 新增 `_month_prev` / `_month_next` 模块级辅助函数
- "时间排行"页顶部加入环形图（doughnut，420×420，cutout 60%，outsideLabels 外部标签），
  展示当月各一级分类占比，与周报"时间明细"视觉一致

**新 Logo 与图标打包**
- 设计并生成 `xiaodan_icon.icns`，覆盖10种尺寸（16×16 至 512×512@2x）
- 使用 `iconutil` 从 PNG iconset 打包为 .icns，Quick Look 验证通过

**py2app 打包配置**
- 新增 `setup.py`，配置 py2app 打包参数：
  - `iconfile: XiaoDan.icns`（由 `logo.iconset` 编译生成），Info.plist 中 `CFBundleIconFile`、`LSUIElement: True`
  - `excludes: ['tkinter']`：排除 Tk/Tcl 框架，解决打包时 ad-hoc 签名因 `libtkstub.a` 失败的问题
- 修复 `wellness.py` 资源路径：
  - 打包后 `wellness.py` 在 `Contents/Resources/lib/python3.14/`，而 JSON 在 `Contents/Resources/`
  - 改用 `os.environ["RESOURCEPATH"]`（py2app 启动时注入）定位资源根目录，开发环境退回 `__file__` 相对路径
- **打包命令**：`./build.sh`（不要直接跑 `python3 setup.py py2app`）
  - 脚本内部：清理旧产物 → py2app → codesign ad-hoc 签名（Apple Silicon 必须步骤，否则启动时被系统 kill）

### 问题与解决

- py2app 签名失败（`RuntimeError: Cannot sign bundle`）→ `excludes: ['tkinter']` 排除无法签名的 Tk 静态库
- wellness JSON 路径在打包后失效 → 改用 `RESOURCEPATH` 环境变量，实测验证路径正确
- LaunchAgent `KeepAlive: true` 导致 tracker 无法临时停止测试 → 移走 plist 再测，测后还原
- Apple Silicon 打包后 app 启动被 kill（`SIGKILL Code Signature Invalid`）→ `build.sh` 末尾自动执行 `codesign --force --deep --sign -`

### 当前状态

- tracker 后台记录 ✅
- classifier 自动分类 + 完成通知 ✅
- 菜单栏 UI（图表 + 目标 + 健康提醒 + 日报）✅
- 周报/月报（标签栏导航，环形图 + 进度条列表）✅
- 读书笔记 CRUD ✅
- 新 Logo 图标 ✅
- py2app 打包配置 ✅（本机可运行，ad-hoc 签名，不可分发）

### 下一步

- Apple Developer 证书正式签名，支持分发
- 数据导出功能（CSV / JSON）
- 月报 reflection 存储迁移（当前写 monthly_summaries 表，可考虑统一）
