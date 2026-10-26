"""Regression tests for requirements 50, 51, 52, 53 and 59.

Each test is written to fail against the pre-batch-4 code: the tables did not
exist, the rating average ignored ``reviews.hidden``, book requests had no
grouping counter, wishlist links were not revocable, and account closure did
not exist at all.
"""
import os
import re
import sqlite3
import tempfile
import unittest

from pageturner import create_app
from pageturner.db import get_db
from pageturner.privacy import anonymize_user
from pageturner.reviews import AVERAGE_RATING_SQL, REVIEW_COUNT_SQL


class BatchFourTestCase(unittest.TestCase):
    """Shared app fixture: one temp database per test."""

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

    def user_id(self, email):
        with self.app.app_context():
            row = get_db().execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()

        self.assertIsNotNone(row, f"{email} was not created")
        return row["id"]

    def login_as_admin(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = 1"
            )
            connection.commit()
            email = connection.execute(
                "SELECT email FROM users WHERE id = 1"
            ).fetchone()["email"]

        with self.client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "admin"

        return email

    def add_book(self, title="Test Title", stock=5, price=49900):
        with self.app.app_context():
            connection = get_db()
            cursor = connection.execute(
                """
                INSERT INTO books (title, author, price_paise, stock, color,
                                   category, description)
                VALUES (?, 'Test Author', ?, ?, '#123456', 'Fiction', 'A book.')
                """,
                (title, price, stock),
            )
            connection.commit()
            return cursor.lastrowid

    def add_user(self, email, name="Second Reader", password="Reading123"):
        """Create an extra account directly, so a review can be attributed."""
        from werkzeug.security import generate_password_hash

        with self.app.app_context():
            connection = get_db()
            cursor = connection.execute(
                """
                INSERT INTO users (name, email, password_hash, role)
                VALUES (?, ?, ?, 'customer')
                """,
                (name, email, generate_password_hash(password)),
            )
            connection.commit()
            return cursor.lastrowid

    def add_review(self, book_id, user_id, rating=5, comment="Good", name="Reader"):
        with self.app.app_context():
            connection = get_db()
            cursor = connection.execute(
                """
                INSERT INTO reviews (book_id, user_id, name, rating, comment)
                VALUES (?, ?, ?, ?, ?)
                """,
                (book_id, user_id, name, rating, comment),
            )
            connection.commit()
            return cursor.lastrowid


class HiddenReviewRatingTests(BatchFourTestCase):
    """Requirement 51: hiding a review must change the average *and* count."""

    def setUp(self):
        super().setUp()
        self.book_id = self.add_book()
        self.author_id = self.register_and_login()
        self.other_id = self.add_user("second@example.com")

    def rating_row(self):
        with self.app.app_context():
            return get_db().execute(
                f"SELECT {AVERAGE_RATING_SQL} AS avg_rating, "
                f"{REVIEW_COUNT_SQL} AS review_count "
                "FROM books b WHERE b.id = ?",
                (self.book_id,),
            ).fetchone()

    def test_visible_reviews_are_averaged(self):
        self.add_review(self.book_id, self.author_id, rating=4)
        self.add_review(self.book_id, self.other_id, rating=2)

        row = self.rating_row()

        self.assertAlmostEqual(row["avg_rating"], 3.0)
        self.assertEqual(row["review_count"], 2)

    def test_hidden_review_leaves_the_average_and_the_count(self):
        kept = self.add_review(self.book_id, self.author_id, rating=5)
        hidden = self.add_review(self.book_id, self.other_id, rating=1)

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE reviews SET hidden = 1 WHERE id = ?", (hidden,)
            )
            connection.commit()

        row = self.rating_row()

        # Pre-batch-4 SQL averaged 3.0 over 2 reviews; both must change.
        self.assertAlmostEqual(row["avg_rating"], 5.0)
        self.assertEqual(row["review_count"], 1)

        with self.app.app_context():
            visible = get_db().execute(
                "SELECT id FROM reviews WHERE book_id = ? AND hidden = 0",
                (self.book_id,),
            ).fetchall()

        self.assertEqual([review["id"] for review in visible], [kept])

    def test_storefront_page_agrees_with_the_helper(self):
        self.add_review(
            self.book_id, self.author_id, rating=5, comment="Kept comment"
        )
        hidden = self.add_review(
            self.book_id, self.other_id, rating=1, comment="Hidden comment"
        )

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE reviews SET hidden = 1 WHERE id = ?", (hidden,)
            )
            connection.commit()

        response = self.client.get(f"/book/{self.book_id}")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode()

        # The page must render the same number the helper computes.
        self.assertIn("Customer reviews (1)", body)
        self.assertIn("5.0 / 5", body)

        # The hidden review's text must not be published.
        self.assertIn("Kept comment", body)
        self.assertNotIn("Hidden comment", body)


