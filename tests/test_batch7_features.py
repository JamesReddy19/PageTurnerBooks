"""Regression tests for requirements 71, 72 and 73.

Each test is written to fail on the pre-batch-7 code:

71  ``users`` had no ``totp_secret``/``totp_enabled`` column and login had no
    second step, so a stolen password was enough to sign in;
72  the JSON API was anonymous-only: no key table, no scopes, and the stock
    figure was readable by anybody who could reach the endpoint;
73  authorisation was a decorator hard-coding role names in the source
    (``@role_required("admin")``), so changing what a staff member could reach
    needed a code edit and a deploy.

These tests assert behaviour through the real routes wherever a route exists.
"""

import base64
import os
import re
import tempfile
import unittest

from pageturner import apikeys, create_app, permissions, totp
from pageturner.db import get_db


class BatchSevenTestCase(unittest.TestCase):
    """Shared fixture: one temporary database per test."""

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
    def csrf(self, path="/"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, f"GET {path} failed")
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, path, data=None, token_from="/", **kwargs):
        payload = dict(data or {})
        payload["csrf_token"] = self.csrf(token_from)
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

    def set_role(self, user_id, role):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
            )
            connection.commit()

    def logged_in(self):
        with self.client.session_transaction() as session:
            return session.get("user_id")


