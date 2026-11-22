"""Validate WeChat's encrypted JSON message-push envelope for this app."""

import base64
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException

from nadou.config import get_settings


def configured_key() -> bytes:
    settings = get_settings()
    try:
        key = base64.b64decode(settings.xpay_push_aes.get_secret_value() + "=", validate=True)
    except ValueError:
        key = b""
    if len(key) != 32 or not settings.xpay_push_token.get_secret_value():
        raise HTTPException(503, "消息推送未配置")
    return key


def verify(query, encrypted: str | None = None) -> None:
    cfg = get_settings()
    token = cfg.xpay_push_token.get_secret_value()
    if not token:
        raise HTTPException(503, "消息推送未配置")
    stamp, nonce = query.get("timestamp", ""), query.get("nonce", "")
    if (
        not stamp.isascii()
        or not stamp.isdecimal()
        or len(stamp) > 12
        or not nonce
        or abs(time.time() - int(stamp)) > 300
    ):
        raise HTTPException(403, "无效通知")
    fields = [token, stamp, nonce]
    if encrypted is not None:
        fields.append(encrypted)
    supplied = query.get("msg_signature" if encrypted is not None else "signature", "")
    digest = hashlib.sha1("".join(sorted(fields)).encode()).hexdigest()
    if not supplied.isascii() or not hmac.compare_digest(digest, supplied):
        raise HTTPException(403, "无效通知")


def open_event(query, body: bytes) -> dict:
    cfg = get_settings()
    try:
        encrypted = json.loads(body)["Encrypt"]
        if not isinstance(encrypted, str) or not encrypted:
            raise ValueError
        verify(query, encrypted)
        key = configured_key()
        if len(key) != 32 or not cfg.wx_appid:
            raise ValueError
        decrypt = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decrypt.update(base64.b64decode(encrypted, validate=True)) + decrypt.finalize()
        unpad = padding.PKCS7(256).unpadder()
        clear = unpad.update(padded) + unpad.finalize()
        size = int.from_bytes(clear[16:20], "big")
        if not size or clear[20 + size :] != cfg.wx_appid.encode():
            raise ValueError
        event = json.loads(clear[20 : 20 + size])
        if not isinstance(event, dict):
            raise ValueError
        return event
    except (KeyError, TypeError, ValueError):
        raise HTTPException(403, "无效通知") from None
