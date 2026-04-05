"""Статусы заказа и проверки допустимости действий (бот + API)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_mini_app.db import models

# Ключ meta: кто вводит текст правок (переживает перезапуск бота).
META_CHANGE_TEXT_EDITOR_TG_ID = "change_text_editor_tg_id"


class OrderStatus:
    """Строковые значения order.status и допустимые смыслы переходов."""

    PENDING_OPERATOR = "pending_operator"
    PENDING_OPERATOR_CHANGE_TEXT = "pending_operator_change_text"
    PENDING_CUSTOMER_CHANGE_ACCEPT = "pending_customer_change_accept"
    AWAITING_PAYMENT = "awaiting_payment"
    REJECTED_BY_OPERATOR = "rejected_by_operator"
    REJECTED_BY_CUSTOMER = "rejected_by_customer"
    CANCELLED_BY_CUSTOMER = "cancelled_by_customer"
    ACTIVE = "active"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED_BY_OPERATOR = "cancelled_by_operator"
    PENDING_CUSTOMER_SUBSTITUTION = "pending_customer_substitution"


MSG_STALE_ORDER = "Заказ уже обработан или в другом состоянии."
MSG_NOT_OPERATOR = "Это действие доступно только оператору."
MSG_PAYMENT_ALREADY_DONE = "Оплата по этому заказу уже оформлена."


def require_operator_identity(
    user_id: int,
    configured_operator_chat_id: int | None,
) -> str | None:
    """Если в .env задан OPERATOR_CHAT_ID — только он может жать кнопки заказа."""
    if configured_operator_chat_id is None:
        return None
    if user_id != configured_operator_chat_id:
        return MSG_NOT_OPERATOR
    return None


def require_pending_operator_for_action(status: str) -> str | None:
    """Да / Нет / Изменить — только пока ждём решения оператора."""
    if status != OrderStatus.PENDING_OPERATOR:
        return MSG_STALE_ORDER
    return None


def require_pending_customer_change(status: str) -> str | None:
    """Кнопки клиента по правкам."""
    if status != OrderStatus.PENDING_CUSTOMER_CHANGE_ACCEPT:
        return MSG_STALE_ORDER
    return None


def require_awaiting_payment(status: str) -> str | None:
    """Выбор наличные/карта."""
    if status == OrderStatus.ACTIVE:
        return MSG_PAYMENT_ALREADY_DONE
    if status != OrderStatus.AWAITING_PAYMENT:
        return MSG_STALE_ORDER
    return None


def require_pending_operator_for_cancel(status: str) -> str | None:
    """Отмена клиентом до ответа оператора."""
    if status != OrderStatus.PENDING_OPERATOR:
        return MSG_STALE_ORDER
    return None


def require_active_for_ship(status: str) -> str | None:
    """Команда оператора: передан в доставку (статус out_for_delivery)."""
    if status != OrderStatus.ACTIVE:
        return MSG_STALE_ORDER
    return None


def require_active_or_shipping_for_delivered(status: str) -> str | None:
    """Команда оператора: вручён клиенту."""
    if status not in (OrderStatus.ACTIVE, OrderStatus.OUT_FOR_DELIVERY):
        return MSG_STALE_ORDER
    return None


def require_out_for_delivery_for_courier_delivered(status: str) -> str | None:
    """Доставщик: только после передачи в доставку."""
    if status != OrderStatus.OUT_FOR_DELIVERY:
        return MSG_STALE_ORDER
    return None


def require_operator_cancel_order(status: str) -> str | None:
    """Отмена оператором до финала."""
    if status in (
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED_BY_CUSTOMER,
        OrderStatus.CANCELLED_BY_OPERATOR,
        OrderStatus.REJECTED_BY_OPERATOR,
        OrderStatus.REJECTED_BY_CUSTOMER,
    ):
        return MSG_STALE_ORDER
    return None


def require_pending_customer_substitution(status: str) -> str | None:
    """Ответ клиента по предложенным заменам позиций."""
    if status != OrderStatus.PENDING_CUSTOMER_SUBSTITUTION:
        return MSG_STALE_ORDER
    return None


async def unlock_cart_if_locked(session: AsyncSession, cart_id: str) -> None:
    """После отмены заказа корзину снова можно менять в Mini App."""
    cart = await session.get(models.Cart, cart_id)
    if cart is not None and cart.status == "locked":
        cart.status = "open"


async def find_order_awaiting_change_text(
    session: AsyncSession,
    operator_tg_id: int,
) -> models.Order | None:
    """
    Заказ, по которому оператор нажал «Изменить» и должен ввести текст.
    Хранится в БД (meta), без in-memory словаря.
    """
    result = await session.execute(
        select(models.Order).where(
            models.Order.status == OrderStatus.PENDING_OPERATOR_CHANGE_TEXT,
        )
    )
    orders = result.scalars().all()
    matches = [
        o
        for o in orders
        if int(o.meta.get(META_CHANGE_TEXT_EDITOR_TG_ID) or 0) == operator_tg_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda o: o.id)
