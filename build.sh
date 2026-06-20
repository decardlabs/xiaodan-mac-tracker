#!/bin/bash
# 小蛋 macOS app 打包脚本
# 用法：./build.sh
#
# 注意：py2app 在 Apple Silicon 上生成的 Python stub 没有有效签名，
# 必须在打包后执行 codesign，否则 app 启动时会被系统直接 kill。
# 本脚本已把这一步自动化，不需要手动补跑。

set -e

cd "$(dirname "$0")"

echo "🧹  清理旧构建产物..."
rm -rf build dist

echo "📦  执行 py2app 打包..."
python3 setup.py py2app

echo "🔏  Ad-hoc 签名（Apple Silicon 必须）..."
codesign --force --deep --sign - dist/小蛋.app

echo ""
echo "✅  打包完成：dist/小蛋.app"
echo "    open dist/小蛋.app"
