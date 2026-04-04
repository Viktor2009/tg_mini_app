"""Клавиатуры Telegram, общие для бота и HTTP-обработчиков."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def payment_reply_markup(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Наличные",
                    callback_data=f"pay:{order_id}:cash",
                ),
                InlineKeyboardButton(
                    text="Карта",
                    callback_data=f"pay:{order_id}:card",
                ),
            ]
        ]
    )


def operator_handoff_delivery_markup(order_id: int) -> InlineKeyboardMarkup:
    """Одно нажатие вместо /ship N после того, как заказ стал ACTIVE."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Передан в доставку",
                    callback_data=f"opship:{order_id}",
                ),
            ]
        ],
    )
