#!/usr/bin/env bash
# build.sh — 把小蛋打包成可分发的 .app + .dmg
#
# 用法：
#     ./build.sh              # 打包并生成 .dmg
#     ./build.sh --no-dmg     # 只打包 .app
#
# 产物：
#     dist/XiaoDan.app        # 完整应用包
#     dist/XiaoDan-v0.3.0.dmg # 可分发的磁盘映像
#
# 注意：本脚本不进行 Apple 公证（需要 $99/年开发者账号）。
# 用户首次启动需要右键"打开"绕过 Gatekeeper。

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="XiaoDan"
BUNDLE_ID="com.decard.xiaodan"
VERSION="${VERSION:-0.3.0}"
DIST_DIR="$PROJECT_ROOT/dist"
APP_BUNDLE="$DIST_DIR/${APP_NAME}.app"
CONTENTS="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RES_DIR="$CONTENTS/Resources"

echo "============================================================"
echo "  小蛋打包脚本 v$VERSION"
echo "============================================================"
echo "项目根目录: $PROJECT_ROOT"
echo "输出: $APP_BUNDLE"
echo ""

# ── 1. 清理旧产物 ─────────────────────────────────────────────────────────────
rm -rf "$APP_BUNDLE"
mkdir -p "$DIST_DIR" "$MACOS_DIR" "$RES_DIR"

# ── 2. 复制核心文件 ───────────────────────────────────────────────────────────
echo "📦 复制核心文件…"
cp "$PROJECT_ROOT/tracker.py" "$RES_DIR/tracker.py"
cp "$PROJECT_ROOT/install_launchagent.py" "$RES_DIR/install_launchagent.py"

# 复制 venv（用符号链接以减小体积；如需自包含可改为 cp -RL）
if [ -d "$PROJECT_ROOT/venv" ]; then
    echo "🔗 链接 venv…"
    ln -s "$PROJECT_ROOT/venv" "$RES_DIR/venv"
else
    echo "❌ 错误：未找到 venv 目录"
    exit 1
fi

# 复制图标（icns + 状态栏用的 png）
if [ -f "$PROJECT_ROOT/build_assets/icon.icns" ]; then
    cp "$PROJECT_ROOT/build_assets/icon.icns" "$RES_DIR/icon.icns"
fi
if [ -f "$PROJECT_ROOT/icon.png" ]; then
    cp "$PROJECT_ROOT/icon.png" "$RES_DIR/icon.png"
fi
if [ -f "$PROJECT_ROOT/icon@2x.png" ]; then
    cp "$PROJECT_ROOT/icon@2x.png" "$RES_DIR/icon@2x.png"
fi

# 复制 README
if [ -f "$PROJECT_ROOT/README.md" ]; then
    cp "$PROJECT_ROOT/README.md" "$RES_DIR/README.md"
fi

# ── 3. 写 Info.plist ──────────────────────────────────────────────────────────
echo "📝 生成 Info.plist…"
cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>小蛋</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleIconName</key>
    <string>icon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppleEventsUsageDescription</key>
    <string>小蛋需要访问窗口标题来追踪应用使用时长。</string>
    <key>NSHumanReadableCopyright</key>
    <string>MIT License</string>
    <key>CFBundleDocumentTypes</key>
    <array/>
</dict>
</plist>
EOF

# ── 4. 写启动器 launcher ─────────────────────────────────────────────────────
echo "🚀 生成启动器…"
cat > "$MACOS_DIR/launcher" <<'LAUNCHER_EOF'
#!/bin/bash
# XiaoDan.app 启动器 —— 激活 Python venv 并跑 tracker.py
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$DIR"

# 把 venv 路径加到 PATH 让 rumps/pyobjc 能找到
export PATH="$DIR/venv/bin:$PATH"
export PYTHONPATH="$DIR/venv/lib/python3.13/site-packages:$PYTHONPATH"

# 启动小蛋
exec "$DIR/venv/bin/python3" -u "$DIR/tracker.py" "$@"
LAUNCHER_EOF
chmod +x "$MACOS_DIR/launcher"

# ── 5. 写卸载脚本（放在 Resources 里） ────────────────────────────────────────
cat > "$RES_DIR/uninstall.sh" <<'UNINSTALL_EOF'
#!/bin/bash
# XiaoDan 卸载脚本
echo "正在卸载小蛋…"
# 卸载 LaunchAgent
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.decard.xiaodan.plist"
if [ -f "$LAUNCH_AGENT" ]; then
    launchctl unload "$LAUNCH_AGENT" 2>/dev/null
    rm -f "$LAUNCH_AGENT"
    echo "✅ 已卸载 LaunchAgent"
fi

# 删除应用本体
APP="/Applications/XiaoDan.app"
if [ -d "$APP" ]; then
    rm -rf "$APP"
    echo "✅ 已删除 /Applications/XiaoDan.app"
fi

# 询问是否删除数据
read -p "是否同时删除使用数据（~/Library/Application Support/XiaoDan）？[y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
    rm -rf "$HOME/Library/Application Support/XiaoDan"
    echo "✅ 已删除数据"
fi

echo "卸载完成。"
UNINSTALL_EOF
chmod +x "$RES_DIR/uninstall.sh"

# ── 6. Ad-hoc 签名（避免部分 macOS 警告） ────────────────────────────────────
echo "✍️  Ad-hoc 签名…"
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null || echo "（codesign 失败，可忽略）"

# ── 7. 生成 .dmg ─────────────────────────────────────────────────────────────
if [[ "$1" != "--no-dmg" ]]; then
    echo "💿 生成 .dmg…"
    DMG_PATH="$DIST_DIR/${APP_NAME}-v${VERSION}.dmg"
    rm -f "$DMG_PATH"

    # 创建临时挂载点
    TMP_DMG_DIR=$(mktemp -d)
    cp -R "$APP_BUNDLE" "$TMP_DMG_DIR/"
    # 拖入 Applications 的快捷方式
    ln -s /Applications "$TMP_DMG_DIR/Applications"

    # 创建只读 DMG
    hdiutil create -volname "${APP_NAME} ${VERSION}" \
        -srcfolder "$TMP_DMG_DIR" \
        -ov -format UDZO \
        "$DMG_PATH" >/dev/null
    rm -rf "$TMP_DMG_DIR"

    echo ""
    echo "============================================================"
    echo "✅ 打包完成！"
    echo ""
    echo "   .app:  $APP_BUNDLE"
    echo "   .dmg:  $DMG_PATH"
    echo ""
    echo "下一步："
    echo "   1. 双击 $DMG_PATH"
    echo "   2. 把 XiaoDan 拖入 Applications 文件夹"
    echo "   3. 在 Applications 中右键 XiaoDan → 打开（首次）"
    echo "   4. 在菜单栏点蛋图标 → 安装辅助功能权限"
    echo "   5. （可选）菜单栏点蛋图标 → 安装 LaunchAgent（开机自启）"
    echo "============================================================"
else
    echo ""
    echo "============================================================"
    echo "✅ 打包完成（仅 .app）"
    echo "   $APP_BUNDLE"
    echo "============================================================"
fi
