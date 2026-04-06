from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class CartCreateRequest(BaseModel):
    owner_tg_id: int | None = None


class CartItemChangeRequest(BaseModel):
    product_id: int = Field(ge=1)
    qty_delta: int = Field(ge=-100, le=100)
    init_data: str | None = None
    customer_tg_id: int | None = None


class CartItemResponse(BaseModel):
    product_id: int
    name: str
    qty: int
    price: Decimal
    subtotal: Decimal


class CartResponse(BaseModel):
    id: str
    shared_key: str
    status: str
    items: list[CartItemResponse]
    total: Decimal


class ShareResponse(BaseModel):
    shared_key: str


class OrderCreateRequest(BaseModel):
    cart_id: str
    init_data: str | None = None
    customer_tg_id: int | None = None
    address: str = Field(min_length=3, max_length=512)
    delivery_time: str = Field(min_length=2, max_length=64)
    customer_comment: str = Field(default="", max_length=512)


class OrderLineItemResponse(BaseModel):
    """Позиция заказа (снимок из meta.items)."""

    product_id: int
    name: str
    qty: int
    price: Decimal
    line_status: str = "ok"
    proposed_product_id: int | None = None
    proposed_name: str | None = None
    proposed_price: Decimal | None = None


class OrderResponse(BaseModel):
    id: int
    cart_id: str
    customer_tg_id: int
    address: str
    delivery_time: str
    customer_comment: str
    status: str
    payment_type: str
    total_amount: Decimal
    items: list[OrderLineItemResponse] = Field(default_factory=list)
    delivery_route: str | None = None
    payment_received_confirmed: bool = False
    courier_cash_received: bool = False
    courier_cash_received_at: str | None = None
    courier_delivered_at: str | None = None


# --- Управление каталогом (панель оператора) ---


class ProductAttributeInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=0, max_length=512)


class ProductImageInput(BaseModel):
    url: str = Field(min_length=1, max_length=1024)


class CategoryCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0
    is_active: bool = True


class CategoryPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    sort_order: int | None = None
    is_active: bool | None = None


class ProductCreateBody(BaseModel):
    category_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    composition: str = ""
    weight_g: int | None = None
    price: Decimal = Field(ge=Decimal("0"))
    image_url: str = Field(default="", max_length=1024)
    is_available: bool = True
    sort_order: int = 0
    stock_quantity: int | None = Field(default=None, ge=0)
    attributes: list[ProductAttributeInput] = Field(default_factory=list)
    images: list[ProductImageInput] = Field(default_factory=list)


class ProductPatchBody(BaseModel):
    category_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    composition: str | None = None
    weight_g: int | None = None
    price: Decimal | None = Field(default=None, ge=Decimal("0"))
    image_url: str | None = Field(default=None, max_length=1024)
    is_available: bool | None = None
    sort_order: int | None = None
    stock_quantity: int | None = Field(default=None, ge=0)
    attributes: list[ProductAttributeInput] | None = None
    images: list[ProductImageInput] | None = None

