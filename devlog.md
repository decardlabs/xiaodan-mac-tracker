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
