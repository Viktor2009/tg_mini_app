"""Проверка разбора клиента для заказов (без БД)."""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from tg_mini_app.api.customer_identity import resolve_customer_tg_id
from tg_mini_app.settings import Settings


def _s(**overrides: object) -> Settings:
    """Без чтения .env — иначе APP_ENV из файла ломает проверку production."""
    data: dict[str, object] = {
        "app_env": "local",
        "bot_token": "",
        "telegram_webapp_secret": "",
        "webapp_init_max_age_sec": 86400,
    }
    data.update(overrides)
    return Settings.model_construct(None, **data)


class TestResolveCustomerTgId(unittest.TestCase):
    def test_requires_identity(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            resolve_customer_tg_id(None, None, settings=_s(bot_token="any"))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_debug_numeric_id_local_only(self) -> None:
        self.assertEqual(
            resolve_customer_tg_id(
                None,
                830678989,
                settings=_s(bot_token=""),
            ),
            830678989,
        )

    def test_numeric_id_rejected_outside_local(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            resolve_customer_tg_id(
                None,
                830678989,
                settings=_s(app_env="production", bot_token="x:y"),
            )
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
