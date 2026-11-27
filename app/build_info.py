"""开发模式判断：源码运行 = 开发版；PyInstaller 打包出来的 exe = 发布版。

用法：
    from .build_info import dev_mode
    if dev_mode():
        ...只给开发时看的功能...

需要让打包后的 exe 也显示这些功能时（方便自己调试）：
    1) 命令行加 --dev 参数；或
    2) 设置环境变量 ZIZUO_DEV=1 后再启动。
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

_DEV_ENV_KEYS = ("ZIZUO_DEV", "ZIZUO_DEVMODE")

# 脚本版本：发新版时改这里，首页底部会显示。
APP_VERSION = "2026.11.27"


def is_frozen() -> bool:
    """是否运行在打包后的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def dev_mode(argv: Optional[List[str]] = None) -> bool:
    """True = 开发模式（显示开发功能）。

    优先级：--dev 参数 / 环境变量 > 是否打包。
    """
    args = list(sys.argv if argv is None else argv)
    # 源码里想预览「发布版界面」时加 --release
    if "--release" in args:
        return False
    if "--dev" in args:
        return True
    for key in _DEV_ENV_KEYS:
        value = str(os.environ.get(key) or "").strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
    return not is_frozen()
