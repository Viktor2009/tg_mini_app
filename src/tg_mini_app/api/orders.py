from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_mini_app.api.customer_identity import (
    assert_cart_mutation_allowed,
    resolve_customer_tg_id,
)
from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.schemas import (
    OrderCreateRequest,
    OrderLineItemResponse,
    OrderResponse,
)
from tg_mini_app.db import models
from tg_mini_app.order_flow import (
    OrderStatus,
    require_pending_customer_substitution,
    require_pending_operator_for_cancel,
    unlock_cart_if_locked,
)
from tg_mini_app.order_meta import (
    LINE_STATUS_AWAITING_CUSTOMER,
    LINE_STATUS_OK,
    META_COURIER_CASH_RECEIVED,
    META_COURIER_CASH_RECEIVED_AT,
    META_COURIER_DELIVERED_AT,
    line_has_awaiting_customer,
    meta_items,
    normalize_line,
    set_meta_items,
    total_from_meta_items,
)
from tg_mini_app.settings import get_settings

router = APIRouter(prefix="/orders", tags=["orders"])


async def _load_cart_for_order(session: AsyncSession, cart_id: str) -> models.Cart:
    cart = (
        await session.execute(
            select(models.Cart)
            .where(models.Cart.id == cart_id)
            .options(selectinload(models.Cart.items).selectinload(models.CartItem.product))
        )
    ).scalar_one_or_none()
    if cart is None:
        raise HTTPException(status_code=404, detail="Корзина не найдена")
    return cart


def _calc_total(cart: models.Cart) -> Decimal:
    total = Decimal("0")
    for it in cart.items:
        total += Decimal(it.price_snapshot) * it.qty
    return total


def _order_to_response(order: models.Order) -> OrderResponse:
    meta = dict(order.meta or {})
    raw_items = meta_items(meta)
    lines: list[OrderLineItemResponse] = []
    for it in raw_items:
        row = normalize_line(it)
        prop_raw = row.get("proposed")
        prop: dict[str, Any] = prop_raw if isinstance(prop_raw, dict) else {}
        proposed_price: Decimal | None = None
        pp = prop.get("price_snapshot")
        if pp is not None and pp != "":
            try:
                proposed_price = Decimal(str(pp))
            except (TypeError, ValueError, ArithmeticError):
                proposed_price = None
        prop_pid = prop.get("product_id")
        proposed_product_id: int | None = None
        if prop_pid is not None:
            try:
                proposed_product_id = int(prop_pid)
            except (TypeError, ValueError):
                proposed_product_id = None
        prop_name = prop.get("name")
        try:
            pid = int(row.get("product_id", 0))
        except (TypeError, ValueError):
            pid = 0
        try:
            qty = int(row.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        try:
            unit = Decimal(str(row.get("price_snapshot", "0")))
        except (TypeError, ValueError, ArithmeticError):
            unit = Decimal("0")
        lines.append(
            OrderLineItemResponse(
                product_id=pid,
                name=str(row.get("name", "")),
                qty=qty,
                price=unit,
                line_status=str(row.get("line_status", LINE_STATUS_OK)),
                proposed_product_id=proposed_product_id,
                proposed_name=str(prop_name) if prop_name is not None else None,
                proposed_price=proposed_price,
            )
        )
    route_raw = meta.get("delivery_route")
    delivery_route: str | None = None
    if route_raw is not None and str(route_raw).strip():
        delivery_route = str(route_raw).strip()
    payment_received_confirmed = bool(meta.get("payment_received_confirmed"))
    courier_cash = bool(meta.get(META_COURIER_CASH_RECEIVED))
    ccr_at = meta.get(META_COURIER_CASH_RECEIVED_AT)
    courier_cash_at: str | None = str(ccr_at).strip() if ccr_at else None
    cd_at = meta.get(META_COURIER_DELIVERED_AT)
    courier_delivered_at: str | None = str(cd_at).strip() if cd_at else None

    return OrderResponse(
        id=order.id,
        cart_id=order.cart_id,
        customer_tg_id=order.customer_tg_id,
        address=order.address,
        delivery_time=order.delivery_time,
        customer_comment=order.customer_comment,
        status=order.status,
        payment_type=order.payment_type,
        total_amount=Decimal(order.total_amount),
        items=lines,
        delivery_route=delivery_route,
        payment_received_confirmed=payment_received_confirmed,
        courier_cash_received=courier_cash,
        courier_cash_received_at=courier_cash_at,
        courier_delivered_at=courier_delivered_at,
    )


async def _notify_operator_if_possible(
    request: Request,
    order: models.Order,
    cart: models.Cart,
) -> None:
    bot: Bot | None = getattr(request.app.state, "bot", None)
    if bot is None:
        return

    settings = get_settings()
    operator_chat_id = settings.operator_chat_id
    operator_username = settings.operator_username.strip()
    if operator_chat_id is None and not operator_username:
        return

    items_lines: list[str] = []
    for it in cart.items:
        items_lines.append(f"- {it.product.name} × {it.qty}")

    text = (
        "Новый заказ на согласование\n\n"
        f"Заказ #{order.id}\n"
        f"Адрес: {order.address}\n"
        f"Время: {order.delivery_time}\n"
        f"Сумма: {order.total_amount} ₽\n\n"
        "Состав:\n"
        + "\n".join(items_lines)
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"order:{order.id}:approve"),
                InlineKeyboardButton(text="Нет", callback_data=f"order:{order.id}:reject"),
                InlineKeyboardButton(text="Изменить", callback_data=f"order:{order.id}:change"),
            ]
        ]
    )

    chat_id: int | str = operator_chat_id if operator_chat_id is not None else operator_username
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    except Exception as e:
        # Не прерываем создание заказа, но фиксируем ошибку для диагностики.
        # В проде здесь будет нормальный логгер.
        meta = dict(order.meta)
        meta["operator_notify_error"] = repr(e)
        order.meta = meta
        request.state._operator_notify_error = repr(e)


