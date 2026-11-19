#!/usr/bin/env bash
# 在 macOS 上打包成 .app（必须在 Mac 上跑，PyInstaller 不能交叉编译）
# 用法：bash build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -d ".venv-mac" ]; then
  "$PY" -m venv .venv-mac
fi
# shellcheck disable=SC1091
source .venv-mac/bin/activate

python -m pip install --upgrade pip
pip install -r requirements_mac.txt

rm -rf build dist

# 目标架构：Intel Mac 用 x86_64；Apple Silicon 想兼容 Intel 也先按 x86_64 打
pyinstaller --noconfirm --clean --windowed \
  --name "Zizuo助手" \
  --osx-bundle-identifier com.zizuo.assist \
  --target-arch x86_64 \
  --add-data "app/assist_mapping.json:app" \
  --add-data "app/instructions.json:app" \
  --add-data "app/automation_config.json:app" \
  main.py

# ad-hoc 签名（不是开发者签名，但能让本机少一点麻烦）
codesign --force --deep --sign - "dist/Zizuo助手.app" 2>/dev/null || true

# 打成 zip，方便发给别人（保留 .app 结构）
ditto -c -k --keepParent "dist/Zizuo助手.app" "dist/Zizuo助手-mac.zip"

echo
echo "完成： dist/Zizuo助手-mac.zip"
