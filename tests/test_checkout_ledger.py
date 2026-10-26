"""Regression tests for the checkout stock ledger.

These pin a bug that shipped and was then fixed: the ``stock_log`` insert used to
sit inside the ``if points_earned:`` branch *and* outside the per-book loop, so a
purchase logged only the last line item and logged nothing at all when the order
was too small to earn loyalty points. The ledger is what reconciles
``books.stock`` against real movements, so both halves are covered here.
"""
import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner.db import get_db


class CheckoutLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": os.path.join(self.temp_dir.name, "test.db"),
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
        })
        self.client = self.app.test_client()
        self.register_and_login()

    def tearDown(self):
        self.temp_dir.cleanup()

    def token(self, path="/"):
        response = self.client.get(path)
        match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, path, data=None, **kwargs):
        data = dict(data or {})
        data["csrf_token"] = self.token()
        return self.client.post(path, data=data, **kwargs)

    def register_and_login(self, email="ledger@example.com", name="Ledger Reader", password="Reading123"):
        token = self.token("/register")
        self.client.post(
            "/register",
            data={"csrf_token": token, "name": name, "email": email, "password": password},
            follow_redirects=True,
        )
        token = self.token("/login")
        self.client.post(
            "/login",
            data={"csrf_token": token, "email": email, "password": password},
            follow_redirects=True,
        )

    def place_order(self):
        response = self.post(
            "/checkout",
            data={
                "name": "Ledger Reader",
                "phone": "9876543210",
                "new_address": "12 Main Road",
                "pincode": "500001",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Order #", response.data)

    def test_every_purchased_line_is_logged(self):
        self.post("/cart/add/1", {"quantity": "1"})
        self.post("/cart/add/2", {"quantity": "1"})
        self.place_order()

        with self.app.app_context():
            connection = get_db()
            logged = connection.execute(
                "SELECT book_id, change_amount, reason FROM stock_log "
                "WHERE reason='purchase' ORDER BY book_id"
            ).fetchall()
            stock = {
                row["id"]: row["stock"]
                for row in connection.execute("SELECT id, stock FROM books WHERE id IN (1,2)")
            }
            items = connection.execute(
                "SELECT book_id FROM order_items ORDER BY book_id"
            ).fetchall()

        self.assertEqual([row["book_id"] for row in logged], [1, 2])
        self.assertEqual([row["change_amount"] for row in logged], [-1, -1])
        self.assertEqual([row["book_id"] for row in items], [1, 2])
        # The debit and its ledger row agree with each other.
        self.assertEqual(stock[1], 9)
        self.assertEqual(stock[2], 9)

    def test_purchase_is_logged_even_when_no_points_are_earned(self):
        # Below the 10,000-paise earning threshold, so loyalty_points_earned is 0.
        # This is the exact case that used to skip the stock_log row entirely.
        with self.app.app_context():
            get_db().execute("UPDATE books SET price_paise=5000, sale_price_paise=NULL WHERE id=1")
            get_db().commit()

        self.post("/cart/add/1", {"quantity": "2"})
        self.place_order()

        with self.app.app_context():
            connection = get_db()
            order = connection.execute("SELECT * FROM orders").fetchone()
            logged = connection.execute(
                "SELECT change_amount FROM stock_log WHERE reason='purchase'"
            ).fetchall()
            stock = connection.execute("SELECT stock FROM books WHERE id=1").fetchone()["stock"]

        self.assertEqual(order["loyalty_points_earned"], 0)
        self.assertEqual([row["change_amount"] for row in logged], [-2])
        self.assertEqual(stock, 8)

    def test_ledger_matches_stock_after_cancellation(self):
        self.post("/cart/add/1", {"quantity": "2"})
        self.place_order()

        with self.app.app_context():
            order_id = get_db().execute("SELECT MAX(id) FROM orders").fetchone()[0]

        self.post(f"/order/{order_id}/cancel")

        with self.app.app_context():
            connection = get_db()
            movements = [
                (row["change_amount"], row["reason"])
                for row in connection.execute(
                    "SELECT change_amount, reason FROM stock_log "
                    "WHERE book_id=1 ORDER BY id"
                )
            ]
            stock = connection.execute("SELECT stock FROM books WHERE id=1").fetchone()["stock"]

        # Purchased then restored: the net movement is zero and stock is whole.
        self.assertEqual(movements, [(-2, "purchase"), (2, "order cancellation")])
        self.assertEqual(sum(change for change, _reason in movements), 0)
        self.assertEqual(stock, 10)


if __name__ == "__main__":
    unittest.main()
