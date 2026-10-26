"""Requirements 54, 55, 66 and 68 - price history, bulk repricing,
optimistic locking and the tamper-evident audit chain.

These tests hit the real routes and then inspect the database, because the
requirements are about what is *stored*, not about what the response looks
like.
"""
import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner.audit import GENESIS_HASH, verify_chain
from pageturner.db import get_db
from pageturner.pricing import apply_percentage


class AdminPricingTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "test.db")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": self.database,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO users (name, email, password_hash, role) "
                "VALUES (?, ?, ?, 'admin')",
                ("Boss", "boss@example.com", "x",),
            )
            connection.commit()
            self.admin_id = connection.execute(
                "SELECT id FROM users WHERE email = 'boss@example.com'"
            ).fetchone()["id"]

        self.login_as(self.admin_id)

    def tearDown(self):
        self.temp_dir.cleanup()

    def login_as(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def token(self, path="/admin/books"):
        response = self.client.get(path)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, "CSRF token not found on " + path)
        return match.group(1).decode()

    def post(self, path, data=None, token_path="/admin/books"):
        payload = dict(data or {})
        payload["csrf_token"] = self.token(token_path)
        return self.client.post(path, data=payload, follow_redirects=True)

    def query(self, sql, params=()):
        with self.app.app_context():
            return get_db().execute(sql, params).fetchall()

    def scalar(self, sql, params=()):
        with self.app.app_context():
            return get_db().execute(sql, params).fetchone()[0]

    def book(self, book_id):
        rows = self.query("SELECT * FROM books WHERE id = ?", (book_id,))
        return rows[0] if rows else None

    def history(self, book_id):
        return self.query(
            "SELECT * FROM book_price_history WHERE book_id = ? ORDER BY id",
            (book_id,),
        )

    def edit_payload(self, book, price_paise, version=None):
        # The form field is rupees with 2 decimals; the DB stores integer
        # paise.  Sending paise here would silently multiply the price by 100.
        rupees = "%d.%02d" % (price_paise // 100, price_paise % 100)
        return {
            "title": book["title"],
            "author": book["author"],
            "category": book["category"],
            "price": rupees,
            "description": book["description"],
            "stock": str(book["stock"]),
            "color": book["color"],
            "release_date": book["release_date"] or "",
            "version": str(book["version"] if version is None else version),
        }


class PriceHistoryTests(AdminPricingTestBase):
    """Requirement 54 - every price is kept, with who changed it and why."""

    def test_editing_a_price_keeps_the_old_value_in_history(self):
        book = self.book(1)
        old_price = book["price_paise"]
        new_price = old_price + 5000

        self.post("/admin/books/edit/1", self.edit_payload(book, new_price))

        self.assertEqual(self.book(1)["price_paise"], new_price)

        history = self.history(1)
        # One seeded baseline row plus the recorded change.
        change = history[-1]
        self.assertEqual(change["old_price_paise"], old_price)
        self.assertEqual(change["new_price_paise"], new_price)
        self.assertEqual(change["actor_user_id"], self.admin_id)
        self.assertEqual(change["reason"], "manual edit")
        self.assertIsNotNone(change["changed_at"])

        # The baseline row is preserved, so the trail starts from a known price.
        self.assertIsNone(history[0]["old_price_paise"])
        self.assertEqual(history[0]["new_price_paise"], old_price)

    def test_saving_an_unchanged_price_adds_no_history_noise(self):
        book = self.book(1)
        before = len(self.history(1))

        self.post(
            "/admin/books/edit/1",
            self.edit_payload(book, book["price_paise"]),
        )

        self.assertEqual(len(self.history(1)), before)

    def test_adding_a_book_records_its_first_price(self):
        response = self.post("/admin/books/add", {
            "title": "History Probe",
            "author": "A. Writer",
            "category": "Fiction",
            "price": "123.45",
            "description": "Probe.",
            "stock": "3",
            "color": "#e8f1ef",
            "release_date": "",
        })
        self.assertEqual(response.status_code, 200)

        rows = self.query(
            "SELECT * FROM books WHERE title = 'History Probe'"
        )
        self.assertEqual(len(rows), 1)
        history = self.history(rows[0]["id"])
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0]["old_price_paise"])
        self.assertEqual(history[0]["new_price_paise"], 12345)

    def test_price_history_endpoint_is_admin_only_and_newest_first(self):
        book = self.book(1)
        first = book["price_paise"] + 1000
        second = first + 1000

        self.post("/admin/books/edit/1", self.edit_payload(book, first))
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, second))

        response = self.client.get("/admin/books/1/price-history")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["current_price_paise"], second)
        self.assertGreaterEqual(len(payload["history"]), 2)
        self.assertEqual(
            payload["history"][0]["new_price_paise"], second,
            "history must be newest first",
        )

        # A signed-in customer gets no pricing data at all.
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO users (name, email, password_hash, role) "
                "VALUES ('Shopper', 'shopper@example.com', 'x', 'customer')"
            )
            connection.commit()
            customer_id = connection.execute(
                "SELECT id FROM users WHERE email = 'shopper@example.com'"
            ).fetchone()["id"]

        self.login_as(customer_id)
        self.assertEqual(
            self.client.get("/admin/books/1/price-history").status_code, 403
        )


