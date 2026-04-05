"""API и HTML-панель доставщика: список заказов, наличные, доставлено."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.orders import _order_to_response
from tg_mini_app.db import models
from tg_mini_app.order_flow import OrderStatus, require_out_for_delivery_for_courier_delivered
from tg_mini_app.order_meta import (
    META_COURIER_CASH_RECEIVED,
    META_COURIER_CASH_RECEIVED_AT,
    META_COURIER_DELIVERED_AT,
)
from tg_mini_app.paths import TEMPLATES_DIR
from tg_mini_app.settings import get_settings

router = APIRouter(prefix="/delivery", tags=["delivery"])
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

COURIER_COOKIE_NAME = "courier_auth"
COURIER_COOKIE_TTL_SEC = 7 * 24 * 3600


def _courier_secret() -> str:
    return (get_settings().courier_api_token or "").strip()


def _make_courier_session_cookie(secret: str, ttl_sec: int = COURIER_COOKIE_TTL_SEC) -> str:
    exp = int(time.time()) + ttl_sec
    exp_s = str(exp)
    sig = hmac.new(
        secret.encode("utf-8"),
        exp_s.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp_s}.{sig}"


def _courier_session_cookie_ok(raw: str, secret: str) -> bool:
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


def _request_courier_cookie_ok(request: Request, secret: str) -> bool:
    raw = request.cookies.get(COURIER_COOKIE_NAME)
    if not raw:
        return False
    return _courier_session_cookie_ok(raw, secret)


def _token_matches_secret(got: str | None, secret: str) -> bool:
    if not got or not secret:
        return False
    a = got.strip().encode("utf-8")
    b = secret.encode("utf-8")
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


def _resolve_courier_auth(request: Request, token: str | None) -> None:
    secret = _courier_secret()
    if not secret:
        raise HTTPException(status_code=404, detail="API доставщика отключён")
    if _token_matches_secret(token, secret):
        return
    if _request_courier_cookie_ok(request, secret):
        return
    raise HTTPException(status_code=401, detail="Нужен token или вход через /delivery/login")


def _redirect_delivery(route: str | None, **params: str) -> RedirectResponse:
    q: dict[str, str] = {}
    if route and route.strip():
        q["route"] = route.strip()
    q.update({k: v for k, v in params.items() if v})
    url = "/delivery"
    if q:
        url += "?" + urlencode(q)
    return RedirectResponse(url=url, status_code=303)


@router.get("/ping")
async def delivery_ping() -> dict[str, Any]:
    return {
        "api": "ok",
        "courier_configured": bool(_courier_secret()),
        "ui": "/delivery/login",
    }


@router.get("/login", response_class=HTMLResponse)
async def courier_login_page(request: Request) -> Any:
    if not _courier_secret():
        raise HTTPException(status_code=404, detail="Not Found")
    secret = _courier_secret()
    if _request_courier_cookie_ok(request, secret):
        return RedirectResponse(url="/delivery", status_code=303)
    return _templates.TemplateResponse(
        request=request,
        name="courier_panel_login.html",
        context={"title": "Доставка — вход"},
    )


@router.post("/login")
async def courier_login_submit(
    request: Request,
    token: Annotated[str, Form()],
) -> Any:
    secret = _courier_secret()
    if not secret:
        raise HTTPException(status_code=404, detail="Not Found")
    pwd = (token or "").strip()
    pass_ok = _token_matches_secret(pwd, secret)
    if not pass_ok:
        return _templates.TemplateResponse(
            request=request,
            name="courier_panel_login.html",
            context={
                "title": "Доставка — вход",
                "error": "Неверный секрет.",
            },
            status_code=401,
        )
    response = RedirectResponse(url="/delivery", status_code=303)
    response.set_cookie(
        key=COURIER_COOKIE_NAME,
        value=_make_courier_session_cookie(secret),
        max_age=COURIER_COOKIE_TTL_SEC,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/delivery",
    )
    return response


@router.get("/logout")
async def courier_logout() -> RedirectResponse:
    if not _courier_secret():
        raise HTTPException(status_code=404, detail="Not Found")
    response = RedirectResponse(url="/delivery/login", status_code=303)
    response.delete_cookie(COURIER_COOKIE_NAME, path="/delivery")
    return response


async def _orders_out_for_delivery(
    session: AsyncSession,
    route: str | None,
) -> list[models.Order]:
    rows = list(
        (
            await session.execute(
                select(models.Order)
                .where(models.Order.status == OrderStatus.OUT_FOR_DELIVERY)
                .order_by(models.Order.id.asc())
                .limit(200),
            )
        )
        .scalars()
        .all(),
    )
    rfilter = (route or "").strip()
    if rfilter:
        rows = [
            o
            for o in rows
            if str((o.meta or {}).get("delivery_route", "")).strip() == rfilter
        ]
    return rows


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def courier_panel_home(
    request: Request,
    route: str | None = None,
    ok: str | None = None,
    err: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    secret = _courier_secret()
    if not secret:
        raise HTTPException(status_code=404, detail="Not Found")
    if not _request_courier_cookie_ok(request, secret):
        return RedirectResponse(url="/delivery/login", status_code=303)
    orders = await _orders_out_for_delivery(session, route)
    return _templates.TemplateResponse(
        request=request,
        name="courier_panel.html",
        context={
            "title": "Доставка",
            "orders": orders,
            "route_filter": (route or "").strip(),
            "flash_ok": ok,
            "flash_err": err,
        },
    )


@router.get("/orders")
async def delivery_list_orders(
    request: Request,
    token: Annotated[str | None, Query(description="COURIER_API_TOKEN")] = None,
    route: Annotated[str | None, Query(description="Фильтр по маршруту (delivery_route)")] = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    _resolve_courier_auth(request, token)
    rows = await _orders_out_for_delivery(session, route)
    return [_order_to_response(o).model_dump(mode="json") for o in rows]


def _apply_cash_received(order: models.Order) -> str | None:
    if order.status != OrderStatus.OUT_FOR_DELIVERY:
        return "bad_state"
    if (order.payment_type or "").strip() != "cash":
        return "not_cash"
    now = datetime.now(UTC).isoformat()
    meta = dict(order.meta or {})
    meta[META_COURIER_CASH_RECEIVED] = True
    meta[META_COURIER_CASH_RECEIVED_AT] = now
    meta["payment_received_confirmed"] = True
    order.meta = meta
    return None


async def _apply_courier_delivered(
    request: Request,
    order: models.Order,
) -> str | None:
    err = require_out_for_delivery_for_courier_delivered(order.status)
    if err is not None:
        return "bad_state"
    now = datetime.now(UTC).isoformat()
    meta = dict(order.meta or {})
    meta[META_COURIER_DELIVERED_AT] = now
    order.meta = meta
    order.status = OrderStatus.DELIVERED

    bot: Bot | None = getattr(request.app.state, "bot", None)
    if bot is not None:
        try:
            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=f"Заказ #{order.id} доставлен. Приятного аппетита!",
            )
        except TelegramBadRequest:
            pass
    return None


@router.post("/ui/orders/{order_id}/cash-received")
async def delivery_ui_cash_received(
    order_id: int,
    request: Request,
    filter_route: Annotated[str, Form()] = "",
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """HTML-форма: наличные получены (cookie)."""
    secret = _courier_secret()
    if not secret:
        raise HTTPException(status_code=404, detail="Not Found")
    if not _request_courier_cookie_ok(request, secret):
        return RedirectResponse(url="/delivery/login", status_code=303)
    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        return _redirect_delivery(filter_route or None, err="not_found")
    err_code = _apply_cash_received(order)
    if err_code is not None:
        return _redirect_delivery(filter_route or None, err=err_code)
    await session.commit()
    return _redirect_delivery(filter_route or None, ok="cash")


@router.post("/ui/orders/{order_id}/delivered")
async def delivery_ui_delivered(
    order_id: int,
    request: Request,
    filter_route: Annotated[str, Form()] = "",
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """HTML-форма: доставлено (cookie)."""
    secret = _courier_secret()
    if not secret:
        raise HTTPException(status_code=404, detail="Not Found")
    if not _request_courier_cookie_ok(request, secret):
        return RedirectResponse(url="/delivery/login", status_code=303)
    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        return _redirect_delivery(filter_route or None, err="not_found")
    err_code = await _apply_courier_delivered(request, order)
    if err_code is not None:
        return _redirect_delivery(filter_route or None, err=err_code)
    await session.commit()
    return _redirect_delivery(filter_route or None, ok="delivered")


@router.post("/orders/{order_id}/cash-received")
async def delivery_mark_cash_received_json(
    order_id: int,
    request: Request,
    token: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """JSON/M2M: наличные получены, query token."""
    _resolve_courier_auth(request, token)
    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    err_code = _apply_cash_received(order)
    if err_code == "bad_state":
        raise HTTPException(status_code=409, detail="Недопустимый статус заказа")
    if err_code == "not_cash":
        raise HTTPException(status_code=409, detail="Заказ не на наличные")
    await session.commit()
    return {"ok": True, "order": _order_to_response(order).model_dump(mode="json")}


@router.post("/orders/{order_id}/delivered")
async def delivery_mark_delivered(
    order_id: int,
    request: Request,
    token: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """JSON/M2M: доставлено, query token."""
    _resolve_courier_auth(request, token)
    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    err_code = await _apply_courier_delivered(request, order)
    if err_code is not None:
        raise HTTPException(status_code=409, detail="Недопустимый статус для доставки")
    await session.commit()
    return {
        "ok": True,
        "order": _order_to_response(order).model_dump(mode="json"),
    }
