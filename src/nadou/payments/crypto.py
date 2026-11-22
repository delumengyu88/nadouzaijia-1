"""WeChat Pay APIv3 signatures and AES-GCM resource decryption."""

import base64

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes, PublicKeyTypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def load_private_key(pem: bytes) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("merchant key must be RSA")
    return key


def load_public_key(pem: bytes) -> PublicKeyTypes:
    """Accept a WeChat Pay public key or a platform certificate."""
    if b"BEGIN CERTIFICATE" in pem:
        return x509.load_pem_x509_certificate(pem).public_key()
    return serialization.load_pem_public_key(pem)


def sign_rsa(private_key: PrivateKeyTypes, message: bytes) -> str:
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def verify_rsa(public_key: PublicKeyTypes, message: bytes, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return False
    return True


def decrypt_resource(
    api_v3_key: str, *, nonce: str, ciphertext: str, associated_data: str
) -> bytes:
    if len(api_v3_key.encode()) != 32:
        raise ValueError("resource")
    if not isinstance(nonce, str) or not isinstance(ciphertext, str):
        raise ValueError("resource")
    if not isinstance(associated_data, str):
        associated_data = ""
    try:
        raw = base64.b64decode(ciphertext)
        return AESGCM(api_v3_key.encode()).decrypt(nonce.encode(), raw, associated_data.encode())
    except Exception:
        raise ValueError("resource") from None


def authorization_header(
    *,
    mchid: str,
    serial: str,
    private_key: PrivateKeyTypes,
    method: str,
    canonical_url: str,
    body: str,
    timestamp: str,
    nonce: str,
) -> str:
    message = f"{method}\n{canonical_url}\n{timestamp}\n{nonce}\n{body}\n".encode()
    signature = sign_rsa(private_key, message)
    return (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{mchid}",'
        f'nonce_str="{nonce}",'
        f'signature="{signature}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{serial}"'
    )


def mini_program_pay_sign(
    private_key: PrivateKeyTypes, appid: str, timestamp: str, nonce: str, package: str
) -> str:
    message = f"{appid}\n{timestamp}\n{nonce}\n{package}\n".encode()
    return sign_rsa(private_key, message)


def notify_message(timestamp: str, nonce: str, body: bytes) -> bytes:
    return timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
