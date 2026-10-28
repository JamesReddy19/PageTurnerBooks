"""Automated smoke and security tests for PageTurner Books."""
import os
import re
import tempfile
import unittest
from werkzeug.security import generate_password_hash
from pageturner import create_app
from pageturner.db import get_db


class PageTurnerTests(unittest.TestCase):
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

    def tearDown(self):
        self.temp_dir.cleanup()

    def token(self, path="/"):
        response = self.client.get(path)
        match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, path, data=None, token=None, **kwargs):
        data = dict(data or {})
        data["csrf_token"] = token or self.token()
        return self.client.post(path, data=data, **kwargs)

    def register_and_login(self, email="reader@example.com", name="Reader", password="Reading123"):
        token = self.token("/register")
        response = self.client.post("/register", data={"csrf_token": token, "name": name, "email": email, "password": password}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        token = self.token("/login")
        response = self.client.post("/login", data={"csrf_token": token, "email": email, "password": password}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_01_home_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PageTurner Books", response.data)

    def test_02_seed_has_twelve_books(self):
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM books").fetchone()[0]
        self.assertEqual(count, 12)

    def test_03_money_is_integer_paise(self):
        with self.app.app_context():
            row = get_db().execute("SELECT price_paise FROM books WHERE title='Steve Jobs'").fetchone()
        self.assertEqual(row[0], 59900)
        self.assertIsInstance(row[0], int)

    def test_04_book_detail(self):
        self.assertEqual(self.client.get("/book/1").status_code, 200)

    def test_05_missing_book_is_404(self):
        self.assertEqual(self.client.get("/book/9999").status_code, 404)

    def test_06_api_lists_books(self):
        response = self.client.get("/api/books")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 12)

    def test_07_api_search(self):
        response = self.client.get("/api/books?q=Atomic")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("Atomic Habits" in item["title"] for item in response.json["books"]))

    def test_08_api_single_book(self):
        self.assertEqual(self.client.get("/api/books/1").status_code, 200)

    def test_09_api_missing_book_returns_json_404(self):
        response = self.client.get("/api/books/9999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json)

    def test_10_post_without_csrf_is_rejected(self):
        response = self.client.post("/cart/add/1", data={"quantity": "1"})
        self.assertEqual(response.status_code, 400)

    def test_11_guest_can_add_book_to_cart(self):
        response = self.post("/cart/add/1", {"quantity": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/cart").status_code, 200)

    def test_12_invalid_quantity_is_rejected(self):
        response = self.post("/cart/add/1", {"quantity": "0"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"Choose a quantity", self.client.get("/cart", follow_redirects=True).data)

    def test_13_register_customer(self):
        token = self.token("/register")
        response = self.client.post("/register", data={"csrf_token": token, "name": "Test Reader", "email": "new@example.com", "password": "StrongPass123"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            user = get_db().execute("SELECT role FROM users WHERE email='new@example.com'").fetchone()
        self.assertEqual(user["role"], "customer")

    def test_14_login_creates_hashed_session_record(self):
        self.register_and_login()
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
        self.assertEqual(count, 1)

    def test_15_checkout_requires_login(self):
        response = self.client.get("/checkout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_16_logged_in_user_can_place_order(self):
        self.register_and_login()
        self.post("/cart/add/1", {"quantity": "2"})
        response = self.client.get("/checkout")
        self.assertEqual(response.status_code, 200)
        token = self.token("/checkout")
        response = self.client.post("/checkout", data={"csrf_token": token, "name": "Reader", "phone": "9876543210", "new_address": "12 Main Road", "pincode": "500001"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Order #", response.data)
        with self.app.app_context():
            order = get_db().execute("SELECT * FROM orders").fetchone()
            item = get_db().execute("SELECT * FROM order_items").fetchone()
            book = get_db().execute("SELECT stock FROM books WHERE id=1").fetchone()
        self.assertEqual(order["status"], "Placed")
        self.assertEqual(item["quantity"], 2)
        self.assertEqual(book["stock"], 8)
        self.assertGreater(order["tax_paise"], 0)

    def test_17_wishlist_requires_login(self):
        response = self.client.post("/wishlist/toggle/1", data={"csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)

    def test_18_customer_can_add_wishlist(self):
        self.register_and_login()
        response = self.post("/wishlist/toggle/1")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM wishlist").fetchone()[0]
        self.assertEqual(count, 1)

    def test_19_staff_cannot_manage_books(self):
        self.register_and_login()
        with self.app.app_context():
            get_db().execute("UPDATE users SET role='staff' WHERE email='reader@example.com'")
            get_db().commit()
        self.assertEqual(self.client.get("/admin/books").status_code, 403)

    def test_20_customer_cannot_open_another_users_order(self):
        self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            connection.execute("INSERT INTO users(name,email,password_hash) VALUES (?,?,?)", ("Other", "other@example.com", generate_password_hash("OtherPass123")))
            other_id = connection.execute("SELECT id FROM users WHERE email='other@example.com'").fetchone()["id"]
            connection.execute("INSERT INTO orders(user_id,name,phone,address,total_paise) VALUES (?,?,?,?,?)", (other_id,"Other","9876543210","Somewhere",10000))
            connection.commit()
            order_id = connection.execute("SELECT id FROM orders WHERE user_id=?", (other_id,)).fetchone()["id"]
        self.assertEqual(self.client.get(f"/order/{order_id}").status_code, 404)

    def test_21_invalid_status_transition_is_rejected(self):
        self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            connection.execute("INSERT INTO orders(user_id,name,phone,address,total_paise,status) VALUES (?,?,?,?,?,'Placed')", (1,"Reader","9876543210","Address",10000))
            connection.commit()
            order_id = connection.execute("SELECT MAX(id) FROM orders").fetchone()[0]
        token = self.token("/order-history")
        response = self.client.post(f"/admin/orders/{order_id}/status", data={"csrf_token": token, "status": "Delivered"})
        self.assertIn(response.status_code, (302, 403))

    def test_22_contact_message_is_saved(self):
        token = self.token("/contact")
        response = self.client.post("/contact", data={"csrf_token": token, "name": "Reader", "email": "reader@example.com", "subject": "Question", "message": "Hello"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0]
        self.assertEqual(count, 1)

    def test_23_profile_requires_login(self):
        self.assertEqual(self.client.get("/profile").status_code, 302)

    def test_24_notifications_are_private(self):
        self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            connection.execute("INSERT INTO notifications(user_id,message) VALUES (?,?)", (1,"Private notice"))
            connection.commit()
        response = self.client.get("/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Private notice", response.data)

    def test_25_api_rate_limit_returns_json_429(self):
        for _ in range(30):
            response = self.client.get("/api/books")
            self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/books")
        self.assertEqual(response.status_code, 429)
        self.assertIn("error", response.json)

    def test_26_health_endpoint(self):
        self.assertEqual(self.client.get("/health").json["status"], "ok")

    # --- Batch 1: catalog filters and review moderation ---

    def make_book(self, title, price_paise, stock=10, rating=0, review_status="published"):
        """Insert a book (and optionally a review) used by the filter tests."""
        with self.app.app_context():
            connection = get_db()
            cursor = connection.execute(
                "INSERT INTO books(title,author,category,price_paise,stock) VALUES (?,?,?,?,?)",
                (title, "Filter Author", "Filters", price_paise, stock),
            )
            book_id = cursor.lastrowid
            if rating:
                connection.execute(
                    "INSERT INTO reviews(book_id,user_id,name,rating,comment,status) VALUES (?,?,?,?,?,?)",
                    (book_id, None, "Reviewer", rating, "Solid read", review_status),
                )
            connection.commit()
        return book_id

    def test_27_catalog_price_range_filter(self):
        self.make_book("Price Filter Low", 10000)
        self.make_book("Price Filter High", 90000)
        response = self.client.get("/?q=Price+Filter&min_price=500")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Price Filter High", response.data)
        self.assertNotIn(b"Price Filter Low", response.data)

    def test_28_catalog_in_stock_filter(self):
        self.make_book("Stock Filter Empty", 20000, stock=0)
        self.make_book("Stock Filter Available", 20000, stock=4)
        response = self.client.get("/?q=Stock+Filter&in_stock=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stock Filter Available", response.data)
        self.assertNotIn(b"Stock Filter Empty", response.data)

    def test_29_catalog_minimum_rating_filter(self):
        self.make_book("Rating Filter High", 20000, rating=5)
        self.make_book("Rating Filter Low", 20000, rating=2)
        response = self.client.get("/?q=Rating+Filter&min_rating=4")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Rating Filter High", response.data)
        self.assertNotIn(b"Rating Filter Low", response.data)

    def test_30_hidden_review_is_not_public(self):
        book_id = self.make_book("Hidden Review Book", 20000, rating=1, review_status="hidden")
        response = self.client.get(f"/book/{book_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Solid read", response.data)

    def test_31_admin_can_moderate_reviews(self):
        book_id = self.make_book("Moderated Review Book", 20000, rating=4)
        with self.app.app_context():
            connection = get_db()
            review_id = connection.execute(
                "SELECT id FROM reviews WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO users(name,email,password_hash,role) VALUES (?,?,?,'admin')",
                ("Admin", "admin@example.com", generate_password_hash("AdminPass123")),
            )
            connection.commit()
            admin_id = connection.execute(
                "SELECT id FROM users WHERE email='admin@example.com'"
            ).fetchone()[0]
        with self.client.session_transaction() as session:
            session["user_id"] = admin_id
        listing = self.client.get("/admin/reviews")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b"Moderated Review Book", listing.data)
        token = self.token("/admin/reviews")
        response = self.client.post(
            f"/admin/reviews/{review_id}/status",
            data={"csrf_token": token, "status": "hidden"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM reviews WHERE id=?", (review_id,)
            ).fetchone()[0]
        self.assertEqual(status, "hidden")


if __name__ == "__main__":
    unittest.main()
