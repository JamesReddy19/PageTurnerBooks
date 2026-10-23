"""Shared helpers used by the route files."""
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import abort, flash, redirect, session, url_for, request
from werkzeug.security import check_password_hash
from .db import get_db

ORDER_STATUS_TRANSITIONS = {
    "Placed": {"Packed", "Cancelled"},
    "Packed": {"Shipped", "Cancelled"},
    "Shipped": {"In Transit"},
    "In Transit": {"Out for Delivery"},
    "Out for Delivery": {"Delivered"},
    "Delivered": set(),
    "Cancelled": set(),
}


def money(paise):
    """Format integer paise as Indian rupees without using float arithmetic."""
    paise = int(paise or 0)
    rupees, cents = divmod(abs(paise), 100)
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{rupees:,}" + (f".{cents:02d}" if cents else "")


def rupees_to_paise(value):
    """Convert a user-entered rupee amount to integer paise using decimal text."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        amount = Decimal(str(value).strip())
        if amount < 0:
            raise ValueError
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid non-negative price.")


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("auth.login", next=requested_path()))
        return view(*args, **kwargs)
    return wrapped


def requested_path():
    from flask import request
    return request.full_path if request.query_string else request.path


def role_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                if session.get("admin_password_login") and "admin" in roles:
                    return view(*args, **kwargs)
                abort(403)
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


def make_session_token():
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def log_admin_action(action, detail=""):
    user = current_user()
    get_db().execute(
        "INSERT INTO admin_log(actor_user_id, action, detail) VALUES (?, ?, ?)",
        (user["id"] if user else None, action, str(detail)[:500]),
    )


def add_notification(user_id, message, link="/"):
    if user_id:
        get_db().execute(
            "INSERT INTO notifications(user_id, message, link) VALUES (?, ?, ?)",
            (user_id, message, link),
        )


def active_reservations(connection, book_id, exclude_cart_key=None):
    now = datetime.now().isoformat(timespec="seconds")
    if exclude_cart_key:
        row = connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS amount FROM reservations WHERE book_id = ? AND expires_at > ? AND cart_key != ?",
            (book_id, now, exclude_cart_key),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS amount FROM reservations WHERE book_id = ? AND expires_at > ?",
            (book_id, now),
        ).fetchone()
    return row["amount"]


def make_reset_token():
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest(), (datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds")


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def audit_action(action):
    """Record successful admin POST actions automatically after the route runs."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            response = view(*args, **kwargs)
            from flask import request
            if response.status_code < 400:
                log_admin_action(action, request.path)
                get_db().commit()
            return response
        return wrapped
    return decorate