class TwoFactorTests(BatchSevenTestCase):
    """Requirement 71: a correct password alone must not complete login."""

    def enable_2fa(self, user_id, secret):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE id = ?",
                (secret, user_id),
            )
            connection.commit()

    def test_totp_algorithm_matches_rfc_6238_vector(self):
        """The code generator must agree with a published RFC test vector.

        This is the assertion that a hand-rolled HOTP would most easily break.
        RFC 4226 Appendix D uses the ASCII secret "12345678901234567890"; this
        implementation takes base32 secrets, so the vector is passed in its
        base32 form (which is what an authenticator app would be given).
        counter 0 -> 755224, 1 -> 287082, 9 -> 520489.
        """
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        self.assertEqual(secret, "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")

        self.assertEqual(totp.hotp(secret, 0), "755224")
        self.assertEqual(totp.hotp(secret, 1), "287082")
        self.assertEqual(totp.hotp(secret, 9), "520489")

    def test_password_alone_leaves_the_session_unauthenticated(self):
        """Login with 2FA on must stop at the second step, not sign the user in."""
        user_id = self.register_and_login()
        self.enable_2fa(user_id, totp.generate_secret())

        self.client.get("/logout")

        response = self.post(
            "/login",
            {"email": "reader@example.com", "password": "Reading123"},
            token_from="/login",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/2fa", response.headers["Location"])

        # The critical assertion: the full session was NOT established.
        self.assertIsNone(self.logged_in())

    def test_a_wrong_code_is_rejected_and_the_step_can_be_retried(self):
        secret = totp.generate_secret()
        user_id = self.register_and_login()
        self.enable_2fa(user_id, secret)
        self.client.get("/logout")

        self.post(
            "/login",
            {"email": "reader@example.com", "password": "Reading123"},
            token_from="/login",
        )

        bad = self.post(
            "/login/2fa",
            {"code": "000000"},
            token_from="/login/2fa",
        )
        self.assertEqual(bad.status_code, 200)
        self.assertIsNone(self.logged_in())

        good_code = totp.code_for(secret)
        response = self.post(
            "/login/2fa",
            {"code": good_code},
            token_from="/login/2fa",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.logged_in(), user_id)

    def test_the_same_code_cannot_be_replayed(self):
        """A TOTP code is single-use; replay inside its window must fail.

        Without the ``totp_used_codes`` table a sniffed code works for the rest
        of its 30-second step, which defeats the point of the second factor.
        """
        secret = totp.generate_secret()
        user_id = self.register_and_login()
        self.enable_2fa(user_id, secret)
        self.client.get("/logout")

        code = totp.code_for(secret)

        self.post(
            "/login",
            {"email": "reader@example.com", "password": "Reading123"},
            token_from="/login",
        )
        first = self.post(
            "/login/2fa", {"code": code}, token_from="/login/2fa"
        )
        self.assertEqual(first.status_code, 302)

        # Sign out, then try the very same code again.
        self.client.get("/logout")
        self.post(
            "/login",
            {"email": "reader@example.com", "password": "Reading123"},
            token_from="/login",
        )
        second = self.post(
            "/login/2fa", {"code": code}, token_from="/login/2fa"
        )

        self.assertEqual(second.status_code, 200)
        self.assertIsNone(self.logged_in())

    def test_enrolling_then_disabling_restores_single_factor_login(self):
        user_id = self.register_and_login()

        response = self.client.get("/security/2fa")
        self.assertEqual(response.status_code, 200)

        # Starting enrolment stores a pending secret but must not enable it:
        # the user has not proven they can generate a code yet.
        self.post("/security/2fa", {}, token_from="/security/2fa")

        with self.app.app_context():
            row = get_db().execute(
                "SELECT totp_secret, totp_enabled FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

        self.assertTrue(row["totp_secret"])
        self.assertEqual(row["totp_enabled"], 0)

        secret = row["totp_secret"]

        response = self.post(
            "/security/2fa",
            {"action": "confirm", "code": totp.code_for(secret)},
            token_from="/security/2fa",
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            enabled = get_db().execute(
                "SELECT totp_enabled FROM users WHERE id = ?", (user_id,)
            ).fetchone()["totp_enabled"]
        self.assertEqual(enabled, 1)

        # Disabling returns the account to password-only login.
        self.post(
            "/security/2fa",
            {"action": "disable"},
            token_from="/security/2fa",
        )

        with self.app.app_context():
            row = get_db().execute(
                "SELECT totp_secret, totp_enabled FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

        self.assertEqual(row["totp_enabled"], 0)
        self.assertIsNone(row["totp_secret"])


class ApiKeyTests(BatchSevenTestCase):
    """Requirement 72: scoped keys, and stock only for a key that may see it."""

    def issue(self, scopes):
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO users (name, email, password_hash, role) "
                "VALUES ('Key Owner', 'owner@example.com', 'x', 'customer')"
            )
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = 'owner@example.com'"
            ).fetchone()["id"]
            row, token = apikeys.create_key(
                connection, user_id, "test key", scopes
            )
            connection.commit()
        return token, row
    def test_the_database_never_stores_the_token_itself(self):
        token, row = self.issue(["books:read"])

        with self.app.app_context():
            stored = get_db().execute(
                "SELECT * FROM api_keys WHERE id = ?", (row["id"],)
            ).fetchone()

        values = " ".join(str(value) for value in tuple(stored))
        self.assertNotIn(token, values)
        # What is stored must still be enough to verify a presented token.
        self.assertIn(apikeys.hash_token(token), values)

    def test_a_valid_key_authenticates_the_request(self):
        token, _ = self.issue(["books:read"])

        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["name"], "test key")

    def test_a_revoked_key_stops_working_immediately(self):
        token, row = self.issue(["books:read"])

        with self.app.app_context():
            connection = get_db()
            apikeys.revoke(connection, row["user_id"], row["id"])
            connection.commit()

        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_a_key_without_the_scope_is_refused(self):
        """A books:read key must not reach an inventory-scoped endpoint."""
        token, _ = self.issue(["books:read"])

        response = self.client.get(
            "/api/inventory", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("inventory:read", response.get_json()["error"])

    def test_stock_is_hidden_from_an_anonymous_caller(self):
        """Stock is commercially sensitive, so it is removed, not merely scoped.

        Pre-batch-7 ``/api/books`` returned ``stock`` to anybody.
        """
        response = self.client.get("/api/books")
        self.assertEqual(response.status_code, 200)

        for book in response.get_json()["books"]:
            self.assertNotIn("stock", book)

    def test_a_scoped_key_sees_stock(self):
        token, _ = self.issue(["books:read", "inventory:read"])

        response = self.client.get(
            "/api/books", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 200)

        books = response.get_json()["books"]
        self.assertTrue(books)
        for book in books:
            self.assertIn("stock", book)

    def test_the_catalogue_still_works_anonymously(self):
        """Scoping must not have broken the public, unauthenticated API."""
        response = self.client.get("/api/books")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["books"])


class PermissionMatrixTests(BatchSevenTestCase):
    """Requirement 73: grants live in the database, not in the source."""

    def test_the_catalogue_and_defaults_are_seeded(self):
        with self.app.app_context():
            connection = get_db()

            codes = {
                row["code"]
                for row in connection.execute(
                    "SELECT code FROM permissions"
                ).fetchall()
            }
            self.assertEqual(codes, set(permissions.DEFAULT_PERMISSIONS))

            staff = permissions.permissions_for_role("staff")
            self.assertEqual(staff, permissions.DEFAULT_ROLE_PERMISSIONS["staff"])

            # Nothing about the customer role grants admin access.
            self.assertEqual(permissions.permissions_for_role("customer"), set())

    def test_revoking_a_grant_changes_the_next_request_without_a_deploy(self):
        """The whole point of the requirement: data, not code, decides access.

        A staff account is replaced by a direct DELETE from ``role_permissions``
        — exactly what an operator would do — and the very next request is
        refused.  On the pre-batch-7 code the route consulted the role name, so
        nothing short of a source edit could have this effect.
        """
        user_id = self.register_and_login()
        self.set_role(user_id, "staff")

        # A staff member with orders.manage can load the orders page.
        self.assertEqual(self.client.get("/admin/orders").status_code, 200)

        with self.app.app_context():
            connection = get_db()
            permissions.revoke(connection, "staff", "orders.manage")
            connection.commit()

        self.assertEqual(self.client.get("/admin/orders").status_code, 403)

        # Granting it back restores access, again with no code change.
        with self.app.app_context():
            connection = get_db()
            permissions.grant(connection, "staff", "orders.manage")
            connection.commit()

        self.assertEqual(self.client.get("/admin/orders").status_code, 200)

    def test_a_new_permission_code_is_immediately_usable_by_admin(self):
        """Adding a code to the catalogue must not need a back-fill migration."""
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO permissions (code, description) VALUES (?, ?)",
                ("experimental.thing", "A capability added after seeding"),
            )
            connection.commit()

            # No row was inserted into role_permissions for it, yet admin holds it.
            self.assertTrue(
                permissions.role_can("admin", "experimental.thing")
            )
            self.assertFalse(
                permissions.role_can("staff", "experimental.thing")
            )

    def test_seeding_never_restores_a_deliberate_revocation(self):
        """A revoke by the operator must survive the next ``init_db``.

        Seeding that re-inserts defaults on every boot would silently undo an
        operator's decision, which is worse than no matrix at all.
        """
        with self.app.app_context():
            connection = get_db()
            permissions.revoke(connection, "staff", "requests.manage")
            connection.commit()

            permissions.sync_default_permissions(connection)
            connection.commit()

            self.assertFalse(
                permissions.role_can("staff", "requests.manage"),
                "init_db re-granted a permission the operator revoked",
            )

    def test_only_a_role_with_roles_manage_can_edit_the_matrix(self):
        user_id = self.register_and_login()
        self.set_role(user_id, "staff")

        self.assertEqual(self.client.get("/admin/permissions").status_code, 403)

        self.set_role(user_id, "admin")
        self.assertEqual(self.client.get("/admin/permissions").status_code, 200)

    def test_saving_the_matrix_grants_and_revokes_in_one_submit(self):
        """The form is the complete desired state, so it can do both at once."""
        user_id = self.register_and_login()
        self.set_role(user_id, "admin")

        with self.app.app_context():
            connection = get_db()
            before = permissions.permissions_for_role("staff")
            connection.commit()

        # Submit staff grants with reports.view added and requests.manage removed.
        desired = (before | {"reports.view"}) - {"requests.manage"}

        response = self.post(
            "/admin/permissions",
            {"grant": [f"staff:{code}" for code in sorted(desired)]},
            token_from="/admin/permissions",
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            after = permissions.permissions_for_role("staff")

        self.assertEqual(after, desired)
        self.assertIn("reports.view", after)
        self.assertNotIn("requests.manage", after)

    def test_the_superuser_row_cannot_be_stripped(self):
        """No form submission may leave the admin area unreachable."""
        user_id = self.register_and_login()
        self.set_role(user_id, "admin")

        # An empty submission means "no grants for the editable roles"; admin
        # must be unaffected because it is resolved from the catalogue.
        response = self.post(
            "/admin/permissions", {}, token_from="/admin/permissions"
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertTrue(permissions.role_can("admin", "admin.access"))

        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_the_matrix_page_renders_every_pair(self):
        user_id = self.register_and_login()
        self.set_role(user_id, "admin")

        response = self.client.get("/admin/permissions")
        self.assertEqual(response.status_code, 200)

        body = response.data.decode()
        with self.app.app_context():
            codes = [
                row["code"]
                for row in get_db().execute(
                    "SELECT code FROM permissions ORDER BY code"
                ).fetchall()
            ]

        for code in codes:
            self.assertIn(f'value="staff:{code}"', body)


if __name__ == "__main__":
    unittest.main()
