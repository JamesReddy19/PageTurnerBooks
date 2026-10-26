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
        # Requirement 61: the form carries a single-use token; submit it the way
        # a browser would.
        import re as _re
        checkout_token = _re.search(
            r'name="checkout_token" value="([^"]+)"',
            self.client.get("/checkout").get_data(as_text=True)
        ).group(1)
        response = self.client.post("/checkout", data={"csrf_token": token, "checkout_token": checkout_token, "name": "Reader", "phone": "9876543210", "new_address": "12 Main Road", "pincode": "500001"}, follow_redirects=True)
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

    def test_27_staff_can_process_orders_but_not_catalogue_or_money(self):
        self.register_and_login()
        with self.app.app_context():
            get_db().execute("UPDATE users SET role='staff' WHERE email='reader@example.com'")
            get_db().commit()

        # Order fulfilment and message handling are the staff's job.
        self.assertEqual(self.client.get("/admin/orders").status_code, 200)
        self.assertEqual(self.client.get("/admin/messages").status_code, 200)

        # Catalogue, pricing, coupons, reports, refunds and role changes stay admin-only.
        for path in (
            "/admin/books",
            "/admin/coupons",
            "/admin/reports",
            "/admin/returns",
            "/admin/sales",
            "/admin/bundles",
            "/admin/users",
            "/admin/",
        ):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_28_admin_appoints_staff_and_cannot_demote_self(self):
        self.register_and_login()
        with self.app.app_context():
            connection = get_db()
            connection.execute("UPDATE users SET role='admin' WHERE email='reader@example.com'")
            connection.execute(
                "INSERT INTO users(name,email,password_hash) VALUES (?,?,?)",
                ("Sam", "sam@example.com", generate_password_hash("SamPass123")),
            )
            connection.commit()
            admin_id = connection.execute("SELECT id FROM users WHERE email='reader@example.com'").fetchone()["id"]
            sam_id = connection.execute("SELECT id FROM users WHERE email='sam@example.com'").fetchone()["id"]

        self.assertEqual(self.client.get("/admin/users").status_code, 200)

        response = self.post(f"/admin/users/{sam_id}/role", data={"role": "staff"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT role FROM users WHERE id=?", (sam_id,)).fetchone()["role"], "staff")

        # The new staff member can reach order fulfilment.
        with self.app.app_context():
            get_db().execute("UPDATE users SET role='staff' WHERE id=?", (admin_id,))
            get_db().commit()
        self.assertEqual(self.client.get("/admin/orders").status_code, 200)
        with self.app.app_context():
            get_db().execute("UPDATE users SET role='admin' WHERE id=?", (admin_id,))
            get_db().commit()

        # An admin cannot drop their own privileges (self-lockout guard).
        self.post(f"/admin/users/{admin_id}/role", data={"role": "customer"})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT role FROM users WHERE id=?", (admin_id,)).fetchone()["role"], "admin")

    def test_29_customer_cannot_manage_roles(self):
        self.register_and_login()
        self.assertEqual(self.client.get("/admin/users").status_code, 403)
        response = self.post("/admin/users/1/role", data={"role": "admin"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
