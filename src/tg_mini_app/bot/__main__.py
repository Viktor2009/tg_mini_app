from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from sqlalchemy import select

from tg_mini_app.bot.db import Db
from tg_mini_app.db import models
from tg_mini_app.order_flow import (
    META_CHANGE_TEXT_EDITOR_TG_ID,
    OrderStatus,
    find_order_awaiting_change_text,
    require_active_for_ship,
    require_active_or_shipping_for_delivered,
    require_awaiting_payment,
    require_operator_identity,
    require_pending_customer_change,
    require_pending_operator_for_action,
    unlock_cart_if_locked,
)
from tg_mini_app.paths import PROJECT_ROOT
from tg_mini_app.settings import Settings, get_settings
from tg_mini_app.telegram_keyboards import (
    operator_handoff_delivery_markup,
    payment_reply_markup,
)


def _resolve_operator_notify_chat_id(
    meta: dict[str, Any],
    configured_operator_chat_id: int | None,
) -> int:
    """
    Куда слать служебные сообщения оператору.

    Сначала id из meta (кто согласовал в боте), иначе OPERATOR_CHAT_ID из .env —
    иначе после согласования через веб-панель без записи в meta кнопка
    «Передан в доставку» не доходила.
    """
    meta_op = int(meta.get("operator_chat_id") or 0)
    if meta_op:
        return meta_op
    if configured_operator_chat_id is not None:
        return configured_operator_chat_id
    return 0


async def _configure_menu_and_commands(bot: Bot, settings: Settings) -> None:
    """
    Кнопка «меню» у поля ввода (как у многих Mini App-ботов) и подсказки /команд.

    См. https://core.telegram.org/bots/api#setchatmenubutton
    """

    webapp_url = f"{settings.base_url.rstrip('/')}/webapp"
    if webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Меню",
                web_app=WebAppInfo(url=webapp_url),
            )
        )

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть меню заказа"),
            BotCommand(command="help", description="Справка по командам"),
            BotCommand(command="app", description="Ссылка на Mini App"),
            BotCommand(command="operator", description="Контакт оператора"),
            BotCommand(command="id", description="Мой Telegram ID"),
        ]
    )
    if settings.operator_chat_id is not None:
        await bot.set_my_commands(
            [
                BotCommand(
                    command="ship",
                    description="Передан в доставку: /ship N",
                ),
                BotCommand(
                    command="delivery",
                    description="То же: /delivery N",
                ),
                BotCommand(
                    command="delivered",
                    description="Вручён клиенту: /delivered N",
                ),
            ],
            scope=BotCommandScopeChat(chat_id=settings.operator_chat_id),
        )


def _require_token(token: str) -> str:
    if not token.strip():
        raise RuntimeError(
            "BOT_TOKEN пустой. Создайте .env (copy .env.example .env) и "
            "заполните BOT_TOKEN реальным токеном."
        )
    return token


def _acquire_singleton_lock() -> None:
    """
    Гарантируем, что polling бота не запустится в двух экземплярах.

    Telegram polling при дублях приводит к повторной обработке апдейтов.
    """

    lock_path = PROJECT_ROOT / ".tg_mini_app.bot.lock"
    pid = os.getpid()
    ttl_sec = 60

    while True:
        try:
            # O_EXCL гарантирует атомарный захват.
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(pid))
            return
        except FileExistsError:
            # Lock уже есть — считаем его протухшим по возрасту файла.
            try:
                age_sec = time.time() - lock_path.stat().st_mtime
            except OSError:
                age_sec = ttl_sec + 1

            if age_sec <= ttl_sec:
                # Другой экземпляр скорее всего жив — не стартуем.
                raise SystemExit(0) from None

            # Stale lock: удалим и повторим попытку.
            try:
                os.remove(lock_path)
            except OSError:
                pass


def _kb_customer_accept_changes(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data=f"cust:{order_id}:accept_change",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=f"cust:{order_id}:reject_change",
                ),
            ]
        ]
    )


