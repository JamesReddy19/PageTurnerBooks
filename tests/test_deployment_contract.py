"""Deployment contract tests.

These pin the settings that the container actually depends on. They were
correct in the source but unprotected, so a future edit could break the
deployed app while the functional suite stayed green.

The checks are deliberately source/static based: importing ``app.py`` or
``wsgi.py`` builds the real application (touching a database) which a
deployment test must not do.
"""
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path

from pageturner import create_app

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load_gunicorn_config(env_overrides):
    """Import gunicorn.conf.py fresh so env-derived settings are re-evaluated."""
    spec = importlib.util.spec_from_file_location(
        "gunicorn_conf_under_test", ROOT / "gunicorn.conf.py"
    )
    module = importlib.util.module_from_spec(spec)

    saved = {}
    for key, value in env_overrides.items():
        saved[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        spec.loader.exec_module(module)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return module


def build_app(directory):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "DATABASE": os.path.join(directory, "test.db"),
        "UPLOAD_FOLDER": os.path.join(directory, "uploads"),
        "ADMIN_EMAIL": "",
        "ADMIN_PASSWORD": "",
    })


class GunicornConfigurationTests(unittest.TestCase):
    def test_binds_every_interface_on_the_platform_port(self):
        config = load_gunicorn_config({"PORT": "8080"})
        self.assertEqual(config.bind, "0.0.0.0:8080")

    def test_falls_back_to_5000_for_local_development(self):
        config = load_gunicorn_config({"PORT": None})
        self.assertEqual(config.bind, "0.0.0.0:5000")

    def test_never_binds_the_loopback_address(self):
        config = load_gunicorn_config({"PORT": "8080"})
        self.assertNotIn("127.0.0.1", config.bind)
        self.assertNotIn("localhost", config.bind)

    def test_uses_one_worker_with_threads_for_sqlite(self):
        # SQLite permits a single writer; several *processes* on one file is
        # the classic "database is locked" failure. Concurrency comes from
        # threads, each request taking its own connection from flask.g.
        config = load_gunicorn_config({"GUNICORN_WORKERS": None})
        self.assertEqual(config.workers, 1)
        self.assertEqual(config.worker_class, "gthread")
        self.assertGreaterEqual(config.threads, 2)

    def test_logs_to_stdout_so_the_platform_captures_output(self):
        config = load_gunicorn_config({"PORT": "8080"})
        self.assertEqual(config.accesslog, "-")
        self.assertEqual(config.errorlog, "-")


class DockerfileTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = read("Dockerfile")

    def test_start_command_runs_gunicorn_against_the_wsgi_entry_point(self):
        cmd = re.search(r"^CMD\s+\[([^\]]+)\]", self.dockerfile, re.MULTILINE)
        self.assertIsNotNone(cmd, "Dockerfile must define a CMD start command")
        command = cmd.group(1)
        self.assertIn("gunicorn", command)
        self.assertIn("--config", command)
        self.assertIn("gunicorn.conf.py", command)
        self.assertIn("wsgi:app", command)

    def test_exposes_the_port_the_server_binds(self):
        exposed = re.search(r"^EXPOSE\s+(\d+)", self.dockerfile, re.MULTILINE)
        self.assertIsNotNone(exposed)
        self.assertEqual(exposed.group(1), "5000")

    def test_does_not_hardcode_a_loopback_bind(self):
        self.assertNotIn("127.0.0.1", self.dockerfile)
        self.assertNotIn("localhost", self.dockerfile)

    def test_installs_gunicorn_as_a_dependency(self):
        requirements = read("requirements.txt")
        self.assertIn("gunicorn", requirements.lower())
        self.assertIn("flask", requirements.lower())


class WsgiEntryPointTests(unittest.TestCase):
    def test_wsgi_module_exposes_the_application(self):
        source = read("wsgi.py")
        self.assertIn("from app import app", source)

    def test_development_entry_point_honours_port_and_binds_all_interfaces(self):
        source = read("app.py")
        self.assertIn('os.environ.get("PORT"', source)
        self.assertIn('host="0.0.0.0"', source)


class SecretKeyTests(unittest.TestCase):
    def test_production_without_a_secret_key_refuses_to_start(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                create_app({
                    "TESTING": False,
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": None,
                    "DATABASE": os.path.join(directory, "test.db"),
                    "UPLOAD_FOLDER": os.path.join(directory, "uploads"),
                })

    def test_config_has_no_predictable_secret_key_fallback(self):
        source = read("pageturner/config.py")
        assignment = re.search(r"^\s*SECRET_KEY\s*=\s*(.+)$", source, re.MULTILINE)
        self.assertIsNotNone(assignment)
        # Must read the environment and fall back to None, never to a literal.
        value = assignment.group(1).strip()
        self.assertIn("os.environ", value)
        self.assertTrue(value.endswith("or None"))
        # A bare string literal (e.g. SECRET_KEY = "changeme") is forbidden.
        self.assertFalse(
            value.startswith(("'", '"')),
            "SECRET_KEY must not fall back to a hardcoded literal",
        )


class PersistenceConfigurationTests(unittest.TestCase):
    def test_database_location_is_environment_driven(self):
        source = read("pageturner/config.py")
        self.assertIn("PAGETURNER_DATABASE", source)

    def test_upload_location_is_environment_driven(self):
        source = read("pageturner/config.py")
        self.assertIn("PAGETURNER_UPLOADS", source)

    def test_uploads_live_under_static_so_url_for_still_resolves(self):
        # Moving uploads outside static/ would break every cover image URL.
        source = read("pageturner/config.py")
        self.assertIn('"static"', source)
        self.assertIn('"uploads"', source)

    def test_instance_and_upload_directories_are_created_defensively(self):
        # Both may sit on a freshly mounted persistent disk.
        source = read("pageturner/__init__.py")
        self.assertIn("os.makedirs(app.instance_path", source)
        self.assertIn('os.makedirs(app.config["UPLOAD_FOLDER"]', source)


class HealthEndpointTests(unittest.TestCase):
    def test_health_returns_json_ok_without_redirecting(self):
        with tempfile.TemporaryDirectory() as directory:
            app = build_app(directory)
            response = app.test_client().get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"status": "ok"})

    def test_health_is_reachable_without_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            app = build_app(directory)
            response = app.test_client().get("/health", follow_redirects=False)
            self.assertNotIn(response.status_code, (301, 302, 401, 403))


if __name__ == "__main__":
    unittest.main()
