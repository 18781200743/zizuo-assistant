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
export PIP_DISABLE_PIP_VERSION_CHECK=1

pip install -r requirements_mac.txt

# Chrome for Testing 压缩包缓存：第一次下载，后面直接复用
CFT_ZIP="${CFT_ZIP:-$HOME/.cache/zizuo/chrome-mac-x64.zip}"
mkdir -p "$(dirname "$CFT_ZIP")"
CFT_PID=""
if [ ! -s "$CFT_ZIP" ]; then
  CFT_JSON="$(curl -fsSL https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json)"
  CFT_VER="$(printf '%s' "$CFT_JSON" | python -c "import sys,json;print(json.load(sys.stdin)['channels']['Stable']['version'])")"
  echo "Chrome for Testing: ${CFT_VER}（后台下载，和 PyInstaller 并行）"
  (
    curl -fsSL -o "$CFT_ZIP" \
      "https://storage.googleapis.com/chrome-for-testing-public/${CFT_VER}/mac-x64/chrome-mac-x64.zip"
  ) &
  CFT_PID=$!
fi

rm -rf build dist

# 目标架构：Intel Mac 用 x86_64；Apple Silicon 想兼容 Intel 也先按 x86_64 打
pyinstaller --noconfirm --clean --windowed \
  --name "Zizuo助手" \
  --osx-bundle-identifier com.zizuo.assist \
  --target-arch x86_64 \
  --add-data "app/assist_mapping.json:app" \
  --add-data "app/instructions.json:app" \
  --add-data "app/commands_presets.json:app" \
  --add-data "app/chest_aliases_4758a.json:app" \
  --add-data "app/automation_config.json:app" \
  --add-data "app/assets:app/assets" \
  main.py

if [ -n "$CFT_PID" ]; then
  wait "$CFT_PID" || true
fi
if [ ! -s "$CFT_ZIP" ]; then
  CFT_JSON="$(curl -fsSL https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json)"
  CFT_VER="$(printf '%s' "$CFT_JSON" | python -c "import sys,json;print(json.load(sys.stdin)['channels']['Stable']['version'])")"
  echo "Chrome for Testing: ${CFT_VER}（重新下载）"
  curl -fsSL -o "$CFT_ZIP" \
    "https://storage.googleapis.com/chrome-for-testing-public/${CFT_VER}/mac-x64/chrome-mac-x64.zip"
fi

# 把 Chrome for Testing 打进 .app（用户不用自己装浏览器，装完就能开）
mkdir -p "dist/Zizuo助手.app/Contents/Resources"
rm -rf "dist/Zizuo助手.app/Contents/Resources/chrome-mac-x64"
ditto -x -k "$CFT_ZIP" "dist/Zizuo助手.app/Contents/Resources/"

# ad-hoc 签名：先签内嵌的 Chrome，再签整个 .app（顺序反了会导致校验失败）
codesign --force --deep --sign - "dist/Zizuo助手.app/Contents/Resources/chrome-mac-x64/Google Chrome for Testing.app" 2>/dev/null || true
codesign --force --deep --sign - "dist/Zizuo助手.app" 2>/dev/null || true

# 打成 zip，方便发给别人（保留 .app 结构）
ditto -c -k --keepParent "dist/Zizuo助手.app" "dist/Zizuo助手-mac.zip"

echo
echo "完成： dist/Zizuo助手-mac.zip"
