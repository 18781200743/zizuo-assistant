"""离线密钥：与游戏角色 ID 绑定。

同一把密钥只对生成时的角色 ID 有效，换号自动失效。
换 SECRET 后旧密钥全部失效。
"""

from __future__ import annotations

import hashlib
import re

# 想换密钥就改这里，然后重新生成
SECRET = "xiaotaiyang-2026-11-26-v1"


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def make_key(game_id: str, secret: str = SECRET) -> str:
    """按游戏角色 ID 生成密钥。"""
    raw = "%s:%s" % (str(secret), str(game_id or "").strip())
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return "XS-%s-%s-%s" % (digest[:4], digest[4:8], digest[8:12])


def is_valid_key(text: str, game_id: str) -> bool:
    if not text or not str(game_id or "").strip():
        return False
    return _normalize(text) == _normalize(make_key(game_id))
