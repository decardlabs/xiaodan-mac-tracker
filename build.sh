#!/usr/bin/env bash
# build.sh — 把小蛋打包成可分发的 .app + .dmg
#
# 用法：
#     ./build.sh              # py2app 打包并生成 .dmg（默认）
#     ./build.sh --no-dmg     # 只打包 .app（不生成 .dmg）
#     ./build.sh --legacy     # 手搓 .app 模式（旧版 shell launcher，不推荐）
#
# 产物：
#     dist/XiaoDan.app        # 完整应用包
#     dist/XiaoDan-vX.Y.Z.dmg # 可分发的磁盘映像
#
# 注意：本脚本不进行 Apple 公证（需要 $99/年开发者账号）。
# 用户首次启动需要右键"打开"绕过 Gatekeeper。
#
# ─── py2app vs 手搓 .app 的区别（用户视角） ─────────────────────────────
#
# py2app（默认，推荐）：
#   ✅ 真正的 Mach-O arm64 原生二进制（不是 shell 脚本）
#   ✅ 状态栏图标在 macOS 26 (Tahoe) 上**能正常显示**（手搓版 rumps 失败）
#   ✅ 进程能被 System Events / WindowServer 正确注册
#   ❌ 体积大：~33MB（自带 Python.framework）
#   ❌ 启动慢：~0.5-1s（启动解释器）
#   ❌ 首次 py2app build 慢：30-60s
#
# 手搓 .app（旧版）：
#   ✅ 体积小：~1.8MB（只链接 venv）
#   ✅ 启动快：~0.2s
#   ❌ 状态栏图标在 macOS 26 上**不显示**（rumps 注册失败）
#   ❌ spctl 报 "sealed resource is missing or invalid"
#   ❌ 需要自带 venv 符号链接，安装时要求 venv 路径一致

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="XiaoDan"
BUNDLE_ID="com.decard.xiaodan"
VERSION="${VERSION:-0.4.0}"
DIST_DIR="$PROJECT_ROOT/dist"
APP_BUNDLE="$DIST_DIR/${APP_NAME}.app"
DMG_PATH="$DIST_DIR/${APP_NAME}-v${VERSION}.dmg"

# 默认用 py2app
BUILD_MODE="py2app"
BUILD_APP_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --no-dmg) BUILD_APP_ONLY=true ;;
        --py2app) BUILD_MODE="py2app" ;;
        --legacy) BUILD_MODE="legacy" ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

echo "============================================================"
echo "  小蛋打包脚本 v$VERSION"
echo "  模式: $BUILD_MODE"
echo "============================================================"
echo "项目根目录: $PROJECT_ROOT"
echo "输出: $APP_BUNDLE"
echo ""

# ── 清理旧产物 ─────────────────────────────────────────────────────────────
rm -rf "$APP_BUNDLE"
rm -f "$DMG_PATH"
mkdir -p "$DIST_DIR"

# ── 1. 准备状态栏 PNG 图标（py2app 不会自动 resize） ──────────────────────
echo "🎨 准备状态栏图标…"
# 用 venv 下的 Python（pillow 装在 venv 里，系统/managed Python 没有）
PIL_PY="$PROJECT_ROOT/venv/bin/python3"
if [ ! -x "$PIL_PY" ]; then
    PIL_PY="python3"
fi
if [ -f "$PROJECT_ROOT/build_assets/source_icon_1024.png" ]; then
    "$PIL_PY" -c "
from PIL import Image
src = Image.open('$PROJECT_ROOT/build_assets/source_icon_1024.png').convert('RGBA')
for size, name in [(18, 'icon.png'), (36, 'icon@2x.png')]:
    img = src.resize((size, size), Image.LANCZOS)
    img.save('$PROJECT_ROOT/' + name, 'PNG', optimize=True)
print('✅ 状态栏图标就绪: icon.png (18x18) + icon@2x.png (36x36)')
"
elif [ ! -f "$PROJECT_ROOT/icon.png" ]; then
    echo "❌ 错误：未找到 icon.png 或 build_assets/source_icon_1024.png"
    exit 1
fi

# ── 2. 调用 py2app 或手搓打包 ────────────────────────────────────────────
if [ "$BUILD_MODE" = "py2app" ]; then
    echo "📦 调用 py2app…"
    if [ ! -d "$PROJECT_ROOT/venv" ]; then
        echo "❌ 错误：未找到 venv，请先创建虚拟环境：python3 -m venv venv && venv/bin/pip install -r requirements.txt"
        exit 1
    fi
    "$PROJECT_ROOT/venv/bin/python3" setup.py py2app 2>&1 | tail -20