async def main() -> None:
    _acquire_singleton_lock()
    settings = get_settings()
    bot = Bot(token=_require_token(settings.bot_token))
    await _configure_menu_and_commands(bot, settings)
    dp = Dispatcher()
    db = Db()

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        webapp_url = f"{settings.base_url.rstrip('/')}/webapp"
        if webapp_url.startswith("https://"):
            kb = ReplyKeyboardMarkup(
                keyboard=[
                    [
                        KeyboardButton(
                            text="Открыть меню",
                            web_app=WebAppInfo(url=webapp_url),
                        )
                    ]
                ],
                resize_keyboard=True,
                is_persistent=True,
            )
            inline_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Открыть меню (inline)",
                            web_app=WebAppInfo(url=webapp_url),
                        )
                    ]
                ]
            )
            await message.answer(
                "Привет! Это бот заказа суши.\n"
                "Откройте меню, чтобы сделать заказ.\n"
                "Если Mini App не передаёт данные, используйте кнопку inline ниже.",
                reply_markup=kb,
            )
            await message.answer("Открыть Mini App:", reply_markup=inline_kb)
        else:
            await message.answer(
                "Привет! Это бот заказа суши.\n\n"
                "Чтобы открыть Mini App, нужен HTTPS-адрес.\n"
                f"Сейчас BASE_URL = {settings.base_url}\n\n"
                "Сделайте туннель (ngrok/cloudflared) и укажите HTTPS в BASE_URL, "
                "после этого кнопка Mini App появится.\n\n"
                f"Текущая ссылка (в Telegram WebApp не откроется): {webapp_url}"
            )

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "Команды:\n"
            "/start — начать\n"
            "/help — помощь\n"
            "/operator — куда уходят согласования\n"
            "/app — ссылка на Mini App\n\n"
            "Оператор (после оплаты заказа):\n"
            "кнопка «Передан в доставку» в чате или\n"
            "/ship N и /delivery N — передан в доставку\n"
            "/delivered N — вручён клиенту\n\n"
            f"Веб-панель: {settings.base_url.rstrip('/')}/operator-panel\n"
            "(логин operator, пароль OPERATOR_PANEL_TOKEN из .env)"
        )

    @dp.message(Command("operator"))
    async def operator_cmd(message: Message) -> None:
        await message.answer(f"Оператор: {settings.operator_username}")

    @dp.message(Command("id"))
    async def id_cmd(message: Message) -> None:
        if message.from_user is None:
            return
        await message.answer(f"Ваш Telegram ID: {message.from_user.id}")

    @dp.message(Command("app"))
    async def app_cmd(message: Message) -> None:
        webapp_url = f"{settings.base_url.rstrip('/')}/webapp"
        await message.answer(f"Mini App: {webapp_url}")

    @dp.message(Command("ship"))
    @dp.message(Command("delivery"))
    async def cmd_ship_order(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        deny = require_operator_identity(
            message.from_user.id,
            settings.operator_chat_id,
        )
        if deny is not None:
            await message.answer(deny)
            return
        parts = (command.args or "").strip().split()
        if len(parts) < 1:
            await message.answer("Укажите номер заказа: /ship 5 или /delivery 5")
            return
        try:
            oid = int(parts[0])
        except ValueError:
            await message.answer("Номер заказа должен быть числом.")
            return

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == oid))
            ).scalar_one_or_none()
            if order is None:
                await message.answer("Заказ не найден.")
                return
            err = require_active_for_ship(order.status)
            if err is not None:
                await message.answer(f"Нельзя: {err}")
                return
            customer_chat = order.customer_tg_id
            order.status = OrderStatus.OUT_FOR_DELIVERY
            await session.commit()

        await message.answer(f"Заказ #{oid} отмечен: передан в доставку.")
        await bot.send_message(
            chat_id=customer_chat,
            text=(
                f"Заказ #{oid} передан в доставку. Ожидайте курьера."
            ),
        )

    @dp.callback_query(F.data.startswith("opship:"))
    async def operator_handoff_delivery_cb(query: CallbackQuery) -> None:
        if query.data is None or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 2:
            await query.answer("Некорректные данные кнопки", show_alert=True)
            return
        try:
            oid = int(parts[1])
        except ValueError:
            await query.answer("Некорректный номер заказа", show_alert=True)
            return

        deny = require_operator_identity(
            query.from_user.id,
            settings.operator_chat_id,
        )
        if deny is not None:
            await query.answer(deny, show_alert=True)
            return

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == oid))
            ).scalar_one_or_none()
            if order is None:
                await query.answer("Заказ не найден", show_alert=True)
                return
            err = require_active_for_ship(order.status)
            if err is not None:
                await query.answer(f"Нельзя: {err}", show_alert=True)
                return
            customer_chat = order.customer_tg_id
            order.status = OrderStatus.OUT_FOR_DELIVERY
            await session.commit()

        await query.answer("Передан в доставку")
        if query.message is not None:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        await bot.send_message(
            chat_id=customer_chat,
            text=(
                f"Заказ #{oid} передан в доставку. Ожидайте курьера."
            ),
        )

    @dp.message(Command("delivered"))
    async def cmd_delivered_order(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        deny = require_operator_identity(
            message.from_user.id,
            settings.operator_chat_id,
        )
        if deny is not None:
            await message.answer(deny)
            return
        parts = (command.args or "").strip().split()
        if len(parts) < 1:
            await message.answer("Укажите номер заказа: /delivered 5")
            return
        try:
            oid = int(parts[0])
        except ValueError:
            await message.answer("Номер заказа должен быть числом.")
            return

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == oid))
            ).scalar_one_or_none()
            if order is None:
                await message.answer("Заказ не найден.")
                return
            err = require_active_or_shipping_for_delivered(order.status)
            if err is not None:
                await message.answer(f"Нельзя: {err}")
                return
            customer_chat = order.customer_tg_id
            order.status = OrderStatus.DELIVERED
            await session.commit()

        await message.answer(f"Заказ #{oid} отмечен как доставленный.")
        await bot.send_message(
            chat_id=customer_chat,
            text=f"Заказ #{oid} доставлен. Приятного аппетита!",
        )

    @dp.callback_query(F.data.startswith("order:"))
    async def operator_order_action(query: CallbackQuery) -> None:
        if query.data is None or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Некорректные данные кнопки", show_alert=True)
            return

        order_id = int(parts[1])
        action = parts[2]

        deny = require_operator_identity(
            query.from_user.id,
            settings.operator_chat_id,
        )
        if deny is not None:
            await query.answer(deny, show_alert=True)
            return

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == order_id))
            ).scalar_one_or_none()
            if order is None:
                await query.answer("Заказ не найден", show_alert=True)
                return

            stale = require_pending_operator_for_action(order.status)
            if stale is not None:
                await query.answer(stale, show_alert=True)
                return

            meta = dict(order.meta)
            meta["operator_chat_id"] = query.from_user.id
            order.meta = meta

            if action == "approve":
                order.status = OrderStatus.AWAITING_PAYMENT
                await session.commit()
                await query.answer("Ок, отправил клиенту выбор оплаты")
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=(
                        f"Заказ #{order.id} согласован.\n"
                        "Выберите способ оплаты:"
                    ),
                    reply_markup=payment_reply_markup(order.id),
                )
                return

            if action == "reject":
                order.status = OrderStatus.REJECTED_BY_OPERATOR
                await unlock_cart_if_locked(session, order.cart_id)
                await session.commit()
                await query.answer("Отказ отправлен клиенту")
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=f"К сожалению, заказ #{order.id} не принят.",
                )
                return

            if action == "change":
                meta = dict(order.meta)
                meta[META_CHANGE_TEXT_EDITOR_TG_ID] = query.from_user.id
                order.meta = meta
                order.status = OrderStatus.PENDING_OPERATOR_CHANGE_TEXT
                await session.commit()
                await query.answer()
                await bot.send_message(
                    chat_id=query.from_user.id,
                    text=(
                        f"Введите текст изменений для заказа #{order.id}.\n"
                        "Я отправлю его клиенту с кнопками Да/Нет."
                    ),
                )
                return

        await query.answer("Неизвестное действие", show_alert=True)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def operator_change_text_or_fallback(message: Message) -> None:
        if message.from_user is None:
            return

        deny = require_operator_identity(
            message.from_user.id,
            settings.operator_chat_id,
        )
        if deny is not None:
            await message.answer("Пока понимаю только команды. Напишите /help.")
            return

        async with db.session() as session:
            preliminary = await find_order_awaiting_change_text(
                session,
                message.from_user.id,
            )
            if preliminary is None:
                await message.answer("Пока понимаю только команды. Напишите /help.")
                return
            if preliminary.status != OrderStatus.PENDING_OPERATOR_CHANGE_TEXT:
                await message.answer("Этот запрос устарел. Откройте заказ заново.")
                return

        change_text = (message.text or "").strip()
        if not change_text:
            await message.answer("Текст изменений пустой. Попробуйте ещё раз.")
            return

        async with db.session() as session:
            order = await find_order_awaiting_change_text(session, message.from_user.id)
            if order is None:
                await message.answer("Этап ввода правок уже закрыт. Начните с кнопки «Изменить».")
                return
            if order.status != OrderStatus.PENDING_OPERATOR_CHANGE_TEXT:
                await message.answer("Этот запрос устарел.")
                return

            meta = dict(order.meta)
            meta["change_text"] = change_text
            meta["operator_chat_id"] = message.from_user.id
            meta.pop(META_CHANGE_TEXT_EDITOR_TG_ID, None)
            order.meta = meta
            order.status = OrderStatus.PENDING_CUSTOMER_CHANGE_ACCEPT
            await session.commit()

            await bot.send_message(
                chat_id=order.customer_tg_id,
                text=(
                    f"По заказу #{order.id} предлагаю изменения:\n\n"
                    f"{change_text}\n\n"
                    "Согласны?"
                ),
                reply_markup=_kb_customer_accept_changes(order.id),
            )

        await message.answer("Отправил изменения клиенту.")

    @dp.callback_query(F.data.startswith("cust:"))
    async def customer_change_decision(query: CallbackQuery) -> None:
        if query.data is None or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Некорректные данные кнопки", show_alert=True)
            return

        order_id = int(parts[1])
        action = parts[2]

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == order_id))
            ).scalar_one_or_none()
            if order is None:
                await query.answer("Заказ не найден", show_alert=True)
                return
            if query.from_user.id != order.customer_tg_id:
                await query.answer("Это не ваш заказ", show_alert=True)
                return

            stale = require_pending_customer_change(order.status)
            if stale is not None:
                await query.answer(stale, show_alert=True)
                return

            operator_chat_id = int(order.meta.get("operator_chat_id") or 0)

            if action == "accept_change":
                order.status = OrderStatus.AWAITING_PAYMENT
                await session.commit()
                await query.answer("Принято")
                if operator_chat_id:
                    await bot.send_message(
                        chat_id=operator_chat_id,
                        text=f"Клиент согласился с изменениями по заказу #{order.id}.",
                    )
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=f"Заказ #{order.id} согласован. Выберите способ оплаты:",
                    reply_markup=payment_reply_markup(order.id),
                )
                return

            if action == "reject_change":
                order.status = OrderStatus.REJECTED_BY_CUSTOMER
                await unlock_cart_if_locked(session, order.cart_id)
                await session.commit()
                await query.answer("Ок")
                if operator_chat_id:
                    await bot.send_message(
                        chat_id=operator_chat_id,
                        text=f"Клиент отказался от изменений по заказу #{order.id}.",
                    )
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=f"Понял. Заказ #{order.id} отменён.",
                )
                return

        await query.answer("Неизвестное действие", show_alert=True)

    @dp.callback_query(F.data.startswith("pay:"))
    async def payment_choice(query: CallbackQuery) -> None:
        if query.data is None or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Некорректные данные кнопки", show_alert=True)
            return

        order_id = int(parts[1])
        action = parts[2]

        async with db.session() as session:
            order = (
                await session.execute(select(models.Order).where(models.Order.id == order_id))
            ).scalar_one_or_none()
            if order is None:
                await query.answer("Заказ не найден", show_alert=True)
                return
            if query.from_user.id != order.customer_tg_id:
                await query.answer("Это не ваш заказ", show_alert=True)
                return

            pay_err = require_awaiting_payment(order.status)
            if pay_err is not None:
                await query.answer(pay_err, show_alert=True)
                return

            notify_op = _resolve_operator_notify_chat_id(
                order.meta,
                settings.operator_chat_id,
            )

            if action == "cash":
                order.payment_type = "cash"
                order.status = OrderStatus.ACTIVE
                await session.commit()
                await query.answer("Ок")
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=f"Отлично! Заказ #{order.id} активен. Оплата: наличные.",
                )
                if notify_op:
                    await bot.send_message(
                        chat_id=notify_op,
                        text=(
                            f"Заказ #{order.id}: клиент выбрал наличные.\n"
                            "Нажмите, когда передадите заказ в доставку:"
                        ),
                        reply_markup=operator_handoff_delivery_markup(order.id),
                    )
                return

            if action == "card":
                # Заглушка: имитируем успешную оплату картой (RUB).
                # Позже заменим на Telegram Payments + PROVIDER_TOKEN.
                meta = dict(order.meta)
                meta["card_payment_stub"] = True
                meta["currency"] = "RUB"
                order.meta = meta
                order.payment_type = "card"
                order.status = OrderStatus.ACTIVE
                await session.commit()
                await query.answer("Ок")
                await bot.send_message(
                    chat_id=order.customer_tg_id,
                    text=(
                        f"Оплата картой (заглушка): зачислено {order.total_amount} ₽.\n"
                        f"Заказ #{order.id} активен."
                    ),
                )
                if notify_op:
                    await bot.send_message(
                        chat_id=notify_op,
                        text=(
                            f"Заказ #{order.id}: оплата картой "
                            f"(заглушка, {order.total_amount} ₽).\n"
                            "Нажмите, когда передадите заказ в доставку:"
                        ),
                        reply_markup=operator_handoff_delivery_markup(order.id),
                    )
                return

        await query.answer("Неизвестное действие", show_alert=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
