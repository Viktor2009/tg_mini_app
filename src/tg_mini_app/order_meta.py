"""Позиции заказа в order.meta[\"items\"] и пересчёт суммы."""

from __future__ import annotations

# Отметки курьера (та же строка заказа в БД — видит оператор в панели).
META_COURIER_CASH_RECEIVED = "courier_cash_received"
META_COURIER_CASH_RECEIVED_AT = "courier_cash_received_at"
META_COURIER_DELIVERED_AT = "courier_delivered_at"

from decimal import Decimal
from typing import Any

LINE_STATUS_OK = "ok"
LINE_STATUS_REPLACED = "replaced"
LINE_STATUS_AWAITING_CUSTOMER = "awaiting_customer"


def meta_items(order_meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = order_meta.get("items")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if isinstance(it, dict):
            out.append(it)
    return out


def set_meta_items(order_meta: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    m = dict(order_meta)
    m["items"] = items
    return m


def total_from_meta_items(items: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for it in items:
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        price_raw = it.get("price_snapshot", "0")
        try:
            price = Decimal(str(price_raw))
        except (TypeError, ValueError, ArithmeticError):
            price = Decimal("0")
        total += price * qty
    return total


def line_has_awaiting_customer(items: list[dict[str, Any]]) -> bool:
    return any(it.get("line_status") == LINE_STATUS_AWAITING_CUSTOMER for it in items)


def normalize_line(it: dict[str, Any]) -> dict[str, Any]:
    """Гарантирует line_status для отображения клиенту."""
    row = dict(it)
    if "line_status" not in row:
        row["line_status"] = LINE_STATUS_OK
    return row
