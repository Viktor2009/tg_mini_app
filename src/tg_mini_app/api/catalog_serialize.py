"""Сериализация товара для каталога (публичный и админ-API)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tg_mini_app.db import models


def _money(v: Decimal) -> str:
    return f"{v:.2f}"


def product_to_dict(p: models.Product) -> dict[str, Any]:
    images = sorted(p.images, key=lambda i: (i.sort_order, i.id))
    attributes = sorted(p.attributes, key=lambda a: (a.sort_order, a.id))
    gallery_urls = [i.url for i in images]
    primary = (p.image_url or "").strip() or (gallery_urls[0] if gallery_urls else "")
    return {
        "id": p.id,
        "category_id": p.category_id,
        "name": p.name,
        "description": p.description,
        "composition": p.composition,
        "weight_g": p.weight_g,
        "price": _money(p.price),
        "image_url": primary,
        "image_gallery": gallery_urls,
        "is_available": p.is_available,
        "sort_order": p.sort_order,
        "stock_quantity": p.stock_quantity,
        "attributes": [{"name": a.name, "value": a.value} for a in attributes],
    }
