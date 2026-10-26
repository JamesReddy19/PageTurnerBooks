"""Requirement 128 — one authoritative free-delivery threshold of ₹500.

Before this batch the project held two different ideas about delivery:

* ``config.FREE_SHIPPING_PAISE`` was ``100000`` (₹1000), so the promise in the
  brief of "free delivery from ₹500" was not what the code actually charged;
* checkout had a hardcoded ``8000`` fallback for a pincode with no zone, while
  the pre-packing order edit read ``config["SHIPPING_PAISE"]`` — a key that was
  never defined, so an order that dropped below the threshold raised KeyError
  and the edit failed with a 500 instead of re-pricing;
* no screen told the customer what the threshold was, so the number could drift
  from the rule without anyone noticing.

These tests pin the single constant, both order paths, and the screens. As with
the other batches, they are written so that they fail on the pre-fix code:

* ``test_threshold_is_rupees_500_in_integer_paise`` fails because the value was
  100000;
* ``test_checkout_charges_the_config_fee_below_the_threshold`` fails because the
  fallback was the literal 8000 and disagreed with a changed config;
* ``test_order_edit_below_the_threshold_reprices_without_crashing`` fails with
  KeyError on the undefined ``SHIPPING_PAISE`` key;
* ``test_screens_render_the_threshold_from_the_constant`` fails because no
  screen mentioned the threshold at all.
"""
import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner.db import get_db


