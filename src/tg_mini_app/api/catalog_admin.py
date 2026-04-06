"""CRUD категорий и товаров (HTTP Basic / cookie панели оператора)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_mini_app.api.catalog_serialize import product_to_dict
from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.operator_panel import require_operator_panel_auth
from tg_mini_app.api.schemas import (
    CategoryCreateBody,
    CategoryPatchBody,
    ProductCreateBody,
    ProductPatchBody,
)
from tg_mini_app.db import models

router = APIRouter(prefix="/operator-panel/catalog", tags=["operator-panel-catalog"])


async def _category_or_404(session: AsyncSession, category_id: int) -> models.Category:
    c = (
        await session.execute(
            select(models.Category).where(models.Category.id == category_id),
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return c


async def _product_loaded(session: AsyncSession, product_id: int) -> models.Product | None:
    return (
        await session.execute(
            select(models.Product)
            .where(models.Product.id == product_id)
            .options(
                selectinload(models.Product.attributes),
                selectinload(models.Product.images),
            ),
        )
    ).scalar_one_or_none()


@router.get("/categories")
async def admin_list_categories(
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(models.Category).order_by(
                models.Category.sort_order,
                models.Category.id,
            ),
        )
    ).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "sort_order": c.sort_order,
            "is_active": c.is_active,
        }
        for c in rows
    ]


@router.post("/categories")
async def admin_create_category(
    body: CategoryCreateBody,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    c = models.Category(
        name=body.name.strip(),
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    session.add(c)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Категория с таким именем уже существует",
        ) from None
    await session.refresh(c)
    return {
        "id": c.id,
        "name": c.name,
        "sort_order": c.sort_order,
        "is_active": c.is_active,
    }


@router.patch("/categories/{category_id}")
async def admin_patch_category(
    category_id: int,
    body: CategoryPatchBody,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    c = await _category_or_404(session, category_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        c.name = data["name"].strip()
    if "sort_order" in data:
        c.sort_order = data["sort_order"]
    if "is_active" in data:
        c.is_active = data["is_active"]
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Категория с таким именем уже существует",
        ) from None
    await session.refresh(c)
    return {
        "id": c.id,
        "name": c.name,
        "sort_order": c.sort_order,
        "is_active": c.is_active,
    }


@router.delete("/categories/{category_id}")
async def admin_delete_category(
    category_id: int,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    c = await _category_or_404(session, category_id)
    n = (
        await session.scalar(
            select(func.count())
            .select_from(models.Product)
            .where(models.Product.category_id == category_id),
        )
    )
    if (n or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить категорию, пока в ней есть товары",
        )
    await session.delete(c)
    await session.commit()
    return {"status": "ok"}


@router.get("/products")
async def admin_list_products(
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
    category_id: int | None = None,
) -> list[dict[str, Any]]:
    q = select(models.Product).options(
        selectinload(models.Product.attributes),
        selectinload(models.Product.images),
    )
    if category_id is not None:
        q = q.where(models.Product.category_id == category_id)
    q = q.order_by(models.Product.sort_order, models.Product.id)
    rows = (await session.execute(q)).scalars().all()
    return [product_to_dict(p) for p in rows]


@router.post("/products")
async def admin_create_product(
    body: ProductCreateBody,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _category_or_404(session, body.category_id)
    p = models.Product(
        category_id=body.category_id,
        name=body.name.strip(),
        description=body.description or "",
        composition=body.composition or "",
        weight_g=body.weight_g,
        price=Decimal(body.price),
        image_url=(body.image_url or "").strip(),
        is_available=body.is_available,
        sort_order=body.sort_order,
        stock_quantity=body.stock_quantity,
    )
    session.add(p)
    await session.flush()
    for idx, a in enumerate(body.attributes):
        session.add(
            models.ProductAttribute(
                product_id=p.id,
                name=a.name.strip(),
                value=a.value,
                sort_order=idx,
            ),
        )
    for idx, im in enumerate(body.images):
        session.add(
            models.ProductImage(
                product_id=p.id,
                url=im.url.strip(),
                sort_order=idx,
            ),
        )
    await session.commit()
    loaded = await _product_loaded(session, p.id)
    assert loaded is not None
    return product_to_dict(loaded)


@router.patch("/products/{product_id}")
async def admin_patch_product(
    product_id: int,
    body: ProductPatchBody,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    p = await _product_loaded(session, product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    data = body.model_dump(exclude_unset=True)
    if "category_id" in data:
        await _category_or_404(session, data["category_id"])
        p.category_id = data["category_id"]
    if "name" in data:
        p.name = data["name"].strip()
    if "description" in data:
        p.description = data["description"] or ""
    if "composition" in data:
        p.composition = data["composition"] or ""
    if "weight_g" in data:
        p.weight_g = data["weight_g"]
    if "price" in data:
        p.price = Decimal(data["price"])
    if "image_url" in data:
        p.image_url = (data["image_url"] or "").strip()
    if "is_available" in data:
        p.is_available = data["is_available"]
    if "sort_order" in data:
        p.sort_order = data["sort_order"]
    if "stock_quantity" in data:
        p.stock_quantity = data["stock_quantity"]
    if "attributes" in data:
        await session.execute(
            sql_delete(models.ProductAttribute).where(
                models.ProductAttribute.product_id == p.id,
            ),
        )
        for idx, a in enumerate(data["attributes"] or []):
            session.add(
                models.ProductAttribute(
                    product_id=p.id,
                    name=str(a["name"]).strip(),
                    value=str(a["value"]),
                    sort_order=idx,
                ),
            )
    if "images" in data:
        await session.execute(
            sql_delete(models.ProductImage).where(
                models.ProductImage.product_id == p.id,
            ),
        )
        for idx, im in enumerate(data["images"] or []):
            session.add(
                models.ProductImage(
                    product_id=p.id,
                    url=str(im["url"]).strip(),
                    sort_order=idx,
                ),
            )
    await session.commit()
    loaded = await _product_loaded(session, product_id)
    assert loaded is not None
    return product_to_dict(loaded)


@router.delete("/products/{product_id}")
async def admin_delete_product(
    product_id: int,
    _: Annotated[None, Depends(require_operator_panel_auth)],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    p = await session.get(models.Product, product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    in_carts = (
        await session.scalar(
            select(func.count())
            .select_from(models.CartItem)
            .where(models.CartItem.product_id == product_id),
        )
    )
    if (in_carts or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить товар: он добавлен в корзины покупателей",
        )
    try:
        await session.delete(p)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить товар: он связан с заказами или другими данными",
        ) from None
    return {"status": "ok"}
