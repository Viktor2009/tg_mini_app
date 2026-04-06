from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_mini_app.api.customer_identity import (
    assert_cart_mutation_allowed,
    resolve_customer_tg_id,
)
from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.schemas import (
    CartCreateRequest,
    CartItemChangeRequest,
    CartItemResponse,
    CartResponse,
    ShareResponse,
)
from tg_mini_app.db import models
from tg_mini_app.settings import Settings, get_settings

router = APIRouter(prefix="/cart", tags=["cart"])


def _cart_to_response(cart: models.Cart) -> CartResponse:
    items: list[CartItemResponse] = []
    total = Decimal("0")
    for it in cart.items:
        price = Decimal(it.price_snapshot)
        subtotal = price * it.qty
        total += subtotal
        items.append(
            CartItemResponse(
                product_id=it.product_id,
                name=it.product.name,
                qty=it.qty,
                price=price,
                subtotal=subtotal,
            )
        )

    return CartResponse(
        id=cart.id,
        shared_key=cart.shared_key,
        status=cart.status,
        items=items,
        total=total,
    )


async def _get_cart_or_404(session: AsyncSession, cart_id: str) -> models.Cart:
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


def _resolve_cart_caller_tg_id(
    init_data: str | None,
    header_init: str | None,
    customer_tg_id: int | None,
    *,
    settings: Settings,
) -> int:
    raw = (header_init or init_data or "").strip() or None
    return resolve_customer_tg_id(raw, customer_tg_id, settings=settings)


@router.post("", response_model=CartResponse)
async def create_cart(
    payload: CartCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CartResponse:
    cart = models.Cart(owner_tg_id=payload.owner_tg_id, status="open")
    session.add(cart)
    await session.commit()
    await session.refresh(cart)
    cart = await _get_cart_or_404(session, cart.id)
    return _cart_to_response(cart)


@router.get("/{cart_id}", response_model=CartResponse)
async def get_cart(
    cart_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> CartResponse:
    cart = await _get_cart_or_404(session, cart_id)
    return _cart_to_response(cart)


@router.get("/by-share/{shared_key}", response_model=CartResponse)
async def get_cart_by_share(
    shared_key: str,
    session: AsyncSession = Depends(get_db_session),
) -> CartResponse:
    cart = (
        await session.execute(
            select(models.Cart)
            .where(models.Cart.shared_key == shared_key)
            .options(selectinload(models.Cart.items).selectinload(models.CartItem.product))
        )
    ).scalar_one_or_none()
    if cart is None:
        raise HTTPException(status_code=404, detail="Корзина не найдена")
    return _cart_to_response(cart)


@router.post("/{cart_id}/items", response_model=CartResponse)
async def change_item(
    cart_id: str,
    payload: CartItemChangeRequest,
    session: AsyncSession = Depends(get_db_session),
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> CartResponse:
    cart = await _get_cart_or_404(session, cart_id)
    if cart.status != "open":
        raise HTTPException(status_code=409, detail="Корзина закрыта для изменений")

    settings = get_settings()
    if cart.owner_tg_id is not None:
        caller = _resolve_cart_caller_tg_id(
            payload.init_data,
            x_telegram_init_data,
            payload.customer_tg_id,
            settings=settings,
        )
        assert_cart_mutation_allowed(cart.owner_tg_id, caller)

    product = (
        await session.execute(
            select(models.Product).where(models.Product.id == payload.product_id)
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if not product.is_available:
        raise HTTPException(status_code=409, detail="Товар недоступен")
    if product.stock_quantity is not None and product.stock_quantity <= 0:
        raise HTTPException(status_code=409, detail="Товар недоступен")

    item = (
        await session.execute(
            select(models.CartItem).where(
                models.CartItem.cart_id == cart_id,
                models.CartItem.product_id == payload.product_id,
            )
        )
    ).scalar_one_or_none()

    if item is None:
        if payload.qty_delta <= 0:
            return _cart_to_response(cart)
        if (
            product.stock_quantity is not None
            and payload.qty_delta > product.stock_quantity
        ):
            raise HTTPException(
                status_code=409,
                detail="Недостаточно товара на складе",
            )
        session.add(
            models.CartItem(
                cart_id=cart_id,
                product_id=payload.product_id,
                qty=payload.qty_delta,
                price_snapshot=product.price,
            )
        )
    else:
        new_qty = item.qty + payload.qty_delta
        if new_qty <= 0:
            await session.execute(
                delete(models.CartItem).where(models.CartItem.id == item.id)
            )
        else:
            if (
                product.stock_quantity is not None
                and new_qty > product.stock_quantity
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Недостаточно товара на складе",
                )
            item.qty = new_qty

    await session.commit()
    cart = await _get_cart_or_404(session, cart_id)
    return _cart_to_response(cart)


@router.delete("/{cart_id}/items/{product_id}", response_model=CartResponse)
async def delete_item(
    cart_id: str,
    product_id: int,
    session: AsyncSession = Depends(get_db_session),
    init_data: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    customer_tg_id: Annotated[int | None, Query()] = None,
) -> CartResponse:
    cart = await _get_cart_or_404(session, cart_id)
    if cart.status != "open":
        raise HTTPException(status_code=409, detail="Корзина закрыта для изменений")

    settings = get_settings()
    if cart.owner_tg_id is not None:
        caller = _resolve_cart_caller_tg_id(
            init_data,
            x_telegram_init_data,
            customer_tg_id,
            settings=settings,
        )
        assert_cart_mutation_allowed(cart.owner_tg_id, caller)

    await session.execute(
        delete(models.CartItem).where(
            models.CartItem.cart_id == cart_id,
            models.CartItem.product_id == product_id,
        )
    )
    await session.commit()
    cart = await _get_cart_or_404(session, cart_id)
    return _cart_to_response(cart)


@router.get("/{cart_id}/share", response_model=ShareResponse)
async def get_share_key(
    cart_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ShareResponse:
    cart = await _get_cart_or_404(session, cart_id)
    return ShareResponse(shared_key=cart.shared_key)
