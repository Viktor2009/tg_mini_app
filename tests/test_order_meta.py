"""Пересчёт суммы и метаданные позиций заказа."""

from __future__ import annotations

import unittest
from decimal import Decimal

from tg_mini_app.order_meta import (
    line_has_awaiting_customer,
    meta_items,
    set_meta_items,
    total_from_meta_items,
)


class TestTotalFromMetaItems(unittest.TestCase):
    def test_sum(self) -> None:
        items = [
            {"qty": 2, "price_snapshot": "150.50"},
            {"qty": 1, "price_snapshot": "10"},
        ]
        self.assertEqual(total_from_meta_items(items), Decimal("311.00"))


class TestMetaItems(unittest.TestCase):
    def test_roundtrip(self) -> None:
        m = set_meta_items({}, [{"qty": 1, "name": "X", "price_snapshot": "1"}])
        self.assertEqual(len(meta_items(m)), 1)


class TestAwaitingLine(unittest.TestCase):
    def test_detect(self) -> None:
        items = [{"line_status": "awaiting_customer"}]
        self.assertTrue(line_has_awaiting_customer(items))

    def test_empty(self) -> None:
        self.assertFalse(line_has_awaiting_customer([]))


if __name__ == "__main__":
    unittest.main()