class BulkPriceEditTests(AdminPricingTestBase):
    """Requirement 55 - reprice many books in one operation."""

    def test_bulk_increase_rounds_half_up_and_logs_each_book(self):
        books = [self.book(i) for i in (1, 2)]
        expected = {
            b["id"]: apply_percentage(b["price_paise"], 15, "increase")
            for b in books
        }

        self.post("/admin/books/prices/bulk", {
            "book_id": [str(b["id"]) for b in books],
            "percent": "15",
        })

        for book_id, price in expected.items():
            self.assertEqual(self.book(book_id)["price_paise"], price)
            self.assertEqual(
                self.history(book_id)[-1]["new_price_paise"], price
            )
            self.assertIn(
                "bulk +15%", self.history(book_id)[-1]["reason"]
            )

    def test_bulk_discount_leaves_unselected_books_alone(self):
        selected = self.book(1)
        untouched = self.book(2)

        self.post("/admin/books/prices/bulk", {
            "book_id": [str(selected["id"])],
            "percent": "-25",
        })

        self.assertEqual(
            self.book(1)["price_paise"],
            apply_percentage(selected["price_paise"], 25, "discount"),
        )
        self.assertEqual(self.book(2)["price_paise"], untouched["price_paise"])

    def test_out_of_range_percentage_changes_nothing(self):
        before = self.book(1)["price_paise"]
        history_before = len(self.history(1))

        for percent in ("-100", "501", "abc"):
            self.post("/admin/books/prices/bulk", {
                "book_id": ["1"],
                "percent": percent,
            })
            self.assertEqual(self.book(1)["price_paise"], before)
            self.assertEqual(len(self.history(1)), history_before)

    def test_bulk_without_a_selection_changes_nothing(self):
        before = self.book(1)["price_paise"]

        self.post("/admin/books/prices/bulk", {"percent": "10", "book_id": []})

        self.assertEqual(self.book(1)["price_paise"], before)

    def test_half_up_rounding_is_exact_not_float(self):
        # 5% of 1999 = 99.95 -> 100 paise, so the new price is 2099.
        self.assertEqual(apply_percentage(1999, 5, "increase"), 2099)
        # 33% off 100 = 67 paise exactly.
        self.assertEqual(apply_percentage(100, 33, "discount"), 67)
        # A .5 paise result rounds up, not to even.
        self.assertEqual(apply_percentage(1, 50, "increase"), 2)