class HelpfulVoteTests(BatchFourTestCase):
    """Requirement 50: one vote per user, enforced by the database."""

    def setUp(self):
        super().setUp()
        self.book_id = self.add_book()
        self.author_id = self.register_and_login()
        self.review_id = self.add_review(self.book_id, self.author_id)

    def vote_count(self):
        with self.app.app_context():
            return get_db().execute(
                "SELECT COUNT(*) AS c FROM review_votes WHERE review_id = ?",
                (self.review_id,),
            ).fetchone()["c"]

    def test_unique_constraint_blocks_a_second_vote(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO review_votes (review_id, user_id) VALUES (?, ?)",
                (self.review_id, self.author_id),
            )
            connection.commit()

        with self.app.app_context():
            with self.assertRaises(sqlite3.IntegrityError):
                get_db().execute(
                    "INSERT INTO review_votes (review_id, user_id) "
                    "VALUES (?, ?)",
                    (self.review_id, self.author_id),
                )

    def test_double_submit_is_idempotent(self):
        from pageturner.reviews import vote_helpful

        with self.app.app_context():
            connection = get_db()
            first = vote_helpful(connection, self.review_id, self.author_id)
            second = vote_helpful(connection, self.review_id, self.author_id)
            connection.commit()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.vote_count(), 1)

    def test_remove_is_idempotent(self):
        from pageturner.reviews import remove_helpful_vote, vote_helpful

        with self.app.app_context():
            connection = get_db()
            vote_helpful(connection, self.review_id, self.author_id)
            first = remove_helpful_vote(connection, self.review_id, self.author_id)
            second = remove_helpful_vote(connection, self.review_id, self.author_id)
            connection.commit()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.vote_count(), 0)


class ReviewReportTests(BatchFourTestCase):
    """Requirement 51: reports reach an admin queue where a decision is made."""

    def setUp(self):
        super().setUp()
        self.book_id = self.add_book()
        self.author_id = self.register_and_login()
        self.review_id = self.add_review(self.book_id, self.author_id)

    def test_one_report_per_reporter(self):
        from pageturner.reviews import report_review

        with self.app.app_context():
            connection = get_db()
            first = report_review(
                connection, self.review_id, self.author_id, "spam", "Ads"
            )
            second = report_review(
                connection, self.review_id, self.author_id, "abuse", "Rude"
            )
            connection.commit()

        self.assertTrue(first)
        self.assertFalse(second)

    def test_hiding_through_the_admin_queue_updates_the_rating(self):
        from pageturner.reviews import report_review

        reporter = self.register_and_login(email="reporter@example.com")

        with self.app.app_context():
            connection = get_db()
            report_review(connection, self.review_id, reporter, "abuse", "")
            connection.commit()

        self.login_as_admin()

        page = self.client.get("/admin/reviews?status=open")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Abusive or hateful language", page.data)

        response = self.post(f"/admin/reviews/{self.review_id}/hide")

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            connection = get_db()
            review = connection.execute(
                "SELECT hidden FROM reviews WHERE id = ?", (self.review_id,)
            ).fetchone()
            report = connection.execute(
                "SELECT status, resolved_by FROM review_reports "
                "WHERE review_id = ?",
                (self.review_id,),
            ).fetchone()

        self.assertEqual(review["hidden"], 1)
        self.assertEqual(report["status"], "hidden")
        self.assertEqual(report["resolved_by"], 2)

    def test_keeping_a_review_dismisses_the_reports(self):
        from pageturner.reviews import report_review

        reporter = self.register_and_login(email="reporter@example.com")

        with self.app.app_context():
            connection = get_db()
            report_review(connection, self.review_id, reporter, "spam", "")
            connection.commit()

        self.login_as_admin()
        self.post(f"/admin/reviews/{self.review_id}/keep")

        with self.app.app_context():
            connection = get_db()
            review = connection.execute(
                "SELECT hidden FROM reviews WHERE id = ?", (self.review_id,)
            ).fetchone()
            status = connection.execute(
                "SELECT status FROM review_reports WHERE review_id = ?",
                (self.review_id,),
            ).fetchone()["status"]

        self.assertEqual(review["hidden"], 0)
        self.assertEqual(status, "kept")


