"""Customer profile, signed-in device sessions and personal data export."""
import hashlib
import json
from datetime import datetime, timezone

from flask import (
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from .. import apikeys
from ..db import get_db
from ..helpers import current_user, login_required
from ..privacy import anonymize_user
from .auth import auth_bp

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    connection = get_db()
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        if not check_password_hash(user["password_hash"], current_password):
            flash("Your current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("The new password must contain at least 8 characters.", "error")
        else:
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user["id"]))
            connection.commit()
            flash("Your password has been changed.", "success")
            return redirect(url_for("auth.profile"))
    stats = connection.execute("SELECT COUNT(*) AS order_count, COALESCE(SUM(total_paise),0) AS spent FROM orders WHERE user_id = ?", (user["id"],)).fetchone()
    return render_template("profile.html", stats=stats)


@auth_bp.get("/account/delete")
@login_required
def delete_account_page():
    """Confirmation screen for requirement 59."""
    user = current_user()

    order_count = get_db().execute(
        "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (user["id"],)
    ).fetchone()["c"]

    return render_template("delete_account.html", order_count=order_count)


@auth_bp.post("/account/delete")
@login_required
def delete_account():
    """Anonymize the account (requirement 59).

    Password-confirmed and irreversible.  Orders and invoices survive for
    accounting; every personal identifier is replaced and all sessions and
    other personal state are purged.
    """
    connection = get_db()
    user = current_user()

    password = request.form.get("password", "")
    confirmation = (request.form.get("confirm") or "").strip().upper()

    if confirmation != "DELETE":
        flash("Type DELETE to confirm you want to close your account.", "error")
        return redirect(url_for("auth.delete_account_page"))

    if not password or not check_password_hash(user["password_hash"], password):
        flash("Your password is incorrect. Your account was not changed.", "error")
        return redirect(url_for("auth.delete_account_page"))

    connection.execute(
        "DELETE FROM user_sessions WHERE user_id = ?", (user["id"],)
    )

    placeholder = anonymize_user(connection, user["id"])

    if placeholder is None:
        connection.rollback()
        flash("That account has already been closed.", "error")
        return redirect(url_for("auth.login"))

    connection.commit()

    session.clear()
    flash(
        "Your account has been closed and your personal details removed. "
        "Past orders are kept for accounting only.",
        "success",
    )

    return redirect(url_for("shop.index"))


def _export_rows(connection, sql, params):
    """Run a query and return plain dicts (JSON-serialisable, no sqlite3.Row)."""
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


@auth_bp.get("/account/export.json")
@login_required
def export_my_data():
    """
    Download everything PageTurner stores about the signed-in customer (req 58).

    Deliberately excludes the password hash, session token hashes and password
    reset token hashes: they are credentials, not user data, and shipping them
    in a download would turn a convenience feature into a security hole.  A
    customer who wants to know *that* an account has credentials still sees the
    account row itself.
    """
    connection = get_db()
    user = current_user()
    user_id = user["id"]

    account = {
        key: user[key]
        for key in user.keys()
        if key not in {"password_hash"}
    }

    orders = _export_rows(
        connection,
        """
        SELECT id, date, status, name, phone, address, pincode,
               subtotal_paise, discount_paise, shipping_paise, tax_paise,
               total_paise, coupon_code, gift_wrap_paise, gift_message,
               loyalty_points_earned, loyalty_points_redeemed
        FROM orders
        WHERE user_id = ?
        ORDER BY id
        """,
        (user_id,),
    )

    for order in orders:
        order["items"] = _export_rows(
            connection,
            """
            SELECT oi.book_id, b.title, b.author, oi.quantity,
                   oi.price_paise, oi.returned_quantity
            FROM order_items oi
            JOIN books b ON b.id = oi.book_id
            WHERE oi.order_id = ?
            ORDER BY oi.id
            """,
            (order["id"],),
        )
        order["bundles"] = _export_rows(
            connection,
            """
            SELECT bundle_name, quantity, price_paise
            FROM order_bundles
            WHERE order_id = ?
            ORDER BY id
            """,
            (order["id"],),
        )
        order["status_history"] = _export_rows(
            connection,
            """
            SELECT old_status, new_status, note, created_at
            FROM order_status_history
            WHERE order_id = ?
            ORDER BY id
            """,
            (order["id"],),
        )

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "account": account,
        "orders": orders,
        "loyalty_ledger": _export_rows(
            connection,
            """
            SELECT order_id, points_change, reason, created_at
            FROM points_ledger
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ),
        "reviews": _export_rows(
            connection,
            """
            SELECT r.book_id, b.title, r.rating, r.comment, r.created_at
            FROM reviews r
            JOIN books b ON b.id = r.book_id
            WHERE r.user_id = ?
            ORDER BY r.id
            """,
            (user_id,),
        ),
        "wishlist": _export_rows(
            connection,
            """
            SELECT w.book_id, b.title, b.author
            FROM wishlist w
            JOIN books b ON b.id = w.book_id
            WHERE w.user_id = ?
            ORDER BY w.id
            """,
            (user_id,),
        ),
        "addresses": _export_rows(
            connection,
            """
            SELECT label, line1, city, pincode, is_default
            FROM addresses
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ),
        "cart_items": _export_rows(
            connection,
            """
            SELECT c.book_id, b.title, c.quantity
            FROM cart_items c
            JOIN books b ON b.id = c.book_id
            WHERE c.user_id = ?
            ORDER BY c.book_id
            """,
            (user_id,),
        ),
        "returns": _export_rows(
            connection,
            """
            SELECT oi.order_id, r.quantity, r.reason, r.status, r.created_at
            FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            WHERE r.user_id = ?
            ORDER BY r.id
            """,
            (user_id,),
        ),
        "sessions": _export_rows(
            connection,
            """
            SELECT device_name, created_at, last_seen, revoked_at
            FROM user_sessions
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ),
        "notifications": _export_rows(
            connection,
            """
            SELECT message, link, is_read, created_at
            FROM notifications
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ),
    }

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    response = Response(body, mimetype="application/json")
    response.headers["Content-Disposition"] = (
        'attachment; filename="pageturner-account-export.json"'
    )
    # A personal data export must never sit in a shared cache.
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.get("/devices")
@login_required
def devices():
    rows = get_db().execute("SELECT * FROM user_sessions WHERE user_id = ? ORDER BY id DESC", (session["user_id"],)).fetchall()
    return render_template("devices.html", devices=rows)


