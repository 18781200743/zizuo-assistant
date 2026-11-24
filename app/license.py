"""离线账号绑定授权密钥。

密钥格式（Base64URL）::

    ZZK1-<base64url(payload + signature)>

payload 固定 23 字节（大端）::

    version   1 字节   = 1
    key_id    4 字节   随机编号
    acct_hash 8 字节   SHA-256(游戏ID.strip())[:8]
    issued    4 字节   距 2020-01-01 的天数
    expires   4 字节   距 2020-01-01 的天数；0xFFFFFFFF 表示永久
    features  2 字节   功能位

signature 固定 64 字节：ECDSA P-256 的 r(32) || s(32)。

私钥只保存在签发者本机；客户端只需要 ``app/license_public.pem``。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import struct
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

KEY_PREFIX = "ZZK1-"
KEY_VERSION = 1
PAYLOAD_STRUCT = struct.Struct(">BI8sIIH")
PAYLOAD_LEN = PAYLOAD_STRUCT.size  # 23
SIG_LEN = 64
TOTAL_LEN = PAYLOAD_LEN + SIG_LEN
PERMANENT_DAYS = 0xFFFFFFFF
EPOCH = date(2020, 1, 1)
MAX_DAYS = 36500

PUBLIC_KEY_PATH = Path(__file__).resolve().with_name("license_public.pem")

FEATURE_LABELS: dict[str, str] = {
    "farm": "农场",
    "plank": "跳一跳",
    "gacha": "抽卡",
    "guild": "公会",
    "maze": "迷宫",
    "daily": "日常",
    "talisman": "护符",
}

FEATURE_BITS: dict[str, int] = {
    name: 1 << index for index, name in enumerate(FEATURE_LABELS)
}


class LicenseError(Exception):
    """密钥格式或签名错误。"""


@dataclass
class LicenseResult:
    ok: bool
    message: str
    key_id: int = 0
    features: list[str] = field(default_factory=list)
    issued: date | None = None
    expires: date | None = None
    days_left: int | None = None
    permanent: bool = False


def normalize_account(game_id: str) -> str:
    """账号 ID 只去掉首尾空白，大小写保持原样（openid 区分大小写）。"""
    return str(game_id or "").strip()


def account_hash(game_id: str) -> bytes:
    return hashlib.sha256(normalize_account(game_id).encode("utf-8")).digest()[:8]


def account_code(game_id: str) -> str:
    """给用户看的账号码，避免直接暴露 openid。"""
    return base64.b32encode(account_hash(game_id)).decode("ascii").rstrip("=")


def days_since_epoch(value: date) -> int:
    return (value - EPOCH).days


def date_from_days(value: int) -> date:
    return EPOCH + timedelta(days=value)


def days_until(date_value: date, today: date | None = None) -> int:
    return (date_value - (today or date.today())).days


def feature_mask(names: Iterable[str]) -> int:
    mask = 0
    for name in names:
        mask |= FEATURE_BITS.get(str(name).strip().lower(), 0)
    return mask


def feature_names(mask: int) -> list[str]:
    return [name for name, bit in FEATURE_BITS.items() if mask & bit]


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def load_signing_key(path: str | Path) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def load_verify_key(path: str | Path | None = None) -> ec.EllipticCurvePublicKey:
    target = Path(path) if path else PUBLIC_KEY_PATH
    return serialization.load_pem_public_key(target.read_bytes())


def generate_keypair(private_path: str | Path, public_path: str | Path) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    private_path = Path(private_path)
    public_path = Path(public_path)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _sign(private_key: ec.EllipticCurvePrivateKey, payload: bytes) -> bytes:
    der = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def make_payload(
    game_id: str,
    days: int,
    features: Iterable[str],
    *,
    permanent: bool = False,
    key_id: int | None = None,
    today: date | None = None,
) -> bytes:
    if not normalize_account(game_id):
        raise LicenseError("游戏 ID 不能为空")
    today = today or date.today()
    if permanent:
        expires_day = PERMANENT_DAYS
    else:
        if not 1 <= int(days) <= MAX_DAYS:
            raise LicenseError("有效天数必须在 1 到 %d 之间" % MAX_DAYS)
        expires_day = days_since_epoch(today + timedelta(days=int(days)))
    if key_id is None:
        key_id = secrets.randbits(32)
    return PAYLOAD_STRUCT.pack(
        KEY_VERSION,
        key_id & 0xFFFFFFFF,
        account_hash(game_id),
        days_since_epoch(today),
        expires_day,
        feature_mask(features),
    )


def make_key(
    signing_key: ec.EllipticCurvePrivateKey,
    game_id: str,
    days: int,
    features: Iterable[str],
    *,
    permanent: bool = False,
    key_id: int | None = None,
    today: date | None = None,
) -> str:
    payload = make_payload(
        game_id,
        days,
        features,
        permanent=permanent,
        key_id=key_id,
        today=today,
    )
    return KEY_PREFIX + _b64encode(payload + _sign(signing_key, payload))


def normalize_key(raw: str) -> str:
    text = "".join(str(raw or "").split())
    if not text:
        raise LicenseError("密钥为空")
    if not text.startswith(KEY_PREFIX):
        if text.startswith("ZZK1"):
            text = KEY_PREFIX + text[4:]
        else:
            raise LicenseError("密钥格式不对，应以 ZZK1- 开头")
    return text


def parse_key(raw: str) -> tuple[dict, bytes]:
    text = normalize_key(raw)
    body = text[len(KEY_PREFIX):]
    try:
        data = _b64decode(body)
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("密钥内容不是有效的 Base64") from exc
    if len(data) != TOTAL_LEN:
        raise LicenseError("密钥长度不对，可能复制不完整")
    payload = data[:PAYLOAD_LEN]
    signature = data[PAYLOAD_LEN:]
    version, key_id, acct, issued, expires, features = PAYLOAD_STRUCT.unpack(payload)
    if version != KEY_VERSION:
        raise LicenseError("不支持的密钥版本：%d" % version)
    return {
        "key_id": key_id,
        "acct_hash": acct,
        "issued": date_from_days(issued),
        "expires_day": expires,
        "expires": date_from_days(expires) if expires != PERMANENT_DAYS else None,
        "permanent": expires == PERMANENT_DAYS,
        "features_mask": features,
        "features": feature_names(features),
    }, signature


def verify_key(
    raw: str,
    game_id: str,
    *,
    verify_key_obj: ec.EllipticCurvePublicKey | None = None,
    today: date | None = None,
) -> LicenseResult:
    try:
        info, signature = parse_key(raw)
    except LicenseError as exc:
        return LicenseResult(False, str(exc))

    key = verify_key_obj or load_verify_key()
    payload = (
        PAYLOAD_STRUCT.pack(
            KEY_VERSION,
            info["key_id"],
            info["acct_hash"],
            days_since_epoch(info["issued"]),
            PERMANENT_DAYS if info["permanent"] else days_since_epoch(info["expires"]),
            info["features_mask"],
        )
    )
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    try:
        key.verify(encode_dss_signature(r, s), payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return LicenseResult(False, "密钥签名无效，可能被修改过")

    if not secrets.compare_digest(info["acct_hash"], account_hash(game_id)):
        return LicenseResult(False, "此密钥不适用于当前游戏账号")

    today = today or date.today()
    if info["permanent"]:
        days_left = None
    else:
        days_left = days_until(info["expires"], today)
        if days_left < 0:
            return LicenseResult(
                False,
                "密钥已过期（到期日 %s）" % info["expires"].isoformat(),
                key_id=info["key_id"],
                features=info["features"],
                issued=info["issued"],
                expires=info["expires"],
                days_left=days_left,
            )

    return LicenseResult(
        True,
        "授权有效，剩余 %s" % ("永久" if info["permanent"] else "%d 天" % days_left),
        key_id=info["key_id"],
        features=info["features"],
        issued=info["issued"],
        expires=info["expires"],
        days_left=days_left,
        permanent=info["permanent"],
    )


def describe_key(raw: str, verify_key_obj: ec.EllipticCurvePublicKey | None = None) -> str:
    """给生成器用：只验签，不校验账号。"""
    text = normalize_key(raw)
    data = _b64decode(text[len(KEY_PREFIX):])
    if len(data) != TOTAL_LEN:
        raise LicenseError("密钥长度不对")
    payload, signature = data[:PAYLOAD_LEN], data[PAYLOAD_LEN:]
    key = verify_key_obj or load_verify_key()
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    try:
        key.verify(encode_dss_signature(r, s), payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise LicenseError("签名无效") from exc
    version, key_id, acct, issued, expires, features = PAYLOAD_STRUCT.unpack(payload)
    expire_text = "永久" if expires == PERMANENT_DAYS else date_from_days(expires).isoformat()
    return (
        "编号 %08X · 账号码 %s · 签发 %s · 到期 %s · 功能 %s"
        % (
            key_id,
            base64.b32encode(acct).decode("ascii").rstrip("="),
            date_from_days(issued).isoformat(),
            expire_text,
            "、".join(feature_names(features)) or "无",
        )
    )