class BookRequestTests(BatchFourTestCase):
    """Requirement 52: requests group by title and sort by demand."""

    def setUp(self):
        super().setUp()
        self.register_and_login()

    def request_count(self, title):
        with self.app.app_context():
            row = get_db().execute(
                "SELECT request_count FROM book_requests "
                "WHERE normalized_title = ?",
                (title,),
            ).fetchone()

        return row["request_count"] if row else 0

    def test_normalization_groups_near_duplicates(self):
        from pageturner.book_requests import normalize_title

        variants = [
            "The  Midnight   Library",
            "the midnight library",
            "THE MIDNIGHT LIBRARY!",
            "The Midnight Library.",
        ]

        self.assertEqual(
            {normalize_title(title) for title in variants},
            {"the midnight library"},
        )

    def test_repeated_requests_share_one_row(self):
        for _ in range(3):
            self.post(
                "/request-book",
                {"title": "The Midnight Library", "author": "Matt Haig"},
                follow_redirects=True,
            )

        self.assertEqual(self.request_count("the midnight library"), 3)

        with self.app.app_context():
            total = get_db().execute(
                "SELECT COUNT(*) AS c FROM book_requests"
            ).fetchone()["c"]

        self.assertEqual(total, 1, "variants must not create duplicate rows")

    def test_admin_list_sorts_by_request_count(self):
        for _ in range(2):
            self.post(
                "/request-book", {"title": "Popular Book"}, follow_redirects=True
            )
        self.post("/request-book", {"title": "Quiet Book"}, follow_redirects=True)

        self.login_as_admin()

        with self.app.app_context():
            rows = get_db().execute(
                "SELECT title, request_count FROM book_requests "
                "ORDER BY request_count DESC, updated_at DESC"
            ).fetchall()

        self.assertEqual(
            [(row["title"], row["request_count"]) for row in rows],
            [("Popular Book", 2), ("Quiet Book", 1)],
        )

        page = self.client.get("/admin/book-requests")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Popular Book", page.data)

    def test_short_titles_are_rejected(self):
        self.post("/request-book", {"title": "a"}, follow_redirects=True)

        with self.app.app_context():
            total = get_db().execute(
                "SELECT COUNT(*) AS c FROM book_requests"
            ).fetchone()["c"]

        self.assertEqual(total, 0)


class WishlistShareTests(BatchFourTestCase):
    """Requirement 53: sharing is read-only, hashed at rest and revocable."""

    def setUp(self):
        super().setUp()
        self.book_id = self.add_book(title="Shared Book")
        self.user_id_value = self.register_and_login()

        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO wishlist (user_id, book_id) VALUES (?, ?)",
                (self.user_id_value, self.book_id),
            )
            connection.commit()

    def create_link(self):
        self.post("/wishlist/share")
        with self.client.session_transaction() as session_data:
            return session_data.get("wishlist_share_token")

    def test_only_the_hash_is_stored(self):
        token = self.create_link()

        self.assertTrue(token)

        with self.app.app_context():
            row = get_db().execute(
                "SELECT token_hash FROM wishlist_shares WHERE user_id = ?",
                (self.user_id_value,),
            ).fetchone()

        self.assertNotEqual(row["token_hash"], token)
        self.assertEqual(len(row["token_hash"]), 64)

    def test_anonymous_visitor_can_read_the_list(self):
        token = self.create_link()

        with self.app.test_client() as anonymous:
            response = anonymous.get(f"/wishlist/shared/{token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shared Book", response.data)

    def test_revoked_link_returns_404(self):
        token = self.create_link()

        with self.app.test_client() as anonymous:
            self.assertEqual(
                anonymous.get(f"/wishlist/shared/{token}").status_code, 200
            )

        self.post("/wishlist/share/revoke")

        with self.app.test_client() as anonymous:
            self.assertEqual(
                anonymous.get(f"/wishlist/shared/{token}").status_code, 404
            )

    def test_unknown_token_returns_404(self):
        with self.app.test_client() as anonymous:
            response = anonymous.get("/wishlist/shared/not-a-real-token")

        self.assertEqual(response.status_code, 404)

    def test_creating_a_new_link_invalidates_the_old_one(self):
        first = self.create_link()
        second = self.create_link()

        self.assertNotEqual(first, second)

        with self.app.test_client() as anonymous:
            self.assertEqual(
                anonymous.get(f"/wishlist/shared/{first}").status_code, 404
            )
            self.assertEqual(
                anonymous.get(f"/wishlist/shared/{second}").status_code, 200
            )


