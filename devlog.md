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
