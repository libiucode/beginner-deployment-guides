"""独立主密钥与用途绑定的认证加密。"""
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings


def _cipher():
    key = Path(settings.XGIFT_KEY_FILE).read_bytes()
    if len(key) != 32:
        raise ValueError("主密钥文件必须包含 32 字节")
    return AESGCM(key)


def seal(value: bytes, purpose: str) -> bytes:
    nonce = os.urandom(12)
    return b"\x01" + nonce + _cipher().encrypt(nonce, value, purpose.encode("utf-8"))


def unseal(payload: bytes, purpose: str) -> bytes:
    if len(payload) < 29 or payload[0] != 1:
        raise ValueError("不支持或损坏的密文")
    return _cipher().decrypt(payload[1:13], payload[13:], purpose.encode("utf-8"))
