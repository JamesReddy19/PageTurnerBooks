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
            # Keep test mail out of the checked-out project's instance folder.
            "MAIL_SERVER": "",
            "MAIL_OUTBOX": os.path.join(self.temp_dir.name, "mail_outbox.log"),
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

    def test_32_api_supports_price_and_stock_filters(self):
        self.make_book("API Filter Low", 10000)
        self.make_book("API Filter High", 90000)
        response = self.client.get("/api/books?q=API+Filter&min_price=500")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.json["books"]]
        self.assertIn("API Filter High", titles)
        self.assertNotIn("API Filter Low", titles)

    def test_33_api_pagination_metadata(self):
        response = self.client.get("/api/books?page=1&page_size=2")
        self.assertEqual(response.status_code, 200)
        payload = response.json
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 2)
        self.assertLessEqual(len(payload["books"]), 2)
        self.assertGreaterEqual(payload["total"], len(payload["books"]))

    def test_34_api_book_detail_includes_published_reviews_only(self):
        book_id = self.make_book("API Review Book", 20000, rating=5, review_status="published")
        self.make_book("API Hidden Book", 20000, rating=1, review_status="hidden")
        response = self.client.get(f"/api/books/{book_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["review_count"], 1)
        self.assertEqual(len(response.json["reviews"]), 1)
        self.assertEqual(response.json["average_rating"], 5.0)

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

    def login_as_admin(self):
        with self.app.app_context():
            connection = get_db()
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
        return admin_id

    def test_35_admin_dashboard_shows_kpi_cards(self):
        self.login_as_admin()
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        for label in (
            b"AVERAGE ORDER",
            b"REVENUE TREND",
            b"NEW CUSTOMERS",
            b"PENDING RETURNS",
        ):
            self.assertIn(label, response.data)

    def test_36_admin_dashboard_revenue_trend_is_computed(self):
        self.login_as_admin()
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO orders(user_id,name,phone,address,total_paise,date,status) "
                "VALUES (NULL,'Buyer','9999999999','Somewhere',50000,date('now'),'Placed')"
            )
            connection.commit()
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        # Revenue is present in the current window and zero in the previous one.
        self.assertIn(b"+100%", response.data)

    def test_37_admin_dashboard_renders_category_chart(self):
        self.login_as_admin()
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="category-chart"', response.data)

    def place_order(self, payment_method="cod"):
        self.register_and_login()
        self.post("/cart/add/1", {"quantity": "1"})
        token = self.token("/checkout")
        data = {
            "csrf_token": token,
            "name": "Reader",
            "phone": "9876543210",
            "new_address": "12 Main Road",
            "pincode": "500001",
        }
        if payment_method is not None:
            data["payment_method"] = payment_method
        return self.client.post("/checkout", data=data, follow_redirects=True)

    def test_38_admin_served_by_css(self):
        """Requirement 56: the seven-day chart must be pure HTML + CSS."""
        self.login_as_admin()
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        chart_start = html.index('class="sales-bar-chart"')
        chart_end = html.index("</div>", html.index('class="sales-bar-orders"', chart_start))
        chart = html[chart_start:chart_end]

        self.assertIn("sales-bar-fill", chart)
        self.assertIn('style="height:', chart)
        self.assertNotIn("<script", chart.lower())
        self.assertNotIn("<canvas", chart.lower())
        # The separate JS-rendered revenue canvas still exists elsewhere.
        self.assertIn('id="revenue-chart"', html)

        with open(os.path.join(os.path.dirname(__file__), "..", "templates", "admin.html")) as handle:
            template = handle.read()
        self.assertNotIn("Chart(", template)
        self.assertNotIn("chart.js", template.lower())

    def test_39_checkout_records_simulated_payment(self):
        response = self.place_order("card_simulated")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Simulated", response.data)
        with self.app.app_context():
            order = get_db().execute("SELECT * FROM orders").fetchone()
            payment = get_db().execute("SELECT * FROM payments").fetchone()
        self.assertEqual(order["status"], "Placed")
        self.assertIsNotNone(payment)
        self.assertEqual(payment["order_id"], order["id"])
        self.assertEqual(payment["method"], "card_simulated")
        self.assertEqual(payment["amount_paise"], order["total_paise"])
        self.assertEqual(payment["is_simulated"], 1)
        self.assertTrue(payment["reference"].startswith("SIM-"))

    def test_40_cash_on_delivery_payment_is_pending(self):
        response = self.place_order("cod")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            payment = get_db().execute("SELECT * FROM payments").fetchone()
        self.assertIsNotNone(payment)
        self.assertEqual(payment["method"], "cod")
        self.assertEqual(payment["status"], "Pending collection")

    def test_41_checkout_rejects_unknown_payment_method(self):
        response = self.place_order("bitcoin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"payment method", response.data.lower())
        with self.app.app_context():
            payments = get_db().execute("SELECT COUNT(*) FROM payments").fetchone()[0]
            orders = get_db().execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        self.assertEqual(payments, 0)
        self.assertEqual(orders, 0)

    def test_41b_missing_payment_field_defaults_to_cash_on_delivery(self):
        response = self.place_order(None)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Order #", response.data)
        with self.app.app_context():
            payment = get_db().execute("SELECT method, status FROM payments").fetchone()
        self.assertEqual(payment["method"], "cod")
        self.assertEqual(payment["status"], "Pending collection")

    def test_42_order_confirmation_email_is_written_to_outbox(self):
        response = self.place_order("upi_simulated")
        self.assertEqual(response.status_code, 200)
        outbox = self.app.config["MAIL_OUTBOX"]
        self.assertTrue(os.path.exists(outbox))
        with open(outbox, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertIn("reader@example.com", contents)
        self.assertIn("order #", contents.lower())
        self.assertIn("simulated", contents.lower())
        self.assertIn("upi_simulated", contents)

    def test_43_test_mail_never_touches_project_outbox(self):
        """Tests must not append to the checked-out project's instance folder."""
        project_outbox = os.path.join(self.app.instance_path, "mail_outbox.log")
        before = os.path.getsize(project_outbox) if os.path.exists(project_outbox) else 0
        self.place_order("cod")
        after = os.path.getsize(project_outbox) if os.path.exists(project_outbox) else 0
        self.assertEqual(before, after)
        self.assertNotEqual(
            os.path.realpath(project_outbox),
            os.path.realpath(self.app.config["MAIL_OUTBOX"]),
        )

    def test_44_admin_supporting_pages_render(self):
        """Coupons, returns, bundles and reports pages were previously untested."""
        self.login_as_admin()
        for path in ("/admin/coupons", "/admin/returns", "/admin/bundles", "/admin/reports"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path} failed to render")
            self.assertGreater(len(response.data), 500, f"{path} rendered an empty page")

    def test_45_reports_csv_exports_orders_in_range(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO orders(user_id,name,phone,address,total_paise,date,status) "
                "VALUES (NULL,'Buyer','9999999999','Somewhere',50000,date('now'),'Placed')"
            )
            connection.commit()
        self.login_as_admin()
        response = self.client.get("/admin/reports.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])
        body = response.data.decode()
        self.assertTrue(body.startswith("order_id,date,customer,status,"), body[:80])
        self.assertIn("Buyer", body)

    def test_46_orders_export_csv_includes_orders(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO orders(user_id,name,phone,address,total_paise,date,status) "
                "VALUES (NULL,'ExportMe','8888888888','Elsewhere',75000,date('now'),'Placed')"
            )
            connection.commit()
        self.login_as_admin()
        response = self.client.get("/admin/export/orders.csv")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode()
        self.assertTrue(body.startswith("id,date,name,phone,address,status,"), body[:80])
        self.assertIn("ExportMe", body)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_47_reports_csv_rejects_invalid_date_range(self):
        self.login_as_admin()
        response = self.client.get("/admin/reports.csv?start=2026-01-05&end=2026-01-01")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/admin/reports.csv?start=not-a-date&end=2026-01-01")
        self.assertEqual(response.status_code, 400)

    def test_48_compare_requires_exactly_two_books(self):
        """Documents the real contract: repeated book_ids, not a single 'ids' list."""
        response = self.client.get("/compare")
        self.assertEqual(response.status_code, 400)

        response = self.client.get("/compare?book_ids=1&book_ids=1")
        self.assertEqual(response.status_code, 400)

        response = self.client.get("/compare?book_ids=1&book_ids=9999")
        self.assertEqual(response.status_code, 404)

        response = self.client.get("/compare?book_ids=1&book_ids=2")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            titles = [
                row["title"]
                for row in get_db().execute("SELECT title FROM books WHERE id IN (1,2)").fetchall()
            ]
        for title in titles:
            self.assertIn(title.encode(), response.data)


if __name__ == "__main__":
    unittest.main()
