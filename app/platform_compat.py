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


def mac_chromium_path() -> Optional[str]:
    """找 macOS 上的 Chrome / Edge / Chromium 可执行文件。"""
    if not IS_MAC:
        return None
    candidates: List[Path] = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for path in candidates:
        try:
            if path.exists():
                return str(path)
        except OSError:
            continue
    return None
