"""Simple application settings for PageTurner Books."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "pageturner-local-dev-key-change-before-deployment"
    DATABASE = os.environ.get("PAGETURNER_DATABASE", str(BASE_DIR / "instance" / "database.db"))
    UPLOAD_FOLDER = str(BASE_DIR / "static" / "uploads")
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    FREE_SHIPPING_PAISE = 100000
    TAX_PERCENT = 5
    RESERVATION_MINUTES = 5

    # Email delivery. Without MAIL_SERVER the app writes messages to
    # instance/mail_outbox.log instead of sending them.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get(
        "MAIL_DEFAULT_SENDER", "no-reply@pageturnerbooks.local"
    )

    DEBUG = False
