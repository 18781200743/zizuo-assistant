"""跨平台兼容层：Windows / macOS。

Windows 上保留原来的 WebView2 + Win32 行为；
macOS 上没有 WebView2、没有 Win32，所以：
  * 游戏窗口改成启动 Chrome/Edge（带 9229 调试端口），其余 CDP 代码完全复用；
  * 全局热键不可用时自动跳过（界面按钮仍然能用）；
  * 屏幕截图/窗口锁定/录制这些 Win32 功能会明确报「仅支持 Windows」。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

WIN_ONLY_MESSAGE = "该功能仅支持 Windows"


def require_windows() -> None:
    if not IS_WIN:
        raise RuntimeError(WIN_ONLY_MESSAGE)


MAC_BROWSER_NAMES = (
    "Google Chrome",
    "Microsoft Edge",
    "Chromium",
    "Brave Browser",
    "Vivaldi",
    "Arc",
    "Google Chrome Beta",
    "Google Chrome Canary",
)

MAC_BUNDLE_IDS = (
    "com.google.Chrome",
    "com.microsoft.edgemac",
    "org.chromium.Chromium",
    "com.brave.Browser",
    "com.vivaldi.Vivaldi",
    "company.thebrowser.Browser",
    "com.google.Chrome.beta",
    "com.google.Chrome.canary",
)


def mac_chromium_candidates() -> List[str]:
    """所有可能装 Chromium 系浏览器的地方（给诊断/兜底用）。"""
    home = Path.home()
    dirs = [Path("/Applications"), Path("/Applications/Utilities"), home / "Applications"]
    out: List[str] = []
    for base in dirs:
        for name in MAC_BROWSER_NAMES:
            exe = base / (name + ".app") / "Contents" / "MacOS" / name
            out.append(str(exe))
    return out


def _spotlight_browsers() -> List[str]:
    """用 Spotlight 找已安装的 Chromium 系浏览器（能覆盖改了名的 .app）。"""
    import os
    import subprocess

    found: List[str] = []
    for bundle_id in MAC_BUNDLE_IDS:
        try:
            proc = subprocess.run(
                ["mdfind", "kMDItemCFBundleIdentifier == '%s'" % bundle_id],
                capture_output=True, text=True, timeout=6,
            )
        except Exception:  # noqa: BLE001
            continue
        for line in (proc.stdout or "").splitlines():
            app = Path(line.strip())
            exe_dir = app / "Contents" / "MacOS"
            if not exe_dir.is_dir():
                continue
            try:
                for child in exe_dir.iterdir():
                    if child.is_file() and os.access(str(child), os.X_OK):
                        found.append(str(child))
                        break
            except OSError:
                continue
    return found


def _bundled_chromium() -> Optional[str]:
    """打包在 .app 里的 Chrome for Testing（装完就能用，不需要用户装浏览器）。"""
    if not getattr(sys, "frozen", False):
        return None
    roots = []
    try:
        roots.append(Path(sys._MEIPASS))
    except Exception:  # noqa: BLE001
        pass
    try:
        exe = Path(sys.executable).resolve()
        roots.append(exe.parent.parent / "Resources")
        roots.append(exe.parent.parent)
    except Exception:  # noqa: BLE001
        pass
    for root in roots:
        for folder in ("chrome-mac-x64", "chrome-mac-arm64", "chrome-mac"):
            base = root / folder
            for app_name in ("Google Chrome for Testing", "Google Chrome", "Chromium"):
                for exe_name in (app_name, "Google Chrome for Testing"):
                    candidate = base / (app_name + ".app") / "Contents" / "MacOS" / exe_name
                    try:
                        if candidate.exists():
                            return str(candidate)
                    except OSError:
                        continue
    return None


def mac_chromium_path() -> Optional[str]:
    """找 macOS 上的 Chrome / Edge / Chromium 可执行文件。"""
    if not IS_MAC:
        return None
    bundled = _bundled_chromium()
    if bundled:
        return bundled
    for candidate in mac_chromium_candidates() + _spotlight_browsers():
        try:
            if Path(candidate).exists():
                return candidate
        except OSError:
            continue
    return None
