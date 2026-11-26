"""离线密钥：解锁受保护功能（复活/闪现、地图/红包、跳一跳、公会情报）。

密钥由 SECRET 派生，只有拿到本文件的人能重新生成。
换 SECRET 后旧密钥立即失效。
"""

from __future__ import annotations

import hashlib
import re

# 想换密钥就改这里，然后用 tools/make_key.py 重新生成
SECRET = "xiaotaiyang-2026-11-26-v1"


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def make_key(secret: str = SECRET) -> str:
    digest = hashlib.sha256(("xiaotaiyang:" + str(secret)).encode("utf-8")).hexdigest().upper()
    return "XS-%s-%s-%s" % (digest[:4], digest[4:8], digest[8:12])


VALID_KEY = make_key()


def is_valid_key(text: str) -> bool:
    return bool(text) and _normalize(text) == _normalize(VALID_KEY)


if __name__ == "__main__":
    print(VALID_KEY)
