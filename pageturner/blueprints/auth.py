
"""Registration, login, password reset, profile, addresses and device sessions."""

import hashlib
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db
from ..helpers import current_user, login_required, make_reset_token
from ..privacy import is_anonymized
from .auth_helpers import save_login_session


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if len(name) < 2 or "@" not in email or len(password) < 8:
            flash(
                "Enter a name, valid email and password of at least 8 characters.",
                "error",
            )
        else:
            try:
                connection = get_db()
                connection.execute(
                    """
                    INSERT INTO users(name, email, password_hash, role)
                    VALUES (?, ?, ?, 'customer')
                    """,
                    (name, email, generate_password_hash(password)),
                )
                connection.commit()

                flash("Your account has been created. Please log in.", "success")
                return redirect(url_for("auth.login"))

            except Exception:
                get_db().rollback()
                flash("That email address may already be registered.", "error")

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        ip_address = request.remote_addr or "unknown"

        connection = get_db()

        attempt = connection.execute(
            """
            SELECT *
            FROM login_attempts
            WHERE email = ? AND ip_address = ?
            """,
            (email, ip_address),
        ).fetchone()

        now = datetime.now()

        # Lock the account/IP combination for 5 minutes after repeated failures.
        if (
            attempt
            and attempt["locked_until"]
            and attempt["locked_until"] > now.isoformat(timespec="seconds")
        ):
            flash(
                "Too many failed attempts. Please wait 5 minutes before trying again.",
                "error",
            )
            return render_template("login.html"), 429

        user = connection.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            connection.execute(
                """
                DELETE FROM login_attempts
                WHERE email = ? AND ip_address = ?
                """,
                (email, ip_address),
            )
            connection.commit()

            save_login_session(user)

            destination = request.args.get("next", "")
            if destination.startswith("/") and not destination.startswith("//"):
                return redirect(destination)

            return redirect(url_for("shop.index"))

        # Start a new failed-attempt window after 5 minutes.
        if (
            not attempt
            or (
                now - datetime.fromisoformat(attempt["window_started"])
            ).total_seconds() > 300
        ):
            failed_count = 1

            connection.execute(
                """
                INSERT INTO login_attempts
                    (email, ip_address, failed_count, window_started)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email, ip_address)
                DO UPDATE SET
                    failed_count = 1,
                    window_started = excluded.window_started,
                    locked_until = NULL
                """,
                (
                    email,
                    ip_address,
                    failed_count,
                    now.isoformat(timespec="seconds"),
                ),
            )
        else:
            failed_count = attempt["failed_count"] + 1

            # Apply a 5-minute lockout after 5 failed attempts.
            locked_until = (
                now + timedelta(minutes=5)
            ).isoformat(timespec="seconds") if failed_count >= 5 else None

            connection.execute(
                """
                UPDATE login_attempts
                SET failed_count = ?, locked_until = ?
                WHERE email = ? AND ip_address = ?
                """,
                (failed_count, locked_until, email, ip_address),
            )

        connection.commit()
        flash("Email or password is incorrect.", "error")

    return render_template("login.html")


@auth_bp.get("/logout")
def logout():
    token = session.get("session_token")
    user_id = session.get("user_id")

    if token and user_id:
        get_db().execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE token_hash = ? AND user_id = ?
            """,
            (hashlib.sha256(token.encode()).hexdigest(), user_id),
        )
        get_db().commit()

    session.clear()
    flash("You have been logged out.", "success")

    return redirect(url_for("shop.index"))


@auth_bp.route("/password-reset", methods=["GET", "POST"])
def password_reset_request():
    reset_token = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        user = get_db().execute(
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if user:
            token, token_hash, expires_at = make_reset_token()

            get_db().execute(
                """
                INSERT INTO password_reset_tokens
                    (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (user["id"], token_hash, expires_at),
            )
            get_db().commit()

            # No email server is configured, so show the token for this demo.
            reset_token = token
            flash(
                "For this no-email demo, use the one-time token shown below within 15 minutes.",
                "success",
            )
        else:
            flash(
                "If that account exists, a reset token has been created.",
                "success",
            )

    return render_template(
        "password_reset_request.html",
        reset_token=reset_token,
    )


@auth_bp.route("/password-reset/confirm", methods=["GET", "POST"])
def password_reset_confirm():
    if request.method == "POST":
        token = request.form.get("token", "")
        password = request.form.get("password", "")

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        connection = get_db()

        if len(password) >= 8:
            try:
                connection.execute("BEGIN IMMEDIATE")

                row = connection.execute(
                    """
                    SELECT *
                    FROM password_reset_tokens
                    WHERE token_hash = ?
                      AND used_at IS NULL
                      AND expires_at > ?
                    """,
                    (
                        token_hash,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                ).fetchone()

                if not row:
                    raise ValueError("The token is invalid or expired.")

                changed = connection.execute(
                    """
                    UPDATE password_reset_tokens
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND used_at IS NULL
                    """,
                    (row["id"],),
                ).rowcount

                if changed != 1:
                    raise ValueError("This token has already been used.")

                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(password), row["user_id"]),
                )

                # Log out all existing sessions for this user.
                connection.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (row["user_id"],),
                )

                connection.commit()
                flash("Password changed. Please log in again.", "success")

                return redirect(url_for("auth.login"))

            except ValueError:
                connection.rollback()

        flash(
            "The token is invalid or expired, or the password is too short.",
            "error",
        )

    return render_template("password_reset_confirm.html")


# Import route modules after the shared Blueprint and login helpers exist.
from . import auth_profile, auth_addresses  # noqa: E402,F401