@auth_bp.post("/devices/revoke/<int:session_id>")
@login_required
def revoke_device(session_id):
    connection = get_db()
    connection.execute("UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?", (session_id, session["user_id"]))
    connection.commit()
    flash("That device session has been revoked.", "success")
    return redirect(url_for("auth.devices"))


@auth_bp.post("/devices/revoke-all")
@login_required
def revoke_all_devices():
    connection = get_db()
    connection.execute("UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = ?", (session["user_id"],))
    connection.commit()
    session.clear()
    flash("All device sessions have been revoked. Please log in again.", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Requirement 72: API keys with scopes
# ---------------------------------------------------------------------------

def _api_keys_for(user_id):
    return get_db().execute(
        "SELECT * FROM api_keys WHERE user_id = ? ORDER BY id DESC", (user_id,)
    ).fetchall()


@auth_bp.get("/api-keys")
@login_required
def api_keys_page():
    return render_template(
        "api_keys.html",
        keys=_api_keys_for(session["user_id"]),
        available_scopes=apikeys.SCOPES,
    )


@auth_bp.post("/api-keys/create")
@login_required
def create_api_key():
    """Issue a key and show the secret exactly once.

    The plaintext token is never stored: only a hash and a short prefix are
    kept, so a database leak cannot be replayed against the API.  That means
    this response is the customer's only chance to copy it.
    """
    user_id = session["user_id"]
    name = (request.form.get("name") or "").strip() or "Unnamed key"
    requested = request.form.getlist("scopes")

    granted, error = apikeys.validate_scopes(requested)

    if error:
        flash(error, "error")
        return redirect(url_for("auth.api_keys_page"))

    token, prefix = apikeys.issue(get_db(), user_id, name, granted)
    get_db().commit()

    return render_template(
        "api_key_issued.html",
        token=token,
        prefix=prefix,
        name=name,
        scopes=granted,
    )


@auth_bp.post("/api-keys/<int:key_id>/revoke")
@login_required
def revoke_api_key(key_id):
    connection = get_db()
    row = connection.execute(
        "SELECT id FROM api_keys WHERE id = ? AND user_id = ?",
        (key_id, session["user_id"]),
    ).fetchone()

    # Scoped to the owner, so one customer can never revoke another's key.
    if row is None:
        flash("That API key does not exist.", "error")
    else:
        apikeys.revoke(connection, key_id, session["user_id"])
        connection.commit()
        flash("API key revoked.", "success")

    return redirect(url_for("auth.api_keys_page"))
