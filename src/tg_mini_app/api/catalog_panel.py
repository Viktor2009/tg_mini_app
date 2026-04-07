"""HTML-панель управления каталогом (та же авторизация, что у панели оператора)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.templating import Jinja2Templates

from tg_mini_app.api.catalog_serialize import product_to_dict
from tg_mini_app.api.catalog_uploads import ensure_catalog_uploads_dir, save_catalog_image
from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.operator_panel import require_operator_panel_auth
from tg_mini_app.db import models
from tg_mini_app.paths import CATALOG_UPLOADS_DIR, TEMPLATES_DIR

router = APIRouter(prefix="/operator-panel/catalog-manage", tags=["catalog-panel"])
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_MEDIA_PREFIX = "/catalog-media"


def _redirect(msg: str, *, err: bool = False, category_id: int | None = None) -> RedirectResponse:
    q = f"err={quote(msg)}" if err else f"ok={quote(msg)}"
    if category_id is not None:
        q += f"&category_id={category_id}"
    return RedirectResponse(
        url=f"/operator-panel/catalog-manage?{q}",
        status_code=303,
    )


def _parse_attributes_text(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and not line.startswith("http"):
            key, _, val = line.partition("=")
        elif ":" in line:
            key, _, val = line.partition(":")
        else:
            continue
        key, val = key.strip(), val.strip()
        if key and len(key) <= 128 and len(val) <= 512:
            out.append((key, val))
    return out


def _parse_urls_text(raw: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for line in raw.splitlines():
        u = line.strip()
        if not u or u.startswith("#"):
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


async def _category_or_none(session: AsyncSession, category_id: int) -> models.Category | None:
    return (
        await session.execute(select(models.Category).where(models.Category.id == category_id))
    ).scalar_one_or_none()


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


def _norm_stock(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d+", s) is None:
        raise ValueError("stock")
    v = int(s)
    if v < 0:
        raise ValueError("stock")
    return v


def _norm_weight(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s) is None:
        raise ValueError("weight")
    return int(s)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def catalog_manage_index(
    request: Request,
    category_id: int | None = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    cats = list(
        (
            await session.execute(
                select(models.Category).order_by(
                    models.Category.sort_order,
                    models.Category.id,
                ),
            )
        )
        .scalars()
        .all(),
    )
    q = select(models.Product).options(
        selectinload(models.Product.attributes),
        selectinload(models.Product.images),
    )
    if category_id is not None:
        q = q.where(models.Product.category_id == category_id)
    q = q.order_by(models.Product.sort_order, models.Product.id)
    products = list((await session.execute(q)).scalars().all())
    products_data = [product_to_dict(p) for p in products]

    return _templates.TemplateResponse(
        request=request,
        name="catalog_manage.html",
        context={
            "title": "Каталог товаров",
            "categories": cats,
            "products": products_data,
            "filter_category_id": category_id,
            "flash_ok": request.query_params.get("ok"),
            "flash_err": request.query_params.get("err"),
            "media_prefix": _MEDIA_PREFIX,
        },
    )


@router.post("/categories", response_class=RedirectResponse)
async def catalog_create_category(
    name: Annotated[str, Form()],
    sort_order: Annotated[int, Form()] = 0,
    is_active: Annotated[str | None, Form()] = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    n = (name or "").strip()
    if not n:
        return _redirect("Укажите название категории.", err=True)
    c = models.Category(name=n, sort_order=sort_order, is_active=is_active == "on")
    session.add(c)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _redirect("Категория с таким именем уже есть.", err=True)
    return _redirect("Категория создана.")


@router.post("/categories/{cid}", response_class=RedirectResponse)
async def catalog_update_category(
    cid: int,
    name: Annotated[str, Form()],
    sort_order: Annotated[int, Form()] = 0,
    is_active: Annotated[str | None, Form()] = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    c = await _category_or_none(session, cid)
    if c is None:
        return _redirect("Категория не найдена.", err=True)
    n = (name or "").strip()
    if not n:
        return _redirect("Название не может быть пустым.", err=True)
    c.name = n
    c.sort_order = sort_order
    c.is_active = is_active == "on"
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _redirect("Такое имя категории уже занято.", err=True)
    return _redirect("Категория сохранена.")


@router.post("/categories/{cid}/delete", response_class=RedirectResponse)
async def catalog_delete_category(
    cid: int,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    c = await _category_or_none(session, cid)
    if c is None:
        return _redirect("Категория не найдена.", err=True)
    n = (
        await session.scalar(
            select(func.count())
            .select_from(models.Product)
            .where(models.Product.category_id == cid),
        )
    )
    if (n or 0) > 0:
        return _redirect("Сначала удалите или перенесите товары из категории.", err=True)
    await session.delete(c)
    await session.commit()
    return _redirect("Категория удалена.")


@router.get("/product/new", response_class=HTMLResponse)
async def catalog_product_new(
    request: Request,
    category_id: int | None = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    cats = list(
        (
            await session.execute(
                select(models.Category).order_by(
                    models.Category.sort_order,
                    models.Category.id,
                ),
            )
        )
        .scalars()
        .all(),
    )
    if not cats:
        return _templates.TemplateResponse(
            request=request,
            name="catalog_product_form.html",
            context={
                "title": "Новый товар",
                "categories": [],
                "product": None,
                "prefill_category_id": None,
                "attributes_text": "",
                "image_urls_text": "",
                "flash_err": "Сначала создайте хотя бы одну категорию.",
                "flash_ok": None,
                "is_new": True,
                "media_prefix": _MEDIA_PREFIX,
            },
        )
    return _templates.TemplateResponse(
        request=request,
        name="catalog_product_form.html",
        context={
            "title": "Новый товар",
            "categories": cats,
            "product": None,
            "prefill_category_id": category_id if category_id is not None else None,
            "attributes_text": "",
            "image_urls_text": "",
            "flash_err": request.query_params.get("err"),
            "flash_ok": request.query_params.get("ok"),
            "is_new": True,
            "media_prefix": _MEDIA_PREFIX,
        },
    )


@router.get("/product/{pid}", response_class=HTMLResponse)
async def catalog_product_edit(
    request: Request,
    pid: int,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    p = await _product_loaded(session, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    cats = list(
        (
            await session.execute(
                select(models.Category).order_by(
                    models.Category.sort_order,
                    models.Category.id,
                ),
            )
        )
        .scalars()
        .all(),
    )
    pd = product_to_dict(p)
    attrs_lines = [f"{a['name']}: {a['value']}" for a in pd.get("attributes", [])]
    img_lines = list(pd.get("image_gallery", []))
    if pd.get("image_url") and pd["image_url"] not in img_lines:
        img_lines.insert(0, pd["image_url"])

    return _templates.TemplateResponse(
        request=request,
        name="catalog_product_form.html",
        context={
            "title": f"Товар #{pid}",
            "categories": cats,
            "product": pd,
            "prefill_category_id": None,
            "attributes_text": "\n".join(attrs_lines),
            "image_urls_text": "\n".join(img_lines),
            "flash_err": request.query_params.get("err"),
            "flash_ok": request.query_params.get("ok"),
            "is_new": False,
            "media_prefix": _MEDIA_PREFIX,
        },
    )


@router.post("/product/save", response_class=RedirectResponse)
async def catalog_product_save(
    category_id: Annotated[int, Form()],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    composition: Annotated[str, Form()] = "",
    weight_g: Annotated[str, Form()] = "",
    price: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
    is_available: Annotated[str | None, Form()] = None,
    sort_order: Annotated[int, Form()] = 0,
    stock_quantity: Annotated[str, Form()] = "",
    attributes_text: Annotated[str, Form()] = "",
    image_urls_text: Annotated[str, Form()] = "",
    product_id: Annotated[str | None, Form()] = None,
    new_images: Annotated[list[UploadFile] | None, File()] = None,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ensure_catalog_uploads_dir(CATALOG_UPLOADS_DIR)
    files_to_save = new_images or []

    n = (name or "").strip()
    if not n:
        return _redirect("Укажите название товара.", err=True, category_id=category_id)

    try:
        price_d = Decimal((price or "0").replace(",", ".").strip())
        if price_d < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return _redirect("Некорректная цена.", err=True, category_id=category_id)

    try:
        w = _norm_weight(weight_g)
        sq = _norm_stock(stock_quantity)
    except ValueError:
        return _redirect(
            "Вес и остаток: только целые числа ≥ 0 или пусто.",
            err=True,
            category_id=category_id,
        )

    cat = await _category_or_none(session, category_id)
    if cat is None:
        return _redirect("Категория не найдена.", err=True)

    attrs_pairs = _parse_attributes_text(attributes_text)
    url_list = _parse_urls_text(image_urls_text)

    uploaded_urls: list[str] = []
    for uf in files_to_save:
        if not uf.filename:
            continue
        try:
            basename = save_catalog_image(uf, CATALOG_UPLOADS_DIR)
            # Относительный URL с тем же хостом, что и API
            uploaded_urls.append(f"{_MEDIA_PREFIX}/{basename}")
        except ValueError as e:
            return _redirect(str(e), err=True, category_id=category_id)

    gallery = list(url_list)
    for u in uploaded_urls:
        if u not in gallery:
            gallery.append(u)

    main_img = (image_url or "").strip()
    if not main_img and gallery:
        main_img = gallery[0]

    pid_raw = (product_id or "").strip()
    is_create = not pid_raw

    if is_create:
        p = models.Product(
            category_id=category_id,
            name=n,
            description=description or "",
            composition=composition or "",
            weight_g=w,
            price=price_d,
            image_url=main_img,
            is_available=is_available == "on",
            sort_order=sort_order,
            stock_quantity=sq,
        )
        session.add(p)
        await session.flush()
        await session.refresh(p)
    else:
        try:
            pid = int(pid_raw)
        except ValueError:
            return _redirect("Некорректный ID товара.", err=True, category_id=category_id)
        loaded = await _product_loaded(session, pid)
        if loaded is None:
            return _redirect("Товар не найден.", err=True, category_id=category_id)
        p = loaded
        p.category_id = category_id
        p.name = n
        p.description = description or ""
        p.composition = composition or ""
        p.weight_g = w
        p.price = price_d
        p.image_url = main_img
        p.is_available = is_available == "on"
        p.sort_order = sort_order
        p.stock_quantity = sq
        await session.execute(
            sql_delete(models.ProductAttribute).where(
                models.ProductAttribute.product_id == p.id,
            ),
        )
        await session.execute(
            sql_delete(models.ProductImage).where(
                models.ProductImage.product_id == p.id,
            ),
        )
        await session.flush()

    for idx, (an, av) in enumerate(attrs_pairs):
        session.add(
            models.ProductAttribute(
                product_id=p.id,
                name=an,
                value=av,
                sort_order=idx,
            ),
        )
    for idx, url in enumerate(gallery):
        session.add(
            models.ProductImage(
                product_id=p.id,
                url=url,
                sort_order=idx,
            ),
        )
    await session.commit()

    if is_create:
        return RedirectResponse(
            url=f"/operator-panel/catalog-manage/product/{p.id}?ok={quote('Товар создан.')}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/operator-panel/catalog-manage/product/{p.id}?ok={quote('Сохранено.')}",
        status_code=303,
    )


@router.post("/product/{pid}/delete", response_class=RedirectResponse)
async def catalog_product_delete(
    pid: int,
    _: None = Depends(require_operator_panel_auth),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    p = await session.get(models.Product, pid)
    if p is None:
        return _redirect("Товар не найден.", err=True)
    cat_id = p.category_id
    in_carts = (
        await session.scalar(
            select(func.count())
            .select_from(models.CartItem)
            .where(models.CartItem.product_id == pid),
        )
    )
    if (in_carts or 0) > 0:
        return _redirect(
            "Товар в корзине покупателей — удаление недоступно.",
            err=True,
            category_id=cat_id,
        )
    try:
        await session.delete(p)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _redirect(
            "Нельзя удалить: заказ или другие данные ссылаются на товар.",
            err=True,
            category_id=cat_id,
        )
    return _redirect("Товар удалён.", category_id=cat_id)
