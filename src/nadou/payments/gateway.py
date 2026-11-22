"""HTTPS client for WeChat Pay APIv3. Tests pass a transport and do not call out."""

import json
import logging
import secrets
import time
from urllib.parse import quote

import httpx

from nadou.payments.config import WeChatPaySettings
from nadou.payments.crypto import authorization_header

logger = logging.getLogger("nadou.payments")

_HOST = "https://api.mch.weixin.qq.com"
_JSAPI_PATH = "/v3/pay/transactions/jsapi"


class WeChatUpstreamError(Exception):
    def __init__(self, status: int, code: str | None) -> None:
        self.status = status
        self.code = code


def _error_code(payload: bytes) -> str | None:
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("code"), str):
        return data["code"][:64]
    return None


def httpx_transport(
    method: str, url: str, headers: dict[str, str], content: bytes | None
) -> tuple[int, bytes]:
    try:
        response = httpx.request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=10,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise WeChatUpstreamError(0, exc.__class__.__name__) from None
    return response.status_code, response.content


class HttpxWeChatPayGateway:
    def __init__(self, config: WeChatPaySettings, transport=httpx_transport, clock=None) -> None:
        self.config = config
        self.transport = transport
        self.clock = clock or (lambda: int(time.time()))

    def prepay(
        self, *, description: str, out_trade_no: str, amount_fen: int, openid: str, notify_url: str
    ) -> str:
        payload = {
            "appid": self.config.appid,
            "mchid": self.config.mchid,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": notify_url,
            "amount": {"total": amount_fen, "currency": "CNY"},
            "payer": {"openid": openid},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        status, raw = self._call("POST", _JSAPI_PATH, body)
        if status != 200:
            raise WeChatUpstreamError(status, _error_code(raw))
        try:
            data = json.loads(raw)
        except ValueError:
            raise WeChatUpstreamError(status, None) from None
        prepay_id = data.get("prepay_id") if isinstance(data, dict) else None
        if not isinstance(prepay_id, str) or not prepay_id or len(prepay_id) > 64:
            raise WeChatUpstreamError(status, "prepay_id")
        return prepay_id

    def query(self, out_trade_no: str) -> dict | None:
        number = quote(out_trade_no, safe="")
        mchid = quote(self.config.mchid, safe="")
        canonical = f"/v3/pay/transactions/out-trade-no/{number}?mchid={mchid}"
        status, raw = self._call("GET", canonical, b"")
        if status == 404:
            return None
        if status != 200:
            raise WeChatUpstreamError(status, _error_code(raw))
        try:
            data = json.loads(raw)
        except ValueError:
            raise WeChatUpstreamError(status, None) from None
        if not isinstance(data, dict):
            raise WeChatUpstreamError(status, None)
        return data

    def _call(self, method: str, canonical: str, body: bytes) -> tuple[int, bytes]:
        timestamp = str(self.clock())
        nonce = secrets.token_hex(16)
        header = authorization_header(
            mchid=self.config.mchid,
            serial=self.config.mch_serial,
            private_key=self.config.private_key,
            method=method,
            canonical_url=canonical,
            body=body.decode() if body else "",
            timestamp=timestamp,
            nonce=nonce,
        )
        headers = {
            "Authorization": header,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Wechatpay-Serial": self.config.platform_key_id,
            "User-Agent": "nadou-wechat-pay",
        }
        return self.transport(method, _HOST + canonical, headers, body or None)


def build_gateway(config: WeChatPaySettings) -> HttpxWeChatPayGateway:
    return HttpxWeChatPayGateway(config)
