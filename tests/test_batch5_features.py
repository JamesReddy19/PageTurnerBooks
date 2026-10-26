"""Regression tests for requirements 61, 62, 63, 64 and 65.

Every test here is written so that it fails on the pre-batch-5 code:

61  checkout was a plain POST, so a refresh placed a second order;
62  an order could not be changed before packing at all;
63  prices were static, with no rule table to read;
64  there was no gift card table, and the first implementation wrote
    ``order_id = 0`` into ``gift_card_ledger`` (a foreign key) before the order
    existed, which aborted the whole checkout;
65  buying more copies than the warehouse held was a hard error rather than a
    backorder, and ``books.stock`` could be driven negative.

The money assertions are all integer paise, like the rest of the app.
"""
import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner import backorders, giftcards
from pageturner.db import get_db


class BatchFiveTestCase(unittest.TestCase):
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

    def book_id(self):
        with self.app.app_context():
            return get_db().execute(
                "SELECT id FROM books ORDER BY id LIMIT 1"
            ).fetchone()["id"]

    def stock_of(self, book_id):
        with self.app.app_context():
            return get_db().execute(
                "SELECT stock FROM books WHERE id = ?", (book_id,)
            ).fetchone()["stock"]

    def checkout_payload(self):
        """GET /checkout and return the exact form body a browser would send.

        The GET is what creates the stock reservation and the single-use
        checkout token, so posting without it is (correctly) refused.
        """
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
            "pincode": "411001",
        }

    def submit_order(self, payload, **overrides):
        body = dict(payload)
        body.update(overrides)
        return self.client.post("/checkout", data=body)

    def order_rows(self, user_id):
        with self.app.app_context():
            return get_db().execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()

    def add_to_cart(self, book_id, quantity=1):
        response = self.post(
            f"/cart/add/{book_id}", {"quantity": str(quantity)}
        )
        self.assertIn(response.status_code, (302, 303))

    # =================================================================
    # Requirement 61 — a replayed checkout must not place a second order
    # =================================================================
    def test_replayed_checkout_places_exactly_one_order(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=20000, stock=10)
        self.add_to_cart(book, 2)

        payload = self.checkout_payload()
        first = self.submit_order(payload)
        self.assertEqual(first.status_code, 302)

        orders = self.order_rows(user_id)
        self.assertEqual(len(orders), 1, "first POST should place one order")
        first_order_id = orders[0]["id"]
        self.assertIn(f"/order/{first_order_id}", first.headers["Location"])

        # A refresh/Back button re-sends the same body. Only the CSRF token is
        # refreshed, exactly as a browser would re-render the page.
        replay = self.submit_order(payload, csrf_token=self.token("/"))
        self.assertEqual(replay.status_code, 302)
        self.assertIn(
            f"/order/{first_order_id}",
            replay.headers["Location"],
            "a replay must land on the order it already placed",
        )

        self.assertEqual(
            len(self.order_rows(user_id)), 1, "the replay placed a second order"
        )
        # And it must not have debited stock twice.
        self.assertEqual(self.stock_of(book), 8)

        with self.app.app_context():
            item = get_db().execute(
                "SELECT quantity FROM order_items WHERE order_id = ?",
                (first_order_id,),
            ).fetchone()
        self.assertEqual(item["quantity"], 2)

    def test_checkout_without_a_token_is_refused(self):
        user_id = self.register_and_login()
        book = self.prepare_book(stock=5)
        self.add_to_cart(book, 1)

        payload = self.checkout_payload()
        payload.pop("checkout_token")
        response = self.submit_order(payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.order_rows(user_id), [], "an untokened POST placed an order"
        )
        self.assertEqual(self.stock_of(book), 5)

    # =================================================================
    # Requirement 62 — change an order while it is only Placed
    # =================================================================
    def test_order_can_be_changed_before_packing(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=10000, stock=10)
        self.add_to_cart(book, 3)

        self.submit_order(self.checkout_payload())
        order = self.order_rows(user_id)[0]
        self.assertEqual(order["status"], "Placed")

        with self.app.app_context():
            connection = get_db()
            item = connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchone()
            item_id = item["id"]

        response = self.post(
            f"/order/{order['id']}/edit",
            {f"quantity_{item_id}": "1"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            connection = get_db()
            item = connection.execute(
                "SELECT * FROM order_items WHERE id = ?", (item_id,)
            ).fetchone()
            order = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order["id"],)
            ).fetchone()
            logs = connection.execute(
                "SELECT * FROM stock_log WHERE order_id = ? AND reason = ?",
                (order["id"], "order edit"),
            ).fetchall()

        self.assertEqual(item["quantity"], 1, "the quantity was not changed")
        # One copy came back to the shelf (3 -> 1), and the ledger proves it.
        self.assertEqual(self.stock_of(book), 9)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["change_amount"], 2)

        # The money is recomputed, not copied from the old totals: one copy of
        # a 10000 paise book, nothing else, so the total must come from the
        # same pipeline the checkout uses.
        self.assertEqual(order["subtotal_paise"], 10000)
        self.assertLess(order["total_paise"], order["subtotal_paise"] + 100000)

    def test_order_cannot_be_changed_once_packed(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=10000, stock=10)
        self.add_to_cart(book, 2)

        self.submit_order(self.checkout_payload())
        order = self.order_rows(user_id)[0]

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE orders SET status = 'Shipped' WHERE id = ?",
                (order["id"],),
            )
            connection.commit()
            item = connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchone()
            item_id = item["id"]

        response = self.post(
            f"/order/{order['id']}/edit",
            {f"quantity_{item_id}": "1"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            item = get_db().execute(
                "SELECT * FROM order_items WHERE id = ?", (item_id,)
            ).fetchone()

        self.assertEqual(
            item["quantity"], 2, "a shipped order must not be editable"
        )
        self.assertEqual(self.stock_of(book), 8)

    def test_editing_another_customers_order_is_a_404(self):
        self.register_and_login()
        book = self.prepare_book(stock=5)
        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload())
        order = self.order_rows(self.user_id())[0]

        self.client.get("/logout")
        self.register_and_login(email="other@example.com", name="Other Reader")

        with self.app.app_context():
            item_id = get_db().execute(
                "SELECT id FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchone()["id"]

        response = self.post(
            f"/order/{order['id']}/edit", {f"quantity_{item_id}": "1"}
        )
        self.assertEqual(response.status_code, 404)

    # =================================================================
    # Requirement 63 — pricing rules are data, evaluated at checkout
    # =================================================================
    def test_active_pricing_rule_reduces_the_order_total(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=20000, stock=10)

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                """
                INSERT INTO pricing_rules(
                    name, rule_type, book_id, min_quantity, percent_off, active
                )
                VALUES ('Ten percent off this book', 'book_percent', ?, 1, 10, 1)
                """,
                (book,),
            )
            connection.commit()

        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload())

        order = self.order_rows(user_id)[0]
        self.assertEqual(order["subtotal_paise"], 20000)
        self.assertEqual(
            order["rules_discount_paise"],
            2000,
            "a 10% rule on a 20000 paise book must save 2000 paise",
        )

    def test_inactive_pricing_rule_is_ignored(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=20000, stock=10)

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                """
                INSERT INTO pricing_rules(
                    name, rule_type, book_id, min_quantity, percent_off, active
                )
                VALUES ('Switched off', 'book_percent', ?, 1, 50, 0)
                """,
                (book,),
            )
            connection.commit()

        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload())

        order = self.order_rows(user_id)[0]
        self.assertEqual(order["rules_discount_paise"], 0)

    def test_stacked_rules_never_exceed_the_line_value(self):
        """Three 50% rules must not make a line negative."""
        with self.app.app_context():
            connection = get_db()
            book = connection.execute(
                "SELECT id FROM books ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            for index in range(3):
                connection.execute(
                    """
                    INSERT INTO pricing_rules(
                        name, rule_type, book_id, min_quantity, percent_off,
                        active
                    )
                    VALUES (?, 'book_percent', ?, 1, 50, 1)
                    """,
                    (f"Half off {index}", book),
                )
            connection.commit()

            from pageturner import pricing_rules

            discount, per_book = pricing_rules.evaluate(
                connection,
                [{"book_id": book, "quantity": 1, "line_total": 1000}],
            )

        self.assertEqual(discount, 900, "the cap is 90% of the line")
        self.assertEqual(per_book[book], 900)

    # =================================================================
    # Requirement 64 — gift cards
    # =================================================================
    def test_gift_card_can_be_bought_and_then_spent(self):
        user_id = self.register_and_login()

        # Buying a card is a normal order plus an issued card.
        response = self.post("/gift-cards", {"amount_paise": "50000"})
        self.assertEqual(response.status_code, 200)

        code_match = re.search(rb"([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", response.data)
        self.assertIsNotNone(code_match, "the card code was never shown")
        code = code_match.group(1).decode()

        with self.app.app_context():
            connection = get_db()
            card = connection.execute(
                "SELECT * FROM gift_cards ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(card["initial_paise"], 50000)
            self.assertEqual(card["issued_to_user_id"], user_id)
            # The plaintext code must not be recoverable from the database.
            self.assertNotIn(
                code.replace("-", ""), card["code_hash"] + card["code_last4"]
            )
            stored = connection.execute(
                "SELECT COUNT(*) AS n FROM gift_cards WHERE code_last4 = ?",
                (code[-4:],),
            ).fetchone()
            self.assertEqual(stored["n"], 1)

        book = self.prepare_book(price_paise=20000, stock=5)
        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload(), gift_card_code=code)

        order = self.order_rows(user_id)[-1]
        self.assertEqual(order["subtotal_paise"], 20000)
        # A gift card pays the whole amount owed, shipping and tax included -
        # it is not limited to the book subtotal.
        self.assertEqual(
            order["gift_card_paise"],
            order["subtotal_paise"]
            + order["shipping_paise"]
            + order["tax_paise"],
        )
        self.assertEqual(order["total_paise"], 0, "the card paid the whole order")
        self.assertEqual(order["gift_card_id"], card["id"])
        charged = order["gift_card_paise"]

        with self.app.app_context():
            connection = get_db()
            debit = connection.execute(
                """
                SELECT * FROM gift_card_ledger
                WHERE gift_card_id = ? AND amount_paise < 0
                """,
                (card["id"],),
            ).fetchone()
            balance = giftcards.gift_card_balance(connection, card["id"])

        self.assertEqual(debit["amount_paise"], -20000)
        self.assertEqual(
            debit["order_id"],
            order["id"],
            "the ledger row must point at the real order, not a placeholder",
        )
        self.assertEqual(balance, 30000)

    def test_spent_gift_card_balance_cannot_be_spent_twice(self):
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            card_id, code = giftcards.issue_gift_card(
                connection, 20000, issued_to_user_id=user_id
            )
            connection.commit()

        book = self.prepare_book(price_paise=20000, stock=10)

        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload(), gift_card_code=code)
        first = self.order_rows(user_id)[-1]
        self.assertEqual(first["gift_card_paise"], 20000)

        with self.app.app_context():
            connection = get_db()
            self.assertEqual(
                giftcards.gift_card_balance(connection, card_id),
                0,
                "the card should be empty after paying 20000",
            )

        # The second order tries the same (now empty) card: it must not pay.
        self.add_to_cart(book, 1)
        response = self.submit_order(self.checkout_payload(), gift_card_code=code)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            len(self.order_rows(user_id)), 2, "an empty card blocked the order"
        )
        second = self.order_rows(user_id)[-1]
        self.assertEqual(second["gift_card_paise"], 0)
        self.assertGreater(second["total_paise"], 0)

    def test_gift_card_redemption_defers_its_ledger_row(self):
        """The first implementation inserted order_id = 0 and broke the FK."""
        with self.app.app_context():
            connection = get_db()
            card_id, code = giftcards.issue_gift_card(connection, 50000)
            connection.commit()

            # Pricing a card before the order exists must not write a ledger
            # row (there is no order id to reference yet).
            priced_id, applied = giftcards.redeem_gift_card(
                connection, code, None, 30000
            )
            self.assertEqual((priced_id, applied), (card_id, 30000))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM gift_card_ledger "
                    "WHERE gift_card_id = ? AND amount_paise < 0",
                    (card_id,),
                ).fetchone()["n"],
                0,
            )
            self.assertEqual(
                giftcards.gift_card_balance(connection, card_id), 50000
            )

            # Once the order exists the debit is recorded against it.
            order_id = connection.execute(
                """
                INSERT INTO orders(user_id, name, phone, address, status)
                VALUES (NULL, 'N', '0000000000', 'A', 'Placed')
                """
            ).lastrowid
            giftcards.record_redemption(connection, card_id, order_id, applied)
            connection.commit()

            self.assertEqual(
                giftcards.gift_card_balance(connection, card_id), 20000
            )
            row = connection.execute(
                """
                SELECT order_id, amount_paise FROM gift_card_ledger
                WHERE gift_card_id = ? AND amount_paise < 0
                """,
                (card_id,),
            ).fetchone()
            self.assertEqual((row["order_id"], row["amount_paise"]),
                             (order_id, -30000))

    def test_cancelled_gift_card_is_refused(self):
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            card_id, code = giftcards.issue_gift_card(
                connection, 20000, issued_to_user_id=user_id
            )
            connection.commit()

        response = self.post(f"/gift-cards/{card_id}/disable")
        self.assertEqual(response.status_code, 302)

        book = self.prepare_book(price_paise=20000, stock=5)
        self.add_to_cart(book, 1)
        self.submit_order(self.checkout_payload(), gift_card_code=code)

        # The order is refused outright rather than quietly charging full price.
        self.assertEqual(self.order_rows(user_id), [])
        with self.app.app_context():
            self.assertEqual(
                giftcards.gift_card_balance(get_db(), card_id), 20000
            )

    def test_one_customer_cannot_cancel_another_customers_card(self):
        self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            card_id, _code = giftcards.issue_gift_card(connection, 20000)
            connection.commit()

        self.client.get("/logout")
        self.register_and_login(email="other@example.com", name="Other Reader")

        response = self.post(f"/gift-cards/{card_id}/disable")
        self.assertEqual(response.status_code, 404)

        with self.app.app_context():
            card = get_db().execute(
                "SELECT disabled_at FROM gift_cards WHERE id = ?", (card_id,)
            ).fetchone()
        self.assertIsNone(card["disabled_at"])

    # =================================================================
    # Requirement 65 — backorders instead of overselling
    # =================================================================
    def test_short_stock_ships_what_exists_and_queues_the_rest(self):
        user_id = self.register_and_login()
        book = self.prepare_book(price_paise=10000, stock=1)
        self.add_to_cart(book, 3)

        self.submit_order(self.checkout_payload())

        orders = self.order_rows(user_id)
        self.assertEqual(len(orders), 1, "short stock must not fail the order")

        self.assertEqual(
            self.stock_of(book), 0, "stock must never go negative"
        )

        with self.app.app_context():
            connection = get_db()
            queued = backorders.open_backorders(connection, book)

        self.assertEqual(len(queued), 1)
        self.assertEqual(
            queued[0]["quantity"],
            2,
            "the backorder covers only the copies the shelf did not have",
        )
        self.assertEqual(queued[0]["fulfilled_quantity"], 0)
        self.assertEqual(queued[0]["order_id"], orders[0]["id"])

        with self.app.app_context():
            connection = get_db()
            sold = connection.execute(
                "SELECT quantity FROM order_items WHERE order_id = ?",
                (orders[0]["id"],),
            ).fetchone()["quantity"]
        self.assertEqual(
            sold, 3, "the customer is billed for every copy they asked for"
        )

    def test_backorder_is_fulfilled_oldest_first_on_restock(self):
        first_user = self.register_and_login()
        book = self.prepare_book(price_paise=10000, stock=0)
        self.add_to_cart(book, 2)
        self.submit_order(self.checkout_payload())
        first_order = self.order_rows(first_user)[0]

        self.client.get("/logout")
        second_user = self.register_and_login(
            email="second@example.com", name="Second Reader"
        )
        self.add_to_cart(book, 2)
        self.submit_order(self.checkout_payload())
        second_order = self.order_rows(second_user)[0]

        with self.app.app_context():
            connection = get_db()
            queued = backorders.open_backorders(connection, book)
            self.assertEqual([row["order_id"] for row in queued],
                             [first_order["id"], second_order["id"]])

            # One copy arrives: it must go to the customer who has waited
            # longest, not to the newest order.
            connection.execute(
                "UPDATE books SET stock = 1 WHERE id = ?", (book,)
            )
            shipped = backorders.allocate_backorders(connection, book)
            connection.commit()

            rows = connection.execute(
                """
                SELECT order_id, fulfilled_quantity FROM backorders
                WHERE book_id = ? ORDER BY id
                """,
                (book,),
            ).fetchall()

        self.assertEqual(shipped, 1)
        self.assertEqual(rows[0]["order_id"], first_order["id"])
        self.assertEqual(rows[0]["fulfilled_quantity"], 1)
        self.assertEqual(rows[1]["order_id"], second_order["id"])
        self.assertEqual(rows[1]["fulfilled_quantity"], 0)
        # The copy left the shelf for a real order.
        self.assertEqual(self.stock_of(book), 0)


if __name__ == "__main__":
    unittest.main()
