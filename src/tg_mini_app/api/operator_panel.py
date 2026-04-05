"""Веб-панель оператора: список заказов и смена статуса."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated, Any
from urllib.parse import quote, urlencode

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
    require_operator_cancel_order,
    require_pending_operator_for_action,
    unlock_cart_if_locked,
)
from tg_mini_app.order_meta import (
    LINE_STATUS_AWAITING_CUSTOMER,
    LINE_STATUS_REPLACED,
    meta_items,
    set_meta_items,
    total_from_meta_items,
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
    OrderStatus.PENDING_CUSTOMER_SUBSTITUTION: "Ждём ответ по заменам",
    OrderStatus.AWAITING_PAYMENT: "Ждём оплату",
    OrderStatus.REJECTED_BY_OPERATOR: "Отклонён (оператор)",
    OrderStatus.REJECTED_BY_CUSTOMER: "Отклонён (клиент)",
    OrderStatus.CANCELLED_BY_CUSTOMER: "Отменён клиентом",
    OrderStatus.CANCELLED_BY_OPERATOR: "Отменён оператором",
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


def _redirect_panel(
    filter_status: str,
    *,
    filter_route: str = "",
    **params: str,
) -> RedirectResponse:
    q: dict[str, str] = {}
    if filter_status.strip():
        q["status"] = filter_status.strip()
    if filter_route.strip():
        q["route"] = filter_route.strip()
    q.update({k: v for k, v in params.items() if v})
    url = "/operator-panel"
    if q:
        url += "?" + urlencode(q)
    return RedirectResponse(url=url, status_code=303)


def _bot(request: Request) -> Bot | None:
    return getattr(request.app.state, "bot", None)


def _allow_line_substitution(status: str) -> bool:
    return status in (
        OrderStatus.PENDING_OPERATOR,
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.ACTIVE,
    )


def _allow_delivery_route_edit(status: str) -> bool:
    return status not in (
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED_BY_CUSTOMER,
        OrderStatus.CANCELLED_BY_OPERATOR,
        OrderStatus.REJECTED_BY_OPERATOR,
        OrderStatus.REJECTED_BY_CUSTOMER,
    )


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
    route: str | None = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    q = select(models.Order).order_by(models.Order.id.desc()).limit(250)
    if status and status in STATUS_LABELS_RU:
        q = q.where(models.Order.status == status)
    rows = list((await session.execute(q)).scalars().all())
    route_filter = (route or "").strip()
    if route_filter:
        rows = [
            o
            for o in rows
            if str((o.meta or {}).get("delivery_route", "")).strip() == route_filter
        ]
    flash_err = request.query_params.get("err")
    flash_ok = request.query_params.get("ok")
    route_filter_q = quote(route_filter, safe="") if route_filter else ""
    return _templates.TemplateResponse(
        request=request,
        name="operator_panel.html",
        context={
            "title": "Панель оператора",
            "orders": rows,
            "status_filter": status or "",
            "route_filter": route_filter,
            "route_filter_q": route_filter_q,
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
    filter_route: Annotated[str, Form()] = "",
    delivery_route: Annotated[str, Form()] = "",
    line_index: Annotated[str, Form()] = "",
    new_product_id: Annotated[str, Form()] = "",
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    settings = get_settings()
    bot = _bot(request)
    action = (action or "").strip()
    fr = (filter_route or "").strip()

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        return _redirect_panel(filter_status, filter_route=fr, err="not_found")

    op_id = settings.operator_chat_id or 0

    def red(**kw: str) -> RedirectResponse:
        return _redirect_panel(filter_status, filter_route=fr, **kw)

    if action == "approve":
        err = require_pending_operator_for_action(order.status)
        if err is not None:
            return red(err="bad_state")
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
        return red(ok="1")

    if action == "reject":
        err = require_pending_operator_for_action(order.status)
        if err is not None:
            return red(err="bad_state")
        order.status = OrderStatus.REJECTED_BY_OPERATOR
        await unlock_cart_if_locked(session, order.cart_id)
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=f"К сожалению, заказ #{order.id} не принят.",
            )
        return red(ok="1")

    if action == "ship":
        err = require_active_for_ship(order.status)
        if err is not None:
            return red(err="bad_state")
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
        return red(ok="1")

    if action == "delivered":
        err = require_active_or_shipping_for_delivered(order.status)
        if err is not None:
            return red(err="bad_state")
        cid = order.customer_tg_id
        order.status = OrderStatus.DELIVERED
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=cid,
                text=f"Заказ #{order.id} доставлен. Приятного аппетита!",
            )
        return red(ok="1")

    if action == "cancel_operator":
        err = require_operator_cancel_order(order.status)
        if err is not None:
            return red(err="bad_state")
        order.status = OrderStatus.CANCELLED_BY_OPERATOR
        await unlock_cart_if_locked(session, order.cart_id)
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=f"Заказ #{order.id} отменён оператором.",
            )
        return red(ok="1")

    if action == "set_delivery_route":
        if not _allow_delivery_route_edit(order.status):
            return red(err="bad_state")
        meta = dict(order.meta or {})
        meta["delivery_route"] = (delivery_route or "").strip()
        order.meta = meta
        await session.commit()
        return red(ok="1")

    if action == "mark_payment_received":
        if order.status not in (
            OrderStatus.AWAITING_PAYMENT,
            OrderStatus.ACTIVE,
            OrderStatus.OUT_FOR_DELIVERY,
        ):
            return red(err="bad_state")
        meta = dict(order.meta or {})
        meta["payment_received_confirmed"] = True
        order.meta = meta
        await session.commit()
        return red(ok="1")

    if action in ("substitute_direct", "substitute_propose"):
        if not _allow_line_substitution(order.status):
            return red(err="bad_state")
        try:
            idx = int((line_index or "").strip())
            pid = int((new_product_id or "").strip())
        except ValueError:
            return red(err="bad_form")
        items = meta_items(dict(order.meta or {}))
        if idx < 0 or idx >= len(items):
            return red(err="bad_line")
        product = await session.get(models.Product, pid)
        if product is None:
            return red(err="bad_product")
        item = dict(items[idx])
        if action == "substitute_direct":
            item["product_id"] = product.id
            item["name"] = product.name
            item["price_snapshot"] = str(product.price)
            item["line_status"] = LINE_STATUS_REPLACED
            item.pop("proposed", None)
            items[idx] = item
            new_meta = set_meta_items(dict(order.meta or {}), items)
            order.meta = new_meta
            order.total_amount = total_from_meta_items(items)
            await session.commit()
            if bot is not None:
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=(
                        f"По заказу #{order.id} позиция #{idx + 1} заменена на "
                        f"«{product.name}» ({product.price} ₽). Сумма пересчитана."
                    ),
                )
            return red(ok="1")

        item["line_status"] = LINE_STATUS_AWAITING_CUSTOMER
        item["proposed"] = {
            "product_id": product.id,
            "name": product.name,
            "price_snapshot": str(product.price),
        }
        items[idx] = item
        new_meta = set_meta_items(dict(order.meta or {}), items)
        order.meta = new_meta
        order.status = OrderStatus.PENDING_CUSTOMER_SUBSTITUTION
        await session.commit()
        if bot is not None:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=(
                    f"По заказу #{order.id} предложена замена позиции #{idx + 1} на "
                    f"«{product.name}». Откройте мини-приложение и подтвердите или "
                    "отклоните замены."
                ),
            )
        return red(ok="1")

    return red(err="bad_action")
