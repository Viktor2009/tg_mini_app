from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tg_mini_app.paths import PROJECT_ROOT
from tg_mini_app.settings import get_settings


@event.listens_for(Engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_conn: Any, connection_record: Any) -> None:
    """В SQLite по умолчанию FK выключены — без этого не работает ON DELETE RESTRICT."""
    dialect = getattr(connection_record, "dialect", None)
    if getattr(dialect, "name", None) != "sqlite":
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine() -> AsyncEngine:
    settings = get_settings()
    url = make_url(settings.database_url)
    engine_url = settings.database_url
    if url.drivername.startswith("sqlite") and url.database:
        db_path = Path(url.database)
        if not db_path.is_absolute():
            db_path = (PROJECT_ROOT / db_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine_url = str(url.set(database=str(db_path)))
    return create_async_engine(engine_url, echo=False, future=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

