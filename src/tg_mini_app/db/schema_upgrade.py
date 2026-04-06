"""Доработка схемы БД без Alembic (новые колонки в уже существующих таблицах)."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


def _sync_upgrade(conn: Connection) -> None:
    insp = inspect(conn)
    if not insp.has_table("products"):
        return
    cols = {c["name"] for c in insp.get_columns("products")}
    if "stock_quantity" not in cols:
        conn.execute(text("ALTER TABLE products ADD COLUMN stock_quantity INTEGER"))


async def run_schema_upgrades(engine: AsyncEngine) -> None:
    """Вызывать после create_all. Для SQLite и PostgreSQL добавляет недостающие колонки."""
    async with engine.begin() as conn:
        await conn.run_sync(_sync_upgrade)
