# 变更日志 (CHANGELOG)

## v0.4.0 (2026-06-14) — py2app 原生打包 + 纯 PyObjC 重写

### 🐛 重大修复
- **状态栏图标在 macOS 26 上不显示**
  - 根因：`rumps` 框架在 `py2app` 打包后的 bundle 中无法向 macOS 26 WindowServer 注册 NSStatusItem
  - 表现：进程能拉起、`System Events` 看不到菜单栏 item，但应用逻辑正常
  - 修复：完全重写 `tracker.py` UI 层为**纯 PyObjC**（`NSStatusBar` / `NSStatusItem` / `NSMenu`），移除 `rumps` 依赖

### 🔨 打包改造
- 新增 `setup.py`：`py2app` 0.28 原生打包配置
- `build.sh` 默认走 `py2app`（生成 Mach-O arm64 bundle + 自包含 `Python.framework`）
- 新增 `--no-dmg` / `--legacy` 参数：
  - `--no-dmg`：只生成 `.app`
  - `--legacy`：回退到手搓 `.app` 模式（不推荐，仅作兼容）
- `Info.plist` 由 `setup.py` 生成，`LSUIElement=true`、`NSHighResolutionCapable=true`

### 🎨 视觉调整
- 状态栏图标改为**模板模式**（黑+透明，alpha 通道），系统自动适配明暗主题
- 图标更新为新版小蛋涂鸦（手绘风格）

### 🛠 配套修复
- `install_launchagent.py`：
  - 修复 `launcher.sh` 引用问题（py2app 打包后没有这个文件）
  - 改用 `open -a /Applications/XiaoDan.app` 拉起 .app bundle
- `README.md` 同步更新版本号、打包说明、依赖列表（移除 `rumps`）

### 📊 体积变化
- v0.3.0 手搓 `.app`：1.8 MB（依赖系统 Python + 完整 venv 符号链接）
- v0.4.0 py2app `.app`：33 MB（自包含 Python.framework，独立运行）
- v0.4.0 `.dmg`：17 MB（zlib 压缩后）

### 📦 安装要求变化
| 维度 | v0.3.0 手搓 .app | v0.4.0 py2app |
|------|------------------|---------------|
| 用户 Python 环境 | 必须有匹配路径的 venv | ❌ 不需要 |
| 状态栏图标 (macOS 26) | ❌ 不显示 | ✅ 正常 |
| 系统注册 | ❌ sealed resource invalid | ✅ 完整 |
| 自包含 | ❌ 散落文件 | ✅ 单 bundle |

---

## v0.3.0 (2025-XX-XX) — 一键打包 .app + .dmg
- 新增 `build.sh` 一键脚本
- 新增 LaunchAgent 开机自启
- 辅助功能权限引导菜单
- 新版手绘小蛋图标
- 数据库迁移到 `~/Library/Application Support/`

## v0.2.0 — 状态栏 + 自定义图标
- 状态栏 + 小蛋图标 + 30秒刷新
- 今日 / 本周 Top 3 菜单
- 弹窗统计视图

## v0.1.0 — 初始存档
- 主活动检测
- 浏览器深度识别
- 背景音乐检测
- 本地 SQLite 存储
