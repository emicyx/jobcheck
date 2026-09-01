"""Cookie 加密存储：AES-256-GCM，密钥来自环境变量，支持版本号轮换。"""

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_NONCE_LEN = 12


def _load_key() -> bytes:
    raw = settings.binding_encryption_key
    if raw:
        key = bytes.fromhex(raw)
        if len(key) != 32:
            raise ValueError("BINDING_ENCRYPTION_KEY 必须是 64 位 hex（32 字节）")
        return key
    # 未配置独立密钥时从 SECRET_KEY 派生（开发兜底，生产应显式配置）
    return hashlib.sha256(settings.secret_key.encode()).digest()


_KEY_VERSION = 1
_KEY = _load_key()


def encrypt_text(plaintext: str) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_KEY).encrypt(nonce, plaintext.encode("utf-8"), None)
    return bytes([_KEY_VERSION]) + nonce + ct


def decrypt_text(blob: bytes) -> str:
    if not blob:
        raise ValueError("空密文")
    version = blob[0]
    if version != _KEY_VERSION:
        raise ValueError(f"不支持的密钥版本: {version}")
    nonce = blob[1 : 1 + _NONCE_LEN]
    ct = blob[1 + _NONCE_LEN :]
    try:
        return AESGCM(_KEY).decrypt(nonce, ct, None).decode("utf-8")
    except InvalidTag as e:
        raise ValueError("解密失败：密文被篡改或密钥不匹配") from e