class AccountDeletionTests(BatchFourTestCase):
    """Requirement 59: password-confirmed anonymization that keeps orders."""

    def setUp(self):
        super().setUp()
        self.book_id = self.add_book()
        self.user_id_value = self.register_and_login(
            email="delete-me@example.com",
            name="Delete Me",
            password="Reading123",
        )

    def place_order(self):
        self.post("/cart/add/1", {"quantity": "1"})
        self.post(
            "/checkout",
            {
                "name": "Delete Me",
                "phone": "9876543210",
                "new_address": "9 Delete Lane",
                "pincode": "500001",
            },
            follow_redirects=True,
        )

    def test_wrong_password_changes_nothing(self):
        response = self.post(
            "/account/delete",
            {"password": "WrongPassword", "confirm": "DELETE"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            row = get_db().execute(
                "SELECT name, email, anonymized_at FROM users WHERE id = ?",
                (self.user_id_value,),
            ).fetchone()

        self.assertEqual(row["name"], "Delete Me")
        self.assertEqual(row["email"], "delete-me@example.com")
        self.assertIsNone(row["anonymized_at"])

    def test_missing_confirmation_word_changes_nothing(self):
        self.post(
            "/account/delete",
            {"password": "Reading123", "confirm": "yes"},
            follow_redirects=True,
        )

        with self.app.app_context():
            row = get_db().execute(
                "SELECT anonymized_at FROM users WHERE id = ?",
                (self.user_id_value,),
            ).fetchone()

        self.assertIsNone(row["anonymized_at"])

    def test_anonymization_keeps_orders_but_removes_pii(self):
        self.place_order()

        with self.app.app_context():
            before = get_db().execute(
                "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?",
                (self.user_id_value,),
            ).fetchone()["c"]

        self.assertGreaterEqual(before, 1)

        response = self.post(
            "/account/delete",
            {"password": "Reading123", "confirm": "DELETE"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            connection = get_db()
            user = connection.execute(
                "SELECT name, email, phone, anonymized_at FROM users "
                "WHERE id = ?",
                (self.user_id_value,),
            ).fetchone()
            after = connection.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?",
                (self.user_id_value,),
            ).fetchone()["c"]
            order = connection.execute(
                "SELECT name, phone, address FROM orders WHERE user_id = ? "
                "LIMIT 1",
                (self.user_id_value,),
            ).fetchone()
            sessions = connection.execute(
                "SELECT COUNT(*) AS c FROM user_sessions WHERE user_id = ?",
                (self.user_id_value,),
            ).fetchone()["c"]

        # Orders survive for accounting...
        self.assertEqual(after, before)
        # ...but carry no personal identifiers.
        self.assertNotEqual(user["name"], "Delete Me")
        self.assertNotIn("delete-me@example.com", user["email"])
        self.assertIsNotNone(user["anonymized_at"])
        self.assertNotEqual(order["name"], "Delete Me")
        self.assertNotEqual(order["phone"], "9876543210")
        self.assertNotIn("Delete Lane", order["address"])
        # Signed-in devices are gone and the browser session is cleared.
        self.assertEqual(sessions, 0)

    def test_anonymized_account_cannot_log_in(self):
        self.post(
            "/account/delete",
            {"password": "Reading123", "confirm": "DELETE"},
            follow_redirects=True,
        )

        with self.app.test_client() as fresh:
            response = fresh.post(
                "/login",
                data={
                    "csrf_token": self._fresh_token(fresh, "/login"),
                    "email": "delete-me@example.com",
                    "password": "Reading123",
                },
                follow_redirects=True,
            )

        self.assertNotIn(b"Log out", response.data)

    def _fresh_token(self, client, path):
        response = client.get(path)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_anonymize_user_is_idempotent(self):
        with self.app.app_context():
            connection = get_db()
            first = anonymize_user(connection, self.user_id_value)
            connection.commit()
            self.assertIsNotNone(first)

            second = anonymize_user(connection, self.user_id_value)

        self.assertIsNone(second)

    def test_placeholder_emails_stay_unique(self):
        other = self.register_and_login(email="other@example.com", name="Other")

        with self.app.app_context():
            connection = get_db()
            anonymize_user(connection, self.user_id_value)
            anonymize_user(connection, other)
            connection.commit()

            emails = [
                row["email"]
                for row in connection.execute(
                    "SELECT email FROM users WHERE id IN (?, ?)",
                    (self.user_id_value, other),
                ).fetchall()
            ]

        self.assertEqual(len(set(emails)), 2, "placeholder emails must not clash")


if __name__ == "__main__":
    unittest.main()
