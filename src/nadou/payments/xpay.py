"""WeChat virtual-goods purchase and server API signatures."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

import httpx

from nadou.payments.catalog import PayProduct


def _payload(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _signature(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Xpay:
    offer_id: str
    app_key: str = field(repr=False)
    env: int

    def __post_init__(self) -> None:
        if not self.offer_id or not self.app_key or self.env not in (0, 1):
            raise ValueError("虚拟支付配置不完整")

    def purchase(
        self, product: PayProduct, product_id: str, order_no: str, session_key: str
    ) -> dict:
        if not session_key or not product_id or not order_no:
            raise ValueError("虚拟支付下单参数不完整")
        sign_data = _payload(
            {
                "offerId": self.offer_id,
                "buyQuantity": 1,
                "env": self.env,
                "currencyType": "CNY",
                "productId": product_id,
                "goodsPrice": product.amount_fen,
                "outTradeNo": order_no,
                "attach": product.code,
            }
        )
        return {
            "mode": "short_series_goods",
            "signData": sign_data,
            "paySig": _signature(self.app_key, "requestVirtualPayment&" + sign_data),
            "signature": _signature(session_key, sign_data),
        }


def configured(env: int | None = None) -> Xpay:
    from fastapi import HTTPException

    from nadou.config import get_settings

    cfg = get_settings()
    target = cfg.xpay_env if env is None else env
    key = cfg.xpay_sandbox_key if target == 1 else cfg.xpay_live_key
    if target not in (0, 1) or not cfg.xpay_offer or not key.get_secret_value():
        raise HTTPException(503, "虚拟支付未配置")
    return Xpay(cfg.xpay_offer, key.get_secret_value(), target)


def provider_call(gateway: Xpay, path: str, body: dict) -> dict:
    from fastapi import HTTPException

    from nadou.auth import WeChatLogin
    from nadou.config import get_settings

    cfg = get_settings()
    origin = httpx.URL(WeChatLogin.SESSION_URL).copy_with(path="/")
    if not cfg.wx_appid or not cfg.wx_appsecret.get_secret_value():
        raise HTTPException(503, "虚拟支付未配置")
    try:
        with httpx.Client(base_url=origin, timeout=5, follow_redirects=False) as client:
            response = client.post(
                "/cgi-bin/stable_token",
                json={
                    "grant_type": "client_credential",
                    "appid": cfg.wx_appid,
                    "secret": cfg.wx_appsecret.get_secret_value(),
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError
            encoded = _payload(body)
            response = client.post(
                path,
                params={
                    "access_token": token,
                    "pay_sig": _signature(gateway.app_key, path + "&" + encoded),
                },
                content=encoded.encode(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            if path == "/xpay/notify_provide_goods" and not response.content.strip():
                return {}
            data = response.json()
            if path == "/xpay/notify_provide_goods" and data == {}:
                return {}
            if not isinstance(data, dict) or data.get("errcode") != 0:
                raise ValueError
            return data
    except (httpx.HTTPError, ValueError, AttributeError):
        raise HTTPException(502, "微信虚拟支付服务暂不可用") from None


def query_order(env: int, openid: str, order_no: str) -> dict:
    from fastapi import HTTPException

    result = provider_call(
        configured(env), "/xpay/query_order", {"openid": openid, "env": env, "order_id": order_no}
    )
    if not isinstance(result.get("order"), dict):
        raise HTTPException(502, "微信虚拟支付订单信息不完整")
    return result["order"]