async def _notify_operator_text(request: Request, text: str) -> None:
    bot: Bot | None = getattr(request.app.state, "bot", None)
    if bot is None:
        return
    settings = get_settings()
    operator_chat_id = settings.operator_chat_id
    operator_username = settings.operator_username.strip()
    if operator_chat_id is None and not operator_username:
        return
    chat_id: int | str = operator_chat_id if operator_chat_id is not None else operator_username
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        pass


@router.post("", response_model=OrderResponse)
async def create_order(
    payload: OrderCreateRequest,
    request: Request,
    x_telegram_init_data: Annotated[
        str | None,
        Header(alias="X-Telegram-Init-Data"),
    ] = None,
    session: AsyncSession = Depends(get_db_session),
) -> OrderResponse:
    cart = await _load_cart_for_order(session, payload.cart_id)
    if cart.status != "open":
        raise HTTPException(status_code=409, detail="Корзина закрыта")
    if not cart.items:
        raise HTTPException(status_code=409, detail="Корзина пуста")

    settings = get_settings()
    raw_init = (
        (x_telegram_init_data or payload.init_data or "").strip() or None
    )
    customer_tg_id = resolve_customer_tg_id(
        raw_init,
        payload.customer_tg_id,
        settings=settings,
    )
    assert_cart_mutation_allowed(cart.owner_tg_id, customer_tg_id)

    total = _calc_total(cart)
    meta_items: list[dict[str, Any]] = [
        {
            "product_id": it.product_id,
            "name": it.product.name,
            "qty": it.qty,
            "price_snapshot": str(it.price_snapshot),
        }
        for it in cart.items
    ]

    order = models.Order(
        cart_id=cart.id,
        customer_tg_id=customer_tg_id,
        address=payload.address,
        delivery_time=payload.delivery_time,
        customer_comment=payload.customer_comment,
        status=OrderStatus.PENDING_OPERATOR,
        payment_type="",
        total_amount=total,
        meta={"items": meta_items},
    )
    session.add(order)

    cart.status = "locked"
    await session.commit()
    await session.refresh(order)

    await _notify_operator_if_possible(request, order, cart)
    await session.commit()
    return _order_to_response(order)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order_for_customer(
    order_id: int,
    session: AsyncSession = Depends(get_db_session),
    init_data: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    customer_tg_id: Annotated[int | None, Query()] = None,
) -> OrderResponse:
    """
    Статус заказа для клиента Mini App.
    init_data можно передать в query или в заголовке (длинные строки — лучше заголовок).
    """
    raw_init = (x_telegram_init_data or init_data or "").strip() or None
    settings = get_settings()
    tg_id = resolve_customer_tg_id(raw_init, customer_tg_id, settings=settings)

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.customer_tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому заказу")

    return _order_to_response(order)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order_by_customer(
    order_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    init_data: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    customer_tg_id: Annotated[int | None, Query()] = None,
) -> OrderResponse:
    """Отмена до ответа оператора (статус pending_operator)."""
    raw_init = (x_telegram_init_data or init_data or "").strip() or None
    settings = get_settings()
    tg_id = resolve_customer_tg_id(raw_init, customer_tg_id, settings=settings)

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.customer_tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому заказу")

    cancel_err = require_pending_operator_for_cancel(order.status)
    if cancel_err is not None:
        raise HTTPException(status_code=409, detail=cancel_err)

    order.status = OrderStatus.CANCELLED_BY_CUSTOMER
    await unlock_cart_if_locked(session, order.cart_id)
    await session.commit()

    await _notify_operator_text(
        request,
        f"Клиент отменил заказ #{order.id} до согласования.",
    )
    return _order_to_response(order)


