"""Permanent regression guards for the whole URL map.

These tests do not check business rules (``tests/test_app.py`` does that).
They answer a different question: *after a change, is any route now broken?*

Two guards:

``test_no_anonymous_route_raises``
    Requests every GET rule as an anonymous visitor. A 200, 302, 403 or 404 is
    an acceptable answer; a 5xx or an unhandled exception is a regression.

``test_no_duplicate_url_rules``
    Fails if two endpoints claim the same path *and* HTTP method, which would
    make routing depend on registration order.

Both run against a throwaway database, so they never touch real data.
"""
import os
import re
import tempfile
import unittest

from pageturner import create_app


class RouteSweepTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "route-sweep-secret",
            "DATABASE": os.path.join(self.temp_dir.name, "sweep.db"),
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _fill(rule):
        """Turn ``/book/<int:book_id>`` into a concrete URL."""
        url = re.sub(r"<int:[^>]+>", "1", rule)
        url = re.sub(r"<float:[^>]+>", "1.0", url)
        url = re.sub(r"<[^>]+>", "sample", url)
        return url

    def test_no_anonymous_route_raises(self):
        """Every GET route must answer without a 5xx for a signed-out visitor."""
        failures = []
        checked = 0

        for rule in self.app.url_map.iter_rules():
            if "GET" not in rule.methods or rule.endpoint == "static":
                continue
            if rule.endpoint == "health":
                # Covered explicitly by test_app.py; still sweep it here.
                pass

            url = self._fill(rule.rule)
            checked += 1
            try:
                response = self.client.get(url, follow_redirects=False)
            except Exception as exc:  # noqa: BLE001 - report any raise as a failure
                failures.append(f"{rule.endpoint} {url} raised {type(exc).__name__}: {exc}")
                continue

            if response.status_code >= 500:
                failures.append(f"{rule.endpoint} {url} -> {response.status_code}")

        self.assertGreater(checked, 0, "route sweep checked nothing")
        self.assertEqual(failures, [], "Regressed routes:\n  " + "\n  ".join(failures))

    def test_no_duplicate_url_rules(self):
        """No two endpoints may claim the same path and HTTP method."""
        seen = {}
        duplicates = []

        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                key = (rule.rule, method)
                if key in seen and seen[key] != rule.endpoint:
                    duplicates.append(f"{method} {rule.rule}: {seen[key]} vs {rule.endpoint}")
                seen[key] = rule.endpoint

        self.assertEqual(duplicates, [], "Duplicate URL rules:\n  " + "\n  ".join(duplicates))

    def test_health_endpoint_stays_public(self):
        """The hosting platform health check must never require auth."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
