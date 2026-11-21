import base64
import hashlib
import hmac
import json
import time
import urllib.request
from collections.abc import Callable
from urllib.parse import quote_plus

from nadou.config import Settings


class Notifier:
    """Workbench alerts. Implementations must not embed a webhook URL or secret."""

    def notify(self, title: str, body: str) -> None:
        raise NotImplementedError


class NullNotifier(Notifier):
    def notify(self, title: str, body: str) -> None:
        del title, body


class FakeNotifier(Notifier):
    """In-memory notifier for tests. Production uses DingTalk or NullNotifier."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.messages.append({"title": title, "body": body})


class DingTalkSigner:
    @staticmethod
    def signed_url(webhook_url: str, secret: str, epoch_seconds: float) -> str:
        timestamp = str(int(epoch_seconds * 1000))
        signed = f"{timestamp}\n{secret}".encode()
        digest = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(digest))
        join = "&" if "?" in webhook_url else "?"
        return f"{webhook_url}{join}timestamp={timestamp}&sign={sign}"


class UrlLibPoster:
    @staticmethod
    def post(url: str, payload: dict) -> None:
        data = json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()


class DingTalkNotifier(Notifier):
    """DingTalk text robot. URL and secret come from the caller, which reads the environment."""

    def __init__(
        self,
        webhook_url: str,
        secret: str,
        poster: Callable[[str, dict], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not webhook_url or not secret:
            raise RuntimeError("DingTalk webhook URL and secret are required")
        self.webhook_url = webhook_url
        self.secret = secret
        self.poster = poster or UrlLibPoster.post
        self.clock = clock or DingTalkNotifier._now

    def notify(self, title: str, body: str) -> None:
        url = DingTalkSigner.signed_url(self.webhook_url, self.secret, self.clock())
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
        try:
            self.poster(url, payload)
        except Exception:
            return

    @staticmethod
    def _now() -> float:
        return time.time()


class NotifierFactory:
    @staticmethod
    def from_settings(settings: Settings) -> Notifier:
        url = settings.dingtalk_webhook_url.strip()
        secret = settings.dingtalk_webhook_secret.strip()
        if not url or not secret:
            return NullNotifier()
        return DingTalkNotifier(url, secret)