def _apply_accepted_substitutions(items: list[dict[str, Any]]) -> None:
    for it in items:
        if it.get("line_status") != LINE_STATUS_AWAITING_CUSTOMER:
            continue
        prop_raw = it.get("proposed")
        if not isinstance(prop_raw, dict):
            raise ValueError("Нет предложенной замены для позиции")
        try:
            npid = int(prop_raw["product_id"])
            nname = str(prop_raw["name"])
            nprice = str(prop_raw["price_snapshot"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError("Некорректные данные замены") from e
        it["product_id"] = npid
        it["name"] = nname
        it["price_snapshot"] = nprice
        it.pop("proposed", None)
        it["line_status"] = LINE_STATUS_OK


@router.post("/{order_id}/substitutions/accept", response_model=OrderResponse)
async def accept_substitutions(
    order_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    init_data: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    customer_tg_id: Annotated[int | None, Query()] = None,
) -> OrderResponse:
    """Клиент подтверждает предложенные оператором замены позиций."""
    raw_init = (x_telegram_init_data or init_data or "").strip() or None
    settings = get_settings()
    tg_id = resolve_customer_tg_id(raw_init, customer_tg_id, settings=settings)

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.customer_tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому заказу")

    err = require_pending_customer_substitution(order.status)
    if err is not None:
        raise HTTPException(status_code=409, detail=err)

    items = meta_items(dict(order.meta or {}))
    if not line_has_awaiting_customer(items):
        raise HTTPException(
            status_code=409,
            detail="Нет позиций, ожидающих подтверждения замены.",
        )
    try:
        _apply_accepted_substitutions(items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    new_meta = set_meta_items(dict(order.meta or {}), items)
    order.meta = new_meta
    order.total_amount = total_from_meta_items(items)
    order.status = OrderStatus.PENDING_OPERATOR
    await session.commit()

    await _notify_operator_text(
        request,
        f"Клиент принял замены по заказу #{order.id}. Нужно снова согласовать заказ.",
    )
    return _order_to_response(order)


@router.post("/{order_id}/substitutions/reject", response_model=OrderResponse)
async def reject_substitutions(
    order_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    init_data: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    customer_tg_id: Annotated[int | None, Query()] = None,
) -> OrderResponse:
    """Клиент отклоняет предложенные замены — заказ отменяется."""
    raw_init = (x_telegram_init_data or init_data or "").strip() or None
    settings = get_settings()
    tg_id = resolve_customer_tg_id(raw_init, customer_tg_id, settings=settings)

    order = (
        await session.execute(select(models.Order).where(models.Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.customer_tg_id != tg_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому заказу")

    err = require_pending_customer_substitution(order.status)
    if err is not None:
        raise HTTPException(status_code=409, detail=err)

    meta = dict(order.meta or {})
    meta["substitution_rejected_by_customer"] = True
    order.meta = meta
    order.status = OrderStatus.CANCELLED_BY_CUSTOMER
    await unlock_cart_if_locked(session, order.cart_id)
    await session.commit()

    await _notify_operator_text(
        request,
        f"Клиент отклонил замены по заказу #{order.id}. Заказ отменён.",
    )
    return _order_to_response(order)

