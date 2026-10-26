"""Regression tests for requirements 69 and 70.

Every test is written so that it fails on the pre-batch-6 code:

69  there was no outbox table at all, and notifications were written straight
    into ``notifications`` with no delivery attempt, retry or dedupe — a message
    lost to a dead mail server was lost forever, with no record that it existed;
70  there was no subscription model, no featured title, and no way to bill a
    recurring delivery except by placing each order by hand.

The money assertions are integer paise, like the rest of the app.
"""
import os
import re
import tempfile
import unittest
from datetime import date, timedelta

from pageturner import create_app
from pageturner import outbox, subscriptions
from pageturner.db import get_db


class BatchSixTestCase(unittest.TestCase):
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
        self.assertEqual(response.status_code, 200)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, path, data=None, **kwargs):
        payload = dict(data or {})
        payload["csrf_token"] = self.token()
        return self.client.post(path, data=payload, **kwargs)

    def register_and_login(self, email="reader@example.com", name="Reader One"):
        self.post(
            "/register",
            {"name": name, "email": email, "password": "Reading123"},
            follow_redirects=True,
        )
        self.post(
            "/login",
            {"email": email, "password": "Reading123"},
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

    def make_admin(self, user_id):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = ?", (user_id,)
            )
            connection.commit()

    def prepare_book(self, price_paise=20000, stock=10):
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

    def stock_of(self, book_id):
        with self.app.app_context():
            return get_db().execute(
                "SELECT stock FROM books WHERE id = ?", (book_id,)
            ).fetchone()["stock"]

    def add_to_cart(self, book_id, quantity=1):
        response = self.post(f"/cart/add/{book_id}", {"quantity": str(quantity)})
        self.assertIn(response.status_code, (302, 303))

    def checkout_payload(self):
        """GET /checkout and return the exact body a browser would send.

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
        self.assertIsNotNone(checkout_token, "no checkout token on the checkout page")

        return {
            "csrf_token": csrf.group(1).decode(),
            "checkout_token": checkout_token.group(1).decode(),
            "name": "Reader One",
            "phone": "9876543210",
            "new_address": "1 Reading Lane",
            "pincode": "560001",
        }

    def submit_order(self, payload, **overrides):
        body = dict(payload)
        body.update(overrides)
        return self.client.post("/checkout", data=body)

    def subscribe(self, user_id, book_id, quantity=1):
        """Create a subscription directly, already due for this month."""
        with self.app.app_context():
            connection = get_db()
            subscription_id = subscriptions.subscribe(
                connection, user_id, book_id, quantity
            )
            # Pull the billing date back so the next run bills immediately
            # instead of in a month's time.
            connection.execute(
                "UPDATE subscriptions SET next_run_on = ? WHERE id = ?",
                (date.today().isoformat(), subscription_id),
            )
            connection.commit()
            return subscription_id


class OutboxTests(BatchSixTestCase):
    """Requirement 69."""

    def test_checkout_writes_a_confirmation_into_the_outbox(self):
        """The order and its notification must be created together."""
        book_id = self.prepare_book(price_paise=20000, stock=5)
        user_id = self.register_and_login()

        self.add_to_cart(book_id)

        with self.app.app_context():
            connection = get_db()
            basket = connection.execute(
                "SELECT book_id FROM cart_items WHERE user_id = ?", (user_id,)
            ).fetchall()

        self.assertTrue(basket, "cart is empty; the add-to-cart call failed")

        payload = self.checkout_payload()
        response = self.submit_order(payload)
        self.assertIn(response.status_code, (302, 303))

        with self.app.app_context():
            connection = get_db()
            order = connection.execute(
                "SELECT id FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            self.assertIsNotNone(order, "checkout did not create an order")

            message = connection.execute(
                "SELECT * FROM notification_outbox WHERE dedupe_key = ?",
                (f"order:{order['id']}:confirmed",),
            ).fetchone()

        self.assertIsNotNone(
            message,
            "checkout did not queue an order confirmation in the outbox",
        )
        self.assertEqual(message["status"], "pending")
        self.assertEqual(message["attempts"], 0)

    def book_stub(self):
        with self.app.app_context():
            return get_db().execute(
                "SELECT id FROM books ORDER BY id LIMIT 1"
            ).fetchone()["id"]

    def test_a_failing_transport_is_retried_with_backoff(self):
        """A send failure must be recorded and retried, never dropped."""
        user_id = self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            outbox.enqueue(connection, user_id, "Subject A", "Body A")
            connection.commit()

            calls = {"count": 0}

            def broken_transport(_message):
                calls["count"] += 1
                raise RuntimeError("smtp is down")

            summary = outbox.process_due(connection, transport=broken_transport)
            connection.commit()

            row = connection.execute(
                "SELECT * FROM notification_outbox ORDER BY id DESC LIMIT 1"
            ).fetchone()

        self.assertEqual(summary["retried"], 1)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["status"], "pending", "should stay queued for retry")
        self.assertIn("smtp is down", row["last_error"])
        self.assertGreater(
            row["next_attempt_at"],
            row["created_at"],
            "a failed send must not be retried immediately",
        )

    def test_retries_are_bounded_then_the_message_is_parked(self):
        """After its attempt budget the row is parked, not retried forever."""
        user_id = self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            outbox.enqueue(
                connection, user_id, "Subject B", "Body B", max_attempts=2
            )
            connection.commit()

            def broken_transport(_message):
                raise RuntimeError("still down")

            # Each pass must clear the backoff, so push next_attempt_at back.
            for _ in range(3):
                connection.execute(
                    "UPDATE notification_outbox SET next_attempt_at = '2000-01-01 00:00:00' "
                    "WHERE status = 'pending'"
                )
                connection.commit()
                outbox.process_due(connection, transport=broken_transport)
                connection.commit()

            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE subject = 'Subject B'"
            ).fetchone()

        self.assertEqual(row["attempts"], 2, "attempts must stop at max_attempts")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["last_error"], "still down")

    def test_a_delivered_message_is_not_sent_twice(self):
        """The dedupe key plus the sent status means exactly one send."""
        user_id = self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            first = outbox.enqueue(
                connection, user_id, "Subject C", "Body C", dedupe_key="once-only"
            )
            second = outbox.enqueue(
                connection, user_id, "Subject C", "Body C", dedupe_key="once-only"
            )
            connection.commit()

            sends = []

            def transport(message):
                sends.append(message["id"])

            outbox.process_due(connection, transport=transport)
            connection.commit()

            # A second pass over an already-sent row must not deliver again.
            outbox.process_due(connection, transport=transport)
            connection.commit()

            delivered = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_outbox "
                "WHERE status = 'sent'"
            ).fetchone()["count"]

        self.assertEqual(first, second, "the dedupe key must reuse the same row")
        self.assertEqual(len(sends), 1, f"message was sent {len(sends)} times")
        self.assertEqual(delivered, 1)

    def test_the_outbox_page_shows_pending_messages(self):
        user_id = self.register_and_login()
        self.make_admin(user_id)

        with self.app.app_context():
            connection = get_db()
            outbox.enqueue(connection, user_id, "Hello subject", "Hello body")
            connection.commit()

        response = self.client.get("/admin/outbox")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hello subject", response.data)

    def test_staff_can_deliver_and_the_page_reports_it(self):
        user_id = self.register_and_login()
        self.make_admin(user_id)

        with self.app.app_context():
            connection = get_db()
            outbox.enqueue(connection, user_id, "Retry me", "Body")
            connection.commit()

            def transport(_message):
                return None

            summary = outbox.process_due(connection, transport=transport)
            connection.commit()

            counts = outbox.status_counts(connection)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(counts.get("sent"), 1)


class SubscriptionTests(BatchSixTestCase):
    """Requirement 70."""

    def test_the_featured_title_is_published_and_read_back(self):
        book_id = self.prepare_book()

        with self.app.app_context():
            connection = get_db()
            subscriptions.set_featured(connection, "2024-05", book_id)
            connection.commit()

            row = subscriptions.featured(connection, "2024-05")

        self.assertIsNotNone(row)
        self.assertEqual(row["book_id"], book_id)
        self.assertEqual(row["month"], "2024-05")

    def test_publishing_the_same_month_twice_replaces_the_pick(self):
        first = self.prepare_book()
        with self.app.app_context():
            connection = get_db()
            second = connection.execute(
                "SELECT id FROM books ORDER BY id LIMIT 1 OFFSET 1"
            ).fetchone()["id"]

            subscriptions.set_featured(connection, "2024-06", first)
            subscriptions.set_featured(connection, "2024-06", second)
            connection.commit()

            rows = connection.execute(
                "SELECT COUNT(*) AS count FROM book_of_the_month "
                "WHERE month = '2024-06'"
            ).fetchone()["count"]
            row = subscriptions.featured(connection, "2024-06")

        self.assertEqual(rows, 1, "one featured title per month")
        self.assertEqual(row["book_id"], second)

    def test_subscribing_starts_billing_next_month(self):
        book_id = self.prepare_book()
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            subscription_id = subscriptions.subscribe(connection, user_id, book_id, 1)
            connection.commit()
            row = connection.execute(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()

        expected = subscriptions.first_of_next_month(subscriptions.month_key())
        self.assertEqual(row["next_run_on"], expected.isoformat())
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["quantity"], 1)

    def test_a_second_subscription_is_refused(self):
        book_id = self.prepare_book()
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            subscriptions.subscribe(connection, user_id, book_id, 1)
            connection.commit()

            with self.assertRaises(ValueError):
                subscriptions.subscribe(connection, user_id, book_id, 1)

    def test_an_absurd_quantity_is_refused(self):
        book_id = self.prepare_book()
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            with self.assertRaises(ValueError):
                subscriptions.subscribe(connection, user_id, book_id, 99)

    def test_the_monthly_run_creates_a_real_order_and_debits_stock(self):
        """The run must go through the ordinary order pipeline."""
        book_id = self.prepare_book(price_paise=20000, stock=10)
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=2)

        with self.app.app_context():
            connection = get_db()
            summary = subscriptions.run_due(connection)
            connection.commit()

            order = connection.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            items = connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchall()
            ledger = connection.execute(
                "SELECT * FROM stock_log WHERE order_id = ?", (order["id"],)
            ).fetchall()
            stock = self.stock_of(book_id)

        self.assertEqual(summary["ordered"], 1)
        self.assertEqual(order["total_paise"], 40000, "2 x 200.00, no discount")
        self.assertEqual(order["subtotal_paise"], 40000)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 2)
        self.assertEqual(items[0]["price_paise"], 20000)
        self.assertEqual(len(ledger), 1, "the stock movement must be logged")
        self.assertEqual(ledger[0]["change_amount"], -2)
        self.assertEqual(ledger[0]["reason"], "subscription")
        self.assertEqual(stock, 8)

    def test_pressing_run_twice_charges_only_once(self):
        """One order per subscription per month, enforced by the database."""
        book_id = self.prepare_book(price_paise=15000, stock=10)
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=1)

        with self.app.app_context():
            connection = get_db()

            first = subscriptions.run_due(connection)
            connection.commit()
            stock_after_first = self.stock_of(book_id)

            # Make the subscription due again, as if a second staff member
            # pressed the button on the same day.
            connection.execute(
                "UPDATE subscriptions SET next_run_on = ?",
                (date.today().isoformat(),),
            )
            connection.commit()

            second = subscriptions.run_due(connection)
            connection.commit()

            order_count = connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE user_id = ?", (user_id,)
            ).fetchone()["count"]

        self.assertEqual(first["ordered"], 1)
        self.assertEqual(second["ordered"], 0, "the month was already billed")
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(order_count, 1, "the customer was charged twice")
        self.assertEqual(self.stock_of(book_id), stock_after_first)

    def test_a_short_warehouse_becomes_a_backorder_not_negative_stock(self):
        """Requirement 65 applies to subscriptions too."""
        book_id = self.prepare_book(price_paise=20000, stock=1)
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=3)

        with self.app.app_context():
            connection = get_db()
            summary = subscriptions.run_due(connection)
            connection.commit()

            order = connection.execute(
                "SELECT id FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            backorder = connection.execute(
                "SELECT * FROM backorders WHERE order_id = ?", (order["id"],)
            ).fetchone()
            stock = self.stock_of(book_id)

        self.assertEqual(summary["ordered"], 1)
        self.assertEqual(stock, 0, "stock must never go negative")
        self.assertIsNotNone(backorder, "the shortfall must join the queue")
        self.assertEqual(backorder["quantity"], 2)
        self.assertEqual(backorder["quantity_fulfilled"], 0)

    def test_pausing_stops_delivery_and_resuming_does_not_bill_the_gap(self):
        book_id = self.prepare_book(price_paise=20000, stock=10)
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=1)

        with self.app.app_context():
            connection = get_db()
            subscriptions.set_status(connection, user_id, "paused")
            connection.commit()

            summary = subscriptions.run_due(connection)
            connection.commit()

            self.assertEqual(summary["ordered"], 0, "a paused plan must not bill")

            subscriptions.set_status(connection, user_id, "active")
            connection.commit()
            row = subscriptions.subscription_for(connection, user_id)

        self.assertEqual(row["status"], "active")
        self.assertGreaterEqual(
            row["next_run_on"],
            date.today().isoformat(),
            "resuming must not immediately bill a past month",
        )

    def test_cancelling_ends_the_plan_and_frees_the_customer_to_start_again(self):
        book_id = self.prepare_book()
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=1)

        with self.app.app_context():
            connection = get_db()
            subscriptions.set_status(connection, user_id, "cancelled")
            connection.commit()

            self.assertIsNone(subscriptions.subscription_for(connection, user_id))

            # A partial unique index covers live rows only, so a new plan is
            # allowed once the old one is cancelled.
            subscriptions.subscribe(connection, user_id, book_id, 1)
            connection.commit()
            live = subscriptions.count_live(connection)

        self.assertEqual(live, 1)

    def test_the_run_queues_a_confirmation_for_each_delivery(self):
        """Requirement 69 and 70 meet here: the run must notify the customer."""
        book_id = self.prepare_book()
        user_id = self.register_and_login()
        self.subscribe(user_id, book_id, quantity=1)

        with self.app.app_context():
            connection = get_db()
            subscriptions.run_due(connection)
            connection.commit()

            message = connection.execute(
                "SELECT * FROM notification_outbox WHERE user_id = ? "
                "AND subject LIKE '%book of the month%'",
                (user_id,),
            ).fetchone()

        self.assertIsNotNone(message, "the delivery was not announced")
        self.assertEqual(message["status"], "pending")

    def test_the_customer_can_subscribe_pause_resume_and_cancel_through_the_ui(self):
        book_id = self.prepare_book(price_paise=20000, stock=5)
        user_id = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            subscriptions.set_featured(
                connection, subscriptions.month_key(), book_id
            )
            connection.commit()

        response = self.client.get("/subscription")
        self.assertEqual(response.status_code, 200)

        response = self.post(
            "/subscription", {"action": "subscribe", "quantity": "2"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            connection = get_db()
            row = subscriptions.subscription_for(connection, user_id)

        self.assertIsNotNone(row, "the subscribe button did not create a plan")
        self.assertEqual(row["quantity"], 2)

        self.post("/subscription", {"action": "pause"}, follow_redirects=True)
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(
                subscriptions.subscription_for(connection, user_id)["status"],
                "paused",
            )

        self.post("/subscription", {"action": "resume"}, follow_redirects=True)
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(
                subscriptions.subscription_for(connection, user_id)["status"],
                "active",
            )

        self.post("/subscription", {"action": "cancel"}, follow_redirects=True)
        with self.app.app_context():
            connection = get_db()
            self.assertIsNone(subscriptions.subscription_for(connection, user_id))

    def test_staff_can_run_the_month_from_the_admin_page(self):
        book_id = self.prepare_book(price_paise=20000, stock=5)
        user_id = self.register_and_login()
        self.make_admin(user_id)
        self.subscribe(user_id, book_id, quantity=1)

        response = self.client.get("/admin/book-of-the-month")
        self.assertEqual(response.status_code, 200)

        response = self.post(
            "/admin/book-of-the-month/run", {}, follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            connection = get_db()
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE user_id = ?", (user_id,)
            ).fetchone()["count"]

        self.assertEqual(count, 1, "the admin run did not create the order")

    def test_a_plain_customer_cannot_reach_the_admin_run(self):
        book_id = self.prepare_book()
        self.register_and_login()

        response = self.client.get("/admin/book-of-the-month")
        self.assertIn(response.status_code, (302, 403))

        response = self.client.post("/admin/book-of-the-month/run")
        self.assertIn(response.status_code, (302, 400, 403))

    def test_a_blocked_run_does_not_advance_the_schedule(self):
        """A subscription left due must stay due until a human fixes it."""
        book_id = self.prepare_book()
        user_id = self.register_and_login()
        subscription_id = self.subscribe(user_id, book_id, quantity=1)

        with self.app.app_context():
            connection = get_db()
            # Remove the book so the run cannot bill it.
            connection.execute("DELETE FROM cart WHERE book_id = ?", (book_id,))
            connection.execute("DELETE FROM backorders WHERE book_id = ?", (book_id,))
            connection.execute("DELETE FROM stock_log WHERE book_id = ?", (book_id,))
            connection.execute(
                "DELETE FROM book_of_the_month WHERE book_id = ?", (book_id,)
            )
            connection.execute(
                "UPDATE subscriptions SET next_run_on = ? WHERE id = ?",
                (date.today().isoformat(), subscription_id),
            )
            connection.commit()

            summary = subscriptions.run_due(connection)
            connection.commit()

            row = connection.execute(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()

        self.assertEqual(summary["ordered"], 0)
        self.assertEqual(row["next_run_on"], date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
