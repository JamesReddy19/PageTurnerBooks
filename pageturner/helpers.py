
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
    """Format integer paise as Indian rupees without float arithmetic."""
    paise = int(paise or 0)
    rupees, cents = divmod(abs(paise), 100)
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{rupees:,}" + (f".{cents:02d}" if cents else "")


def rupees_to_paise(value):
    """Convert a rupee amount to integer paise using decimal text."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

    try:
        amount = Decimal(str(value).strip())

        if amount < 0:
            raise ValueError

        return int(
            (amount * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid non-negative price.")


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    return get_db().execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()

        if user is None:
            flash("Please log in to continue.", "error")
            return redirect(
                url_for("auth.login", next=requested_path())
            )

        return view(*args, **kwargs)

    return wrapped


def requested_path():
    from flask import request

    return (
        request.full_path
        if request.query_string
        else request.path
    )


def role_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()

            if user is None:
                if (
                    session.get("admin_password_login")
                    and "admin" in roles
                ):
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
        """
        INSERT INTO admin_log(actor_user_id, action, detail)
        VALUES (?, ?, ?)
        """,
        (
            user["id"] if user else None,
            action,
            str(detail)[:500],
        ),
    )


def add_notification(user_id, message, link="/"):
    if user_id:
        get_db().execute(
            """
            INSERT INTO notifications(user_id, message, link)
            VALUES (?, ?, ?)
            """,
            (user_id, message, link),
        )


def active_reservations(connection, book_id, exclude_cart_key=None):
    now = datetime.now().isoformat(timespec="seconds")

    if exclude_cart_key:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS amount
            FROM reservations
            WHERE book_id = ?
              AND expires_at > ?
              AND cart_key != ?
            """,
            (book_id, now, exclude_cart_key),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS amount
            FROM reservations
            WHERE book_id = ?
              AND expires_at > ?
            """,
            (book_id, now),
        ).fetchone()

    return row["amount"]


def make_reset_token():
    token = secrets.token_urlsafe(32)

    return (
        token,
        hashlib.sha256(token.encode()).hexdigest(),
        (
            datetime.now() + timedelta(minutes=15)
        ).isoformat(timespec="seconds"),
    )


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def audit_action(action):
    """Record successful admin POST actions after the route runs."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            response = view(*args, **kwargs)

            if response.status_code < 400:
                log_admin_action(action, request.path)
                get_db().commit()

            return response

        return wrapped

    return decorate


def effective_book_price(book, now=None):
    """
    Return the active sale price or the normal price.

    sale_start and sale_end are stored as ISO date-time strings.
    The sale is active at its start time and ends at sale_end.
    """
    normal_price = int(book["price_paise"])

    try:
        sale_price = book["sale_price_paise"]
        sale_start = book["sale_start"]
        sale_end = book["sale_end"]
    except (KeyError, IndexError):
        return normal_price

    if sale_price is None or not sale_start or not sale_end:
        return normal_price

    current_time = (
        now or datetime.now()
    ).isoformat(timespec="seconds")

    if sale_start <= current_time < sale_end:
        sale_price = int(sale_price)

        if 0 <= sale_price < normal_price:
            return sale_price

    return normal_price


def sale_ends_in_seconds(book, now=None):
    """
    Return the seconds left in the active flash sale, or 0 if none is running.

    Requirement 47 shows a countdown, so the remaining time has to come from the
    same window comparison ``effective_book_price`` uses. Returning 0 for every
    "no sale" case keeps the templates simple: `if remaining` means "show timer".
    """
    try:
        sale_price = book["sale_price_paise"]
        sale_start = book["sale_start"]
        sale_end = book["sale_end"]
        normal_price = int(book["price_paise"])
    except (KeyError, IndexError, TypeError):
        return 0

    if sale_price is None or not sale_start or not sale_end:
        return 0

    # A "sale" that is not actually cheaper is not a running sale.
    try:
        if not 0 <= int(sale_price) < normal_price:
            return 0
    except (TypeError, ValueError):
        return 0

    current = now or datetime.now()

    if not (sale_start <= current.isoformat(timespec="seconds") < sale_end):
        return 0

    try:
        end = datetime.fromisoformat(sale_end)
    except (TypeError, ValueError):
        return 0

    return max(0, int((end - current).total_seconds()))


def loyalty_points_balance(connection, user_id):
    """Calculate a user's current points from the ledger."""
    row = connection.execute(
        """
        SELECT COALESCE(SUM(points_change), 0) AS balance
        FROM points_ledger
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    return max(0, int(row["balance"]))


def loyalty_points_earned(spent_paise):
    """Award one point for each complete ₹100 spent."""
    return max(0, int(spent_paise)) // 10000


def loyalty_discount_paise(points):
    """Every 100 points gives ₹10 off, or 10 paise per point."""
    return max(0, int(points)) * 10
