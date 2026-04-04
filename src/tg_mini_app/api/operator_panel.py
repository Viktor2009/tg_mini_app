"""Веб-панель оператора: список заказов и смена статуса."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated, Any
from urllib.parse import urlencode

from aiogram import Bot
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from tg_mini_app.api.deps import get_db_session
from tg_mini_app.db import models
from tg_mini_app.order_flow import (
    OrderStatus,
    require_active_for_ship,
    require_active_or_shipping_for_delivered,
    require_pending_operator_for_action,
    unlock_cart_if_locked,
)
from tg_mini_app.paths import TEMPLATES_DIR
from tg_mini_app.settings import get_settings
from tg_mini_app.telegram_keyboards import payment_reply_markup

router = APIRouter(prefix="/operator-panel", tags=["operator-panel"])
_security = HTTPBasic(auto_error=False)
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

OPERATOR_PANEL_COOKIE_NAME = "opanel_auth"
OPERATOR_PANEL_COOKIE_TTL_SEC = 7 * 24 * 3600


def _make_panel_session_cookie(secret: str, ttl_sec: int = OPERATOR_PANEL_COOKIE_TTL_SEC) -> str:
    exp = int(time.time()) + ttl_sec
    exp_s = str(exp)
    sig = hmac.new(
        secret.encode("utf-8"),
        exp_s.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp_s}.{sig}"


def _panel_session_cookie_ok(raw: str, secret: str) -> bool:
    try:
        exp_s, sig = raw.split(".", 1)
        exp = int(exp_s)
    except (ValueError, IndexError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        exp_s.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return secrets.compare_digest(sig, expected)


def _request_panel_cookie_ok(request: Request, secret: str) -> bool:
    raw = request.cookies.get(OPERATOR_PANEL_COOKIE_NAME)
    if not raw:
        return False
    return _panel_session_cookie_ok(raw, secret)

STATUS_LABELS_RU: dict[str, str] = {
    OrderStatus.PENDING_OPERATOR: "На согласовании",
    OrderStatus.PENDING_OPERATOR_CHANGE_TEXT: "Правки (текст)",
    OrderStatus.PENDING_CUSTOMER_CHANGE_ACCEPT: "Ждём ответ клиента",
    OrderStatus.AWAITING_PAYMENT: "Ждём оплату",
    OrderStatus.REJECTED_BY_OPERATOR: "Отклонён (оператор)",
    OrderStatus.REJECTED_BY_CUSTOMER: "Отклонён (клиент)",
    OrderStatus.CANCELLED_BY_CUSTOMER: "Отменён клиентом",
    OrderStatus.ACTIVE: "Активен",
    OrderStatus.OUT_FOR_DELIVERY: "Передан в доставку",
    OrderStatus.DELIVERED: "Доставлен",
}


async def require_operator_panel_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)],
) -> None:
    settings = get_settings()
    token = settings.operator_panel_token
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    if _request_panel_cookie_ok(request, token):
        return
    if credentials is not None:
        user_ok = credentials.username == "operator"
        expected = token
        got = credentials.password
        pass_ok = len(got) == len(expected) and secrets.compare_digest(
            got.encode("utf-8"),
            expected.encode("utf-8"),
        )
        if user_ok and pass_ok:
            return
    if request.method in ("GET", "HEAD"):
        raise HTTPException(
            status_code=307,
            headers={"Location": "/operator-panel/login"},
        )
    raise HTTPException(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Operator Panel"'},
    )


def _redirect_panel(filter_status: str, **params: str) -> RedirectResponse:
    q: dict[str, str] = {}
    if filter_status.strip():
        q["status"] = filter_status.strip()
    q.update({k: v for k, v in params.items() if v})
    url = "/operator-panel"
    if q:
        url += "?" + urlencode(q)
    return RedirectResponse(url=url, status_code=303)


def _bot(request: Request) -> Bot | None:
    return getattr(request.app.state, "bot", None)


@router.get("/ping")
async def operator_panel_ping() -> dict[str, Any]:
    """
    Без авторизации: проверка, что API жив и панель включена в конфиге.
    """
    settings = get_settings()
    enabled = bool((settings.operator_panel_token or "").strip())
    return {
        "api": "ok",
        "operator_panel_configured": enabled,
        "open_locally": "http://127.0.0.1:8000/operator-panel",
        "note": (
            "Ссылка из BotFather / trycloudflare устаревает после перезапуска "
            "туннеля — для проверки на этом ПК используйте 127.0.0.1."
        ),
    }


@router.get("/login", response_class=HTMLResponse)
async def operator_panel_login_page(request: Request) -> Any:
    settings = get_settings()
    token = settings.operator_panel_token
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    if _request_panel_cookie_ok(request, token):
        return RedirectResponse(url="/operator-panel", status_code=303)
    return _templates.TemplateResponse(
        request=request,
        name="operator_panel_login.html",
        context={"title": "Панель оператора — вход"},
    )


@router.post("/login")
async def operator_panel_login_submit(
    request: Request,
    password: Annotated[str, Form()],
) -> Any:
    settings = get_settings()
    token = settings.operator_panel_token
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    pwd = (password or "").strip()
    pass_ok = len(pwd) == len(token) and secrets.compare_digest(
        pwd.encode("utf-8"),
        token.encode("utf-8"),
    )
    if not pass_ok:
        return _templates.TemplateResponse(
            request=request,
            name="operator_panel_login.html",
            context={
                "title": "Панель оператора — вход",
                "error": "Неверный пароль.",
            },
            status_code=401,
        )
    response = RedirectResponse(url="/operator-panel", status_code=303)
    response.set_cookie(
        key=OPERATOR_PANEL_COOKIE_NAME,
        value=_make_panel_session_cookie(token),
        max_age=OPERATOR_PANEL_COOKIE_TTL_SEC,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/operator-panel",
    )
    return response


@router.get("/logout")
async def operator_panel_logout() -> RedirectResponse:
    settings = get_settings()
    if not settings.operator_panel_token:
        raise HTTPException(status_code=404, detail="Not Found")
    response = RedirectResponse(url="/operator-panel/login", status_code=303)
    response.delete_cookie(OPERATOR_PANEL_COOKIE_NAME, path="/operator-panel")
    return response


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def operator_panel_home(
    request: Request,
    status: str | None = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    q = select(models.Order).order_by(models.Order.id.desc()).limit(250)
    if status and status in STATUS_LABELS_RU:
        q = q.where(models.Order.status == status)
    rows = (await session.execute(q)).scalars().all()
    flash_err = request.query_params.get("err")
    flash_ok = request.query_params.get("ok")
    return _templates.TemplateResponse(
        request=request,
        name="operator_panel.html",
        context={
            "title": "Панель оператора",
            "orders": rows,
            "status_filter": status or "",
            "status_labels": STATUS_LABELS_RU,
            "status_keys": list(STATUS_LABELS_RU.keys()),
            "flash_err": flash_err,
            "flash_ok": flash_ok,
        },
    )


@router.post("/orders/{order_id}/action")
async def operator_order_action(
    order_id: int,
    request: Request,
    action: Annotated[str, Form()],
    filter_status: Annotated[str, Form()] = "",
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    settings = get_settings()
    bot = _bot(request)
    action = (action or "").strip()

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        return _redirect_panel(filter_status, err="not_found")

    op_id = settings.operator_chat_id or 0

    if action == "approve":
        err = require_pending_operator_for_action(order.status)
        if err is not None:
            return _redirect_panel(filter_status, err="bad_state")
        meta = dict(order.meta)
        meta["operator_chat_id"] = op_id
        order.meta = meta
        order.status = OrderStatus.AWAITING_PAYMENT
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=(
                    f"Заказ #{order.id} согласован.\n"
                    "Выберите способ оплаты:"
                ),
                reply_markup=payment_reply_markup(order.id),
            )
        return _redirect_panel(filter_status, ok="1")

    if action == "reject":
        err = require_pending_operator_for_action(order.status)
        if err is not None:
            return _redirect_panel(filter_status, err="bad_state")
        order.status = OrderStatus.REJECTED_BY_OPERATOR
        await unlock_cart_if_locked(session, order.cart_id)
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=f"К сожалению, заказ #{order.id} не принят.",
            )
        return _redirect_panel(filter_status, ok="1")

    if action == "ship":
        err = require_active_for_ship(order.status)
        if err is not None:
            return _redirect_panel(filter_status, err="bad_state")
        cid = order.customer_tg_id
        order.status = OrderStatus.OUT_FOR_DELIVERY
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=cid,
                text=(
                    f"Заказ #{order.id} передан в доставку. "
                    "Ожидайте курьера."
                ),
            )
        return _redirect_panel(filter_status, ok="1")

    if action == "delivered":
        err = require_active_or_shipping_for_delivered(order.status)
        if err is not None:
            return _redirect_panel(filter_status, err="bad_state")
        cid = order.customer_tg_id
        order.status = OrderStatus.DELIVERED
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=cid,
                text=f"Заказ #{order.id} доставлен. Приятного аппетита!",
            )
        return _redirect_panel(filter_status, ok="1")

    return _redirect_panel(filter_status, err="bad_action")
