from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from tg_mini_app.api.cart import router as cart_router
from tg_mini_app.api.delivery_staff import router as delivery_staff_router
from tg_mini_app.api.deps import get_db_session
from tg_mini_app.api.operator_panel import router as operator_panel_router
from tg_mini_app.api.orders import router as orders_router
from tg_mini_app.db import models
from tg_mini_app.db.base import Base
from tg_mini_app.db.seed import seed_if_empty
from tg_mini_app.db.session import create_engine, create_sessionmaker
from tg_mini_app.paths import STATIC_DIR, TEMPLATES_DIR
from tg_mini_app.settings import get_settings


def create_app() -> FastAPI:
    # Без этого /operator-panel/ даёт 307 без Basic → белый экран в браузере.
    app = FastAPI(title="tg_mini_app", redirect_slashes=False)

    engine = create_engine()
    app.state.engine = engine
    app.state.session_factory = create_sessionmaker(engine)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.on_event("startup")
    async def _startup() -> None:
        settings = get_settings()
        if settings.bot_token.strip():
            from aiogram import Bot  # local import to keep API start lightweight

            app.state.bot = Bot(token=settings.bot_token)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with app.state.session_factory() as session:
            await seed_if_empty(session)

    app.include_router(cart_router)
    app.include_router(orders_router)
    app.include_router(operator_panel_router)
    app.include_router(delivery_staff_router)

    @app.get("/", response_class=HTMLResponse)
    async def root_page() -> str:
        return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/><title>tg_mini_app</title></head>
<body style="font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem">
  <h1>tg_mini_app</h1>
  <ul>
    <li><a href="/webapp">Mini App (витрина)</a></li>
    <li><a href="/operator-panel">Панель оператора</a> — откроется
      <a href="/operator-panel/login">форма входа</a> или HTTP Basic:
      логин <code>operator</code>, пароль = <code>OPERATOR_PANEL_TOKEN</code></li>
    <li><a href="/delivery/login">Страница курьера</a> (секрет =
      <code>COURIER_API_TOKEN</code>)</li>
    <li><a href="/operator-panel/ping">Проверка панели (JSON, без пароля)</a></li>
    <li><a href="/health">/health</a></li>
  </ul>
  <p>Если страница не открывается по адресу из Telegram (cloudflare), поднимите
  туннель заново или откройте этот сервер по
  <strong>http://127.0.0.1:8000</strong> на том же компьютере, где запущен API.</p>
</body></html>"""

    @app.get("/webapp", response_class=HTMLResponse)
    async def webapp(request: Request) -> Any:
        settings = get_settings()
        app_env = (settings.app_env or "local").strip().lower()
        return templates.TemplateResponse(
            request=request,
            name="webapp.html",
            context={"title": "Суши • Mini App", "app_env": app_env},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/catalog/categories")
    async def list_categories(
        session: AsyncSession = Depends(get_db_session),
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(models.Category).order_by(
                    models.Category.sort_order,
                    models.Category.id,
                )
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

    @app.get("/catalog/products")
    async def list_products(
        session: AsyncSession = Depends(get_db_session),
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(models.Product)
                .order_by(models.Product.sort_order, models.Product.id)
            )
        ).scalars().all()

        def to_decimal(v: Decimal) -> str:
            return f"{v:.2f}"

        return [
            {
                "id": p.id,
                "category_id": p.category_id,
                "name": p.name,
                "description": p.description,
                "composition": p.composition,
                "weight_g": p.weight_g,
                "price": to_decimal(p.price),
                "image_url": p.image_url,
                "is_available": p.is_available,
                "sort_order": p.sort_order,
            }
            for p in rows
        ]

    @app.get("/debug/last-order")
    async def debug_last_order(
        session: AsyncSession = Depends(get_db_session),
    ) -> dict[str, Any]:
        order = (
            await session.execute(
                select(models.Order).order_by(models.Order.id.desc()).limit(1)
            )
        ).scalars().first()
        if order is None:
            return {"exists": False}
        return {
            "exists": True,
            "id": order.id,
            "status": order.status,
            "customer_tg_id": order.customer_tg_id,
            "total_amount": str(order.total_amount),
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "operator_notify_error": order.meta.get("operator_notify_error"),
        }

    return app

