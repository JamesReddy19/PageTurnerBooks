"""Simple application settings for PageTurner Books."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    # No predictable fallback: production must supply SECRET_KEY through the
    # environment (create_app refuses to start without it outside development).
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    ENVIRONMENT = (os.environ.get("APP_ENV") or "production").lower()
    DATABASE = os.environ.get("PAGETURNER_DATABASE", str(BASE_DIR / "instance" / "database.db"))
    # Point this at a mounted persistent disk if the host provides one.
    UPLOAD_FOLDER = os.environ.get("PAGETURNER_UPLOADS") or str(BASE_DIR / "static" / "uploads")
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    # Requirement 128: the free-delivery threshold lives here and nowhere else.
    # It is ₹500, stored as integer paise (500 * 100) so no float ever touches
    # the comparison. Every screen and both order paths read this one value.
    FREE_SHIPPING_PAISE = 50_000
    # The flat fee charged when an order is below the threshold and the pincode
    # has no shipping zone. Kept as a constant because the checkout fallback and
    # the pre-packing order edit must charge the same amount.
    DEFAULT_SHIPPING_PAISE = 8_000
    TAX_PERCENT = 5
    RESERVATION_MINUTES = 5

    # Requirement 48: optional gift wrapping at checkout.
    GIFT_WRAP_PAISE = 3000
    GIFT_MESSAGE_MAX_LENGTH = 150
