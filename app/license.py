"""离线密钥：与游戏角色 ID 绑定。

同一把密钥只对生成时的角色 ID 有效，换号自动失效。
换 SECRET 后旧密钥全部失效。
"""

from __future__ import annotations

import hashlib
import os
import re

# 想换密钥就改这里，然后重新生成
SECRET = "xiaotaiyang-2026-11-26-v1"

# 超级密钥：不绑定游戏 ID
SUPER_SECRET = "xiaotaiyang-super-2026-11-26-v1"


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def make_key(game_id: str, secret: str = SECRET) -> str:
    """按游戏角色 ID 生成密钥。"""
    raw = "%s:%s" % (str(secret), str(game_id or "").strip())
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return "XS-%s-%s-%s" % (digest[:4], digest[4:8], digest[8:12])


def make_super_key(nonce: str = "", secret: str = SUPER_SECRET) -> str:
    """生成不绑定游戏 ID 的超级密钥。

    每次生成都不同：SS-XXXX-XXXX-YYYY
    前 8 位随机，后 4 位由 SECRET 和随机部分算出，用来校验。
    """
    raw = str(nonce or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{8}", raw):
        raw = hashlib.sha256(("%s|%s" % (secret, os.urandom(16).hex())).encode("utf-8")).hexdigest().upper()[:8]
    sig = hashlib.sha256(("xiaotaiyang-super:" + str(secret) + ":" + raw).encode("utf-8")).hexdigest().upper()[:4]
    return "SS-%s-%s-%s" % (raw[:4], raw[4:8], sig)


def is_super_key(text: str) -> bool:
    norm = _normalize(text)
    if not re.fullmatch(r"SS[0-9A-F]{12}", norm):
        return False
    nonce = norm[2:10]
    sig = norm[10:14]
    expect = hashlib.sha256(("xiaotaiyang-super:" + str(SUPER_SECRET) + ":" + nonce).encode("utf-8")).hexdigest().upper()[:4]
    return sig == expect


def is_valid_key(text: str, game_id: str) -> bool:
    if not text:
        return False
    if is_super_key(text):
        return True
    if not str(game_id or "").strip():
        return False
    return _normalize(text) == _normalize(make_key(game_id))
