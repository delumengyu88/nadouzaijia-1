"""Mini program payment routes. The WeChat notification is the grant."""

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nadou.auth import UserSessions
from nadou.models import User
from nadou.payments.service import PayError, PaymentService

logger = logging.getLogger("nadou.payments")


class CreateOrderIn(BaseModel):
    """`amount` is accepted and ignored. The server prices the tier."""

    model_config = ConfigDict(extra="ignore")

    tier: str | None = Field(default=None, max_length=32)
    amount: int | None = None


class PaymentApi:
    @staticmethod
    def register(app: FastAPI, get_session, clock, notifier, grant_writer) -> None:
        def payer(
            authorization: str | None = Header(default=None),
            session: Session = Depends(get_session, scope="function"),
        ) -> User:
            try:
                return UserSessions.resolve(session, authorization, clock())
            except HTTPException as exc:
                if exc.status_code == 401:
                    raise HTTPException(
                        401, "未登录", headers={"WWW-Authenticate": "Bearer"}
                    ) from None
                raise

        def service() -> PaymentService:
            return PaymentService(clock, notifier, grant_writer)

        @app.exception_handler(PayError)
        def pay_error(_request: Request, exc: PayError):
            logger.warning("payment rejected status=%s reason=%s", exc.status_code, exc.detail)
            if exc.notify:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"code": "FAIL", "message": "失败"},
                )
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        @app.get("/api/v1/pay/tiers")
        def list_tiers(user: User = Depends(payer), payments: PaymentService = Depends(service)):
            return payments.list_tiers(user)

        @app.post("/api/v1/pay/orders")
        def create_order(
            payload: CreateOrderIn,
            user: User = Depends(payer),
            session: Session = Depends(get_session, scope="function"),
            payments: PaymentService = Depends(service),
        ):
            # payload.amount is intentionally unused.
            return payments.create(session, user, payload.tier)

        @app.get("/api/v1/pay/orders/{order_number}")
        def get_order(
            order_number: str,
            user: User = Depends(payer),
            session: Session = Depends(get_session, scope="function"),
            payments: PaymentService = Depends(service),
        ):
            return payments.get_order(session, user, order_number)

        @app.post("/api/v1/pay/orders/{order_number}/sync")
        def sync_order(
            order_number: str,
            user: User = Depends(payer),
            session: Session = Depends(get_session, scope="function"),
            payments: PaymentService = Depends(service),
        ):
            return payments.sync(session, user, order_number)

        @app.post("/api/v1/pay/wechat/notify")
        async def wechat_notify(
            request: Request,
            session: Session = Depends(get_session, scope="function"),
            payments: PaymentService = Depends(service),
        ):
            payments.handle_notify(session, await request.body(), request.headers)
            return Response(status_code=204)