class ShippingThresholdTestCase(unittest.TestCase):
    """Shared app fixture: one temporary database per test."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")

        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": self.db_path,
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
                "ADMIN_EMAIL": "",
                "ADMIN_PASSWORD": "",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    # -- helpers ---------------------------------------------------------
    def token(self, path="/"):
        response = self.client.get(path)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, path, data=None, **kwargs):
        payload = dict(data or {})
        payload["csrf_token"] = self.token()
        return self.client.post(path, data=payload, **kwargs)

    def register_and_login(
        self,
        email="reader@example.com",
        name="Reader One",
        password="Reading123",
    ):
        self.post(
            "/register",
            {"name": name, "email": email, "password": password},
            follow_redirects=True,
        )
        self.post(
            "/login",
            {"email": email, "password": password},
            follow_redirects=True,
        )
        return self.user_id(email)

    def user_id(self, email="reader@example.com"):
        with self.app.app_context():
            row = get_db().execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()

        self.assertIsNotNone(row, f"{email} was not created")
        return row["id"]

    def prepare_book(self, price_paise=20000, stock=10):
        """Use the first seeded book, with a known price and stock."""
        with self.app.app_context():
            connection = get_db()
            book = connection.execute(
                "SELECT id FROM books ORDER BY id LIMIT 1"
            ).fetchone()
            connection.execute(
                "UPDATE books SET price_paise = ?, stock = ?, "
                "sale_price_paise = NULL WHERE id = ?",
                (price_paise, stock, book["id"]),
            )
            connection.commit()
            return book["id"]

    def add_to_cart(self, book_id, quantity=1):
        response = self.post(
            f"/cart/add/{book_id}", {"quantity": str(quantity)}
        )
        self.assertIn(response.status_code, (302, 303))

    def checkout_payload(self, pincode="411001"):
        """GET /checkout and return the exact form body a browser would send."""
        response = self.client.get("/checkout")
        self.assertEqual(response.status_code, 200)

        csrf = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        checkout_token = re.search(
            rb'name="checkout_token" value="([^"]+)"', response.data
        )
        self.assertIsNotNone(csrf, "no CSRF token on the checkout page")
        self.assertIsNotNone(
            checkout_token, "no checkout token on the checkout page"
        )

        return {
            "csrf_token": csrf.group(1).decode(),
            "checkout_token": checkout_token.group(1).decode(),
            "name": "Reader One",
            "phone": "9876543210",
            "new_address": "1 Test Street, Pune",
            "pincode": pincode,
        }

    def place_order(self, price_paise, quantity=1, pincode="411001"):
        """Register, fill the cart, check out and return (user_id, order row)."""
        user_id = self.register_and_login()
        book_id = self.prepare_book(price_paise=price_paise, stock=20)
        self.add_to_cart(book_id, quantity)

        response = self.client.post(
            "/checkout", data=self.checkout_payload(pincode=pincode)
        )
        self.assertEqual(response.status_code, 302)

        orders = self.order_rows(user_id)
        self.assertEqual(len(orders), 1)
        return user_id, orders[0]

    def order_rows(self, user_id):
        with self.app.app_context():
            return get_db().execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()

    # =================================================================
    # The constant itself
    # =================================================================
    def test_threshold_is_rupees_500_in_integer_paise(self):
        threshold = self.app.config["FREE_SHIPPING_PAISE"]

        self.assertEqual(
            threshold,
            50_000,
            "Requirement 128 fixes free delivery from ₹500 (50000 paise).",
        )
        self.assertIsInstance(threshold, int, "money must be integer paise")

    def test_threshold_is_defined_exactly_once_in_the_package(self):
        """One authoritative constant: no second copy to drift out of step."""
        package = os.path.join(os.path.dirname(__file__), "..", "pageturner")
        definitions = []

        for root, _dirs, files in os.walk(package):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    for number, line in enumerate(handle, start=1):
                        if re.match(
                            r"\s*FREE_SHIPPING_PAISE\s*=", line
                        ):  # assignment, not a read
                            definitions.append(
                                f"{os.path.relpath(path, package)}:{number}"
                            )

        self.assertEqual(
            len(definitions),
            1,
            "FREE_SHIPPING_PAISE must be assigned in one place only, "
            f"found: {definitions}",
        )
        self.assertTrue(definitions[0].startswith("config.py"))

    def test_flat_fee_is_a_named_constant_not_a_literal(self):
        """The fallback fee the two order paths charge is one named value."""
        self.assertEqual(self.app.config["DEFAULT_SHIPPING_PAISE"], 8_000)

        checkout_source = os.path.join(
            os.path.dirname(__file__),
            "..",
            "pageturner",
            "blueprints",
            "shop_checkout.py",
        )
        with open(checkout_source, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn('current_app.config["DEFAULT_SHIPPING_PAISE"]', source)
        self.assertNotIn(
            "else 8000",
            source,
            "checkout must read the config fee, not a hardcoded 8000",
        )

    # =================================================================
    # Checkout: the threshold is the boundary, not an approximation
    # =================================================================
    def test_checkout_is_free_at_exactly_the_threshold(self):
        _user_id, order = self.place_order(price_paise=25_000, quantity=2)

        self.assertEqual(order["subtotal_paise"], 50_000)
        self.assertEqual(
            order["shipping_paise"],
            0,
            "₹500 is inclusive: at the threshold delivery is free",
        )

    def test_checkout_charges_one_paise_below_the_threshold(self):
        # 49999 paise of goods, pincode 411001 has no seeded zone, so the
        # configured flat fee applies.
        _user_id, order = self.place_order(price_paise=49_999, quantity=1)

        self.assertEqual(order["subtotal_paise"], 49_999)
        self.assertEqual(
            order["shipping_paise"],
            self.app.config["DEFAULT_SHIPPING_PAISE"],
            "one paise under the threshold must pay the configured fee",
        )

    def test_zone_fee_is_used_below_the_threshold(self):
        # Pincode 500001 -> prefix "50", seeded at 5000 paise.
        _user_id, order = self.place_order(
            price_paise=20_000, quantity=1, pincode="500001"
        )

        self.assertEqual(order["subtotal_paise"], 20_000)
        self.assertEqual(order["shipping_paise"], 5_000)

    def test_checkout_charges_the_config_fee_below_the_threshold(self):
        """Changing the configured fee changes what checkout charges.

        This is what the old hardcoded ``8000`` could not do.
        """
        self.app.config["DEFAULT_SHIPPING_PAISE"] = 12_345
        try:
            _user_id, order = self.place_order(price_paise=20_000, quantity=1)
        finally:
            self.app.config["DEFAULT_SHIPPING_PAISE"] = 8_000

        self.assertEqual(order["shipping_paise"], 12_345)

    # =================================================================
    # Order edit: the path that used to raise KeyError
    # =================================================================
    def test_order_edit_below_the_threshold_reprices_without_crashing(self):
        user_id, order = self.place_order(price_paise=25_000, quantity=2)
        self.assertEqual(order["shipping_paise"], 0)

        with self.app.app_context():
            item = get_db().execute(
                "SELECT id FROM order_items WHERE order_id = ? ORDER BY id",
                (order["id"],),
            ).fetchone()

        # Drop to one copy: ₹250 of goods, so free delivery no longer applies.
        response = self.post(
            f"/order/{order['id']}/edit",
            {f"quantity_{item['id']}": "1"},
            follow_redirects=True,
        )
        self.assertEqual(
            response.status_code, 200, "the edit must not 500 on missing config"
        )

        edited = self.order_rows(user_id)[0]
        self.assertEqual(edited["subtotal_paise"], 25_000)
        self.assertEqual(
            edited["shipping_paise"],
            self.app.config["DEFAULT_SHIPPING_PAISE"],
            "an order that falls below ₹500 pays the configured flat fee",
        )

    def test_order_edit_above_the_threshold_stays_free(self):
        user_id, order = self.place_order(
            price_paise=20_000, quantity=1, pincode="500001"
        )
        self.assertEqual(order["shipping_paise"], 5_000)

        with self.app.app_context():
            item = get_db().execute(
                "SELECT id FROM order_items WHERE order_id = ? ORDER BY id",
                (order["id"],),
            ).fetchone()

        # Three copies: ₹600 of goods, which now clears the ₹500 threshold.
        self.post(
            f"/order/{order['id']}/edit",
            {f"quantity_{item['id']}": "3"},
            follow_redirects=True,
        )

        edited = self.order_rows(user_id)[0]
        self.assertEqual(edited["subtotal_paise"], 60_000)
        self.assertEqual(edited["shipping_paise"], 0)

    def test_order_edit_keeps_ownership_check(self):
        """Re-pricing must not weaken the existing ownership rule."""
        _user_id, order = self.place_order(price_paise=20_000, quantity=1)

        self.client.get("/logout")
        self.register_and_login(email="other@example.com", name="Other Reader")

        response = self.post(f"/order/{order['id']}/edit", {}, follow_redirects=False)
        self.assertEqual(response.status_code, 404)

    # =================================================================
    # Screens describe the threshold from the constant
    # =================================================================
    def test_screens_render_the_threshold_from_the_constant(self):
        self.register_and_login()
        book_id = self.prepare_book(price_paise=20_000, stock=10)
        self.add_to_cart(book_id, 1)

        cart = self.client.get("/cart")
        self.assertEqual(cart.status_code, 200)
        self.assertIn("₹500".encode(), cart.data)

        checkout = self.client.get("/checkout")
        self.assertEqual(checkout.status_code, 200)
        self.assertIn("₹500".encode(), checkout.data)

        # Move the single constant: every screen must follow it, which proves
        # the number is not written into the templates.
        self.app.config["FREE_SHIPPING_PAISE"] = 123_400
        try:
            moved_cart = self.client.get("/cart")
            moved_checkout = self.client.get("/checkout")
        finally:
            self.app.config["FREE_SHIPPING_PAISE"] = 50_000

        self.assertIn("₹1,234".encode(), moved_cart.data)
        self.assertIn("₹1,234".encode(), moved_checkout.data)
        self.assertNotIn(
            "₹500".encode(),
            moved_cart.data,
            "a hardcoded threshold in the template would still say ₹500",
        )

    def test_cart_shows_how_much_more_is_needed(self):
        book_id = self.prepare_book(price_paise=20_000, stock=10)
        self.add_to_cart(book_id, 1)

        cart = self.client.get("/cart")
        # ₹500 threshold less the ₹200 subtotal leaves ₹300 to qualify.
        self.assertIn("₹300".encode(), cart.data)


if __name__ == "__main__":
    unittest.main()