class OptimisticLockingTests(AdminPricingTestBase):
    """Requirement 66 - a stale edit form can never clobber a newer save."""

    def test_stale_version_is_rejected_and_the_row_is_unchanged(self):
        book = self.book(1)
        stale_version = book["version"]

        # Another admin saves first, bumping the version.
        self.post(
            "/admin/books/edit/1",
            self.edit_payload(book, book["price_paise"] + 100),
        )
        after_first = self.book(1)
        self.assertEqual(after_first["version"], stale_version + 1)

        # Now the first admin's stale form arrives.
        stale_payload = self.edit_payload(book, book["price_paise"] + 900)
        stale_payload["title"] = "Overwritten Title"
        stale_payload["version"] = str(stale_version)
        response = self.post("/admin/books/edit/1", stale_payload)

        self.assertEqual(response.status_code, 200)
        current = self.book(1)
        self.assertEqual(current["title"], after_first["title"])
        self.assertEqual(current["price_paise"], after_first["price_paise"])
        self.assertEqual(current["version"], after_first["version"])
        self.assertIn(b"changed by someone else", response.data)

    def test_missing_version_is_treated_as_a_conflict(self):
        book = self.book(1)
        payload = self.edit_payload(book, book["price_paise"] + 100)
        del payload["version"]

        response = self.post("/admin/books/edit/1", payload)

        self.assertEqual(response.status_code, 200)
        # No silent overwrite just because the field was absent.
        self.assertEqual(self.book(1)["price_paise"], book["price_paise"])
        self.assertEqual(self.book(1)["version"], book["version"])

    def test_successful_edit_bumps_the_version(self):
        book = self.book(1)

        self.post(
            "/admin/books/edit/1",
            self.edit_payload(book, book["price_paise"] + 100),
        )

        self.assertEqual(self.book(1)["version"], book["version"] + 1)

    def test_edit_form_carries_the_current_version(self):
        response = self.client.get("/admin/books")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('name="version"', body)


class AuditChainTests(AdminPricingTestBase):
    """Requirement 68 - the admin log is tamper-evident."""

    def audit_rows(self):
        return self.query("SELECT * FROM admin_log ORDER BY id")

    def verify(self):
        with self.app.app_context():
            return verify_chain(get_db())

    def test_actions_append_a_verifiable_chain(self):
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 51234))
        self.post("/admin/books/prices/bulk", {
            "book_id": ["1"], "percent": "10",
        })

        rows = self.audit_rows()
        self.assertGreaterEqual(len(rows), 2, "no audit rows were written")
        self.assertEqual(rows[0]["prev_hash"], GENESIS_HASH)

        for previous, current in zip(rows, rows[1:]):
            self.assertEqual(current["prev_hash"], previous["row_hash"])

        result = self.verify()
        self.assertTrue(result["ok"], result["reason"])

    def test_editing_a_stored_row_breaks_the_chain(self):
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 51234))
        self.assertTrue(self.verify()["ok"])

        with self.app.app_context():
            connection = get_db()
            target = connection.execute(
                "SELECT id FROM admin_log ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            connection.execute(
                "UPDATE admin_log SET detail = 'forged detail' WHERE id = ?",
                (target,),
            )
            connection.commit()

        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("modified", result["reason"])

    def test_deleting_a_row_breaks_the_chain(self):
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 51234))
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 61234))

        with self.app.app_context():
            connection = get_db()
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM admin_log ORDER BY id"
                ).fetchall()
            ]
            self.assertGreaterEqual(len(ids), 2)
            connection.execute(
                "DELETE FROM admin_log WHERE id = ?", (ids[0],)
            )
            connection.commit()

        self.assertFalse(self.verify()["ok"])

    def test_removing_the_newest_row_is_detected_by_the_head_pointer(self):
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 51234))

        with self.app.app_context():
            connection = get_db()
            newest = connection.execute(
                "SELECT id FROM admin_log ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            connection.execute(
                "DELETE FROM admin_log WHERE id = ?", (newest,)
            )
            connection.commit()

        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("head", result["reason"].lower())

    def test_clearing_a_hash_is_detected(self):
        book = self.book(1)
        self.post("/admin/books/edit/1", self.edit_payload(book, 51234))

        with self.app.app_context():
            connection = get_db()
            connection.execute("UPDATE admin_log SET row_hash = NULL")
            connection.commit()

        self.assertFalse(self.verify()["ok"])

    def test_every_admin_write_path_is_logged(self):
        """A price change must not be auditable only through price history."""
        book = self.book(1)
        before = len(self.audit_rows())

        self.post(
            "/admin/books/edit/1",
            self.edit_payload(book, book["price_paise"] + 100),
        )

        rows = self.audit_rows()
        self.assertGreater(len(rows), before)
        self.assertEqual(rows[-1]["action"], "book_edited")
        self.assertEqual(rows[-1]["actor_user_id"], self.admin_id)


if __name__ == "__main__":
    unittest.main()
