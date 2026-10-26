"""Requirement 58 — a customer can download all of their own stored data."""
import json
import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner.db import get_db


class DataExportTests(unittest.TestCase):
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

    def register_and_login(self, email="exporter@example.com"):
        token = self.token("/register")
        self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "name": "Exporter",
                "email": email,
                "password": "Reading123",
            },
            follow_redirects=True,
        )
        token = self.token("/login")
        self.client.post(
            "/login",
            data={"csrf_token": token, "email": email, "password": "Reading123"},
            follow_redirects=True,
        )

    def test_export_requires_login(self):
        response = self.client.get("/account/export.json")
        # Not signed in: redirected to the login form, never a JSON body.
        self.assertIn(response.status_code, (302, 401))
        self.assertNotIn(b'"account"', response.data)

    def test_export_is_downloadable_json_with_own_data(self):
        self.register_and_login()

        # Give the account some data so the export is not just an empty shell.
        with self.app.app_context():
            connection = get_db()
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", ("exporter@example.com",)
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO wishlist (user_id, book_id) VALUES (?, 1)", (user_id,)
            )
            connection.execute(
                "INSERT INTO addresses (user_id, label, line1, city, pincode, is_default)"
                " VALUES (?, 'Home', '1 Test Street', 'Pune', '411001', 1)",
                (user_id,),
            )
            connection.commit()

        response = self.client.get("/account/export.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn(
            "pageturner-account-export.json", response.headers["Content-Disposition"]
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        payload = json.loads(response.data)
        self.assertEqual(payload["account"]["email"], "exporter@example.com")
        self.assertEqual(payload["account"]["role"], "customer")
        self.assertEqual(len(payload["addresses"]), 1)
        self.assertEqual(payload["addresses"][0]["city"], "Pune")
        self.assertEqual(payload["wishlist"][0]["book_id"], 1)
        # Every section a customer owns is present, even when empty.
        for section in ("orders", "loyalty_ledger", "reviews", "returns", "sessions"):
            self.assertIn(section, payload)
        self.assertIsInstance(payload["orders"], list)

    def test_export_never_leaks_credentials(self):
        self.register_and_login()

        response = self.client.get("/account/export.json")
        self.assertEqual(response.status_code, 200)

        body = response.data.decode()
        self.assertNotIn("password_hash", body)
        self.assertNotIn("pbkdf2:", body)
        self.assertNotIn("token_hash", body)

        with self.app.app_context():
            user = get_db().execute(
                "SELECT password_hash FROM users WHERE email = ?",
                ("exporter@example.com",),
            ).fetchone()
        self.assertNotIn(user["password_hash"], body)

    def test_export_only_contains_the_callers_rows(self):
        self.register_and_login("first@example.com")
        with self.app.app_context():
            connection = get_db()
            first_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", ("first@example.com",)
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO wishlist (user_id, book_id) VALUES (?, 2)", (first_id,)
            )
            connection.commit()

        first_export = json.loads(self.client.get("/account/export.json").data)
        self.client.get("/logout")

        self.register_and_login("second@example.com")
        second_export = json.loads(self.client.get("/account/export.json").data)

        self.assertEqual(second_export["account"]["email"], "second@example.com")
        self.assertEqual(second_export["wishlist"], [])
        # The first account's wishlist row must not appear for the second user.
        self.assertNotIn(2, [item["book_id"] for item in second_export["wishlist"]])


if __name__ == "__main__":
    unittest.main()
