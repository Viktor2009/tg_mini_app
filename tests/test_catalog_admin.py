"""CRUD каталога (панель оператора) и витрина / корзина."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestCatalogAdmin(unittest.TestCase):
    def test_admin_catalog_stock_and_cart(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = os.path.join(tmp, "test.db")
            env = {
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
                "OPERATOR_PANEL_TOKEN": "op-test-token",
                "BOT_TOKEN": "",
            }
            with patch.dict(os.environ, env, clear=False):
                from tg_mini_app.api.app import create_app

                app = create_app()
                auth = ("operator", "op-test-token")
                with TestClient(app) as client:
                    r = client.post(
                        "/operator-panel/catalog/categories",
                        json={"name": "Категория API-тест", "sort_order": 50},
                        auth=auth,
                    )
                    self.assertEqual(r.status_code, 200, r.text)
                    new_id = r.json()["id"]

                    r = client.patch(
                        f"/operator-panel/catalog/categories/{new_id}",
                        json={"name": "Категория API-тест (патч)"},
                        auth=auth,
                    )
                    self.assertEqual(r.status_code, 200, r.text)
                    self.assertIn("патч", r.json()["name"])

                    r = client.post(
                        "/operator-panel/catalog/products",
                        json={
                            "category_id": new_id,
                            "name": "Ролл тест склад",
                            "description": "Описание",
                            "price": "199.00",
                            "stock_quantity": 2,
                            "attributes": [{"name": "Острое", "value": "Нет"}],
                            "images": [
                                {"url": "https://example.com/a.jpg"},
                                {"url": "https://example.com/b.jpg"},
                            ],
                        },
                        auth=auth,
                    )
                    self.assertEqual(r.status_code, 200, r.text)
                    pid = r.json()["id"]
                    self.assertEqual(r.json()["stock_quantity"], 2)
                    self.assertEqual(len(r.json()["attributes"]), 1)
                    self.assertEqual(len(r.json()["image_gallery"]), 2)

                    products = client.get("/catalog/products").json()
                    ours = [p for p in products if p["id"] == pid]
                    self.assertEqual(len(ours), 1)
                    p = ours[0]
                    self.assertEqual(p["attributes"][0]["name"], "Острое")
                    self.assertEqual(len(p["image_gallery"]), 2)

                    cr = client.post("/cart", json={})
                    self.assertEqual(cr.status_code, 200, cr.text)
                    cart_id = cr.json()["id"]
                    r = client.post(
                        f"/cart/{cart_id}/items",
                        json={"product_id": pid, "qty_delta": 3},
                    )
                    self.assertEqual(r.status_code, 409, r.text)

                    r = client.post(
                        f"/cart/{cart_id}/items",
                        json={"product_id": pid, "qty_delta": 2},
                    )
                    self.assertEqual(r.status_code, 200, r.text)

                    r = client.delete(
                        f"/operator-panel/catalog/products/{pid}",
                        auth=auth,
                    )
                    self.assertEqual(r.status_code, 409, r.text)


if __name__ == "__main__":
    unittest.main()
