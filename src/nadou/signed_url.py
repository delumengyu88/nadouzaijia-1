import hashlib
import time
from typing import Protocol

_DERIVED = frozenset({"lowres", "card", "hd"})


class SignedUrlProvider(Protocol):
    """Sign a derived image. Implementations must not sign an original object."""

    def sign_derived(self, variant: str, record_id: str, user_id: str | None) -> str: ...


class FakeSignedUrlProvider:
    """Stand-in signer. URLs have no host, bucket, or original object key."""

    def __init__(self, ttl_seconds: int = 300, now=None) -> None:
        self.ttl_seconds = ttl_seconds
        self._now = now or time.time

    def sign_derived(self, variant: str, record_id: str, user_id: str | None) -> str:
        if variant not in _DERIVED:
            raise ValueError("only derived variants can be signed")
        if not record_id:
            raise ValueError("record_id is required")
        expires = int(self._now()) + self.ttl_seconds
        uid = user_id or "anon"
        payload = f"{variant}|{record_id}|{uid}|{expires}"
        signature = hashlib.sha256(payload.encode()).hexdigest()[:24]
        return f"signed://derived/{variant}/{record_id}?exp={expires}&uid={uid}&sig={signature}"
