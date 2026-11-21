import base64
import hashlib
import hmac
from urllib.parse import quote_plus

from nadou.admin.notify import DingTalkNotifier, DingTalkSigner, NotifierFactory
from nadou.config import Settings


def test_dingtalk_signs_the_webhook_and_does_not_embed_the_secret():
    captured: dict = {}

    def poster(url: str, payload: dict) -> None:
        captured["url"] = url
        captured["payload"] = payload

    notifier = DingTalkNotifier(
        "https://example.invalid/hook",
        "test-secret",
        poster=poster,
        clock=lambda: 1_700_000_000.0,
    )
    notifier.notify("新取证申请", "申请 QZ-1 已提交")
    timestamp = "1700000000000"
    digest = hmac.new(b"test-secret", f"{timestamp}\ntest-secret".encode(), hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(digest))
    assert captured["url"] == (f"https://example.invalid/hook?timestamp={timestamp}&sign={sign}")
    assert captured["payload"]["msgtype"] == "text"
    assert "test-secret" not in captured["payload"]["text"]["content"]
    assert (
        DingTalkSigner.signed_url("https://example.invalid/hook", "test-secret", 1_700_000_000.0)
        == (captured["url"])
    )


def test_missing_webhook_disables_dingtalk():
    settings = Settings(
        dingtalk_webhook_url="",
        dingtalk_webhook_secret="",
        task_export_path="",
    )
    notifier = NotifierFactory.from_settings(settings)
    notifier.notify("索引任务停滞", "超过 2 天没有成功的索引运行")