else
    # 手搓 .app 模式（旧版逻辑，保留以备回退）
    echo "📦 手搓 .app 模式…"
    CONTENTS="$APP_BUNDLE/Contents"
    MACOS_DIR="$CONTENTS/MacOS"
    RES_DIR="$CONTENTS/Resources"
    mkdir -p "$MACOS_DIR" "$RES_DIR"

    cp "$PROJECT_ROOT/tracker.py" "$RES_DIR/tracker.py"
    cp "$PROJECT_ROOT/install_launchagent.py" "$RES_DIR/install_launchagent.py"

    if [ -d "$PROJECT_ROOT/venv" ]; then
        ln -s "$PROJECT_ROOT/venv" "$RES_DIR/venv"
    else
        echo "❌ 错误：未找到 venv"
        exit 1
    fi

    if [ -f "$PROJECT_ROOT/build_assets/icon.icns" ]; then
        cp "$PROJECT_ROOT/build_assets/icon.icns" "$RES_DIR/icon.icns"
    fi
    if [ -f "$PROJECT_ROOT/icon.png" ]; then
        cp "$PROJECT_ROOT/icon.png" "$RES_DIR/icon.png"
    fi
    if [ -f "$PROJECT_ROOT/icon@2x.png" ]; then
        cp "$PROJECT_ROOT/icon@2x.png" "$RES_DIR/icon@2x.png"
    fi

    cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>小蛋</string>
    <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
    <key>CFBundleVersion</key><string>${VERSION}</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>icon</string>
    <key>CFBundleIconName</key><string>icon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleSignature</key><string>????</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppleEventsUsageDescription</key>
    <string>小蛋需要访问窗口标题来追踪应用使用时长。</string>
    <key>NSHumanReadableCopyright</key><string>MIT License</string>
</dict>
</plist>
EOF

    cat > "$MACOS_DIR/launcher" <<'LAUNCHER_EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$DIR"
export PATH="$DIR/venv/bin:$PATH"
export PYTHONPATH="$DIR/venv/lib/python3.13/site-packages:$PYTHONPATH"
exec "$DIR/venv/bin/python3" -u "$DIR/tracker.py" "$@"
LAUNCHER_EOF
    chmod +x "$MACOS_DIR/launcher"
fi

# ── 3. 写卸载脚本（py2app 模式下注入到 .app 内；手搓模式已经在上面写好） ─
if [ "$BUILD_MODE" = "py2app" ]; then
    cat > "$APP_BUNDLE/Contents/Resources/uninstall.sh" <<'UNINSTALL_EOF'
#!/bin/bash
# XiaoDan 卸载脚本
echo "正在卸载小蛋…"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.decard.xiaodan.plist"
if [ -f "$LAUNCH_AGENT" ]; then
    launchctl unload "$LAUNCH_AGENT" 2>/dev/null
    rm -f "$LAUNCH_AGENT"
    echo "✅ 已卸载 LaunchAgent"
fi
APP="/Applications/XiaoDan.app"
if [ -d "$APP" ]; then
    rm -rf "$APP"
    echo "✅ 已删除 /Applications/XiaoDan.app"
fi
read -p "是否同时删除使用数据（~/Library/Application Support/XiaoDan）？[y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
    rm -rf "$HOME/Library/Application Support/XiaoDan"
    echo "✅ 已删除数据"
fi
echo "卸载完成。"
UNINSTALL_EOF
    chmod +x "$APP_BUNDLE/Contents/Resources/uninstall.sh"
fi

# ── 4. Ad-hoc 签名 ────────────────────────────────────────────────────────
echo "✍️  Ad-hoc 签名…"
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null || echo "（codesign 失败，可忽略）"

# ── 5. 生成 .dmg ──────────────────────────────────────────────────────────
if [ "$BUILD_APP_ONLY" = false ]; then
    echo "💿 生成 .dmg…"
    TMP_DMG_DIR=$(mktemp -d)
    cp -R "$APP_BUNDLE" "$TMP_DMG_DIR/"
    ln -s /Applications "$TMP_DMG_DIR/Applications"

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
    echo "============================================================"
else
    echo ""
    echo "============================================================"
    echo "✅ 打包完成（仅 .app）"
    echo "   $APP_BUNDLE"
    echo "============================================================"
fi
