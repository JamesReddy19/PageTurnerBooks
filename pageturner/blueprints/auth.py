
"""Registration, login, password reset, profile, addresses and device sessions."""

import hashlib
import secrets
import time
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db
from ..helpers import current_user, login_required, make_reset_token
from ..privacy import is_anonymized
from ..totp import claim_step, matching_step
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

        # Requirement 59: an anonymized row is a tombstone, never a login.
        if (
            user
            and not is_anonymized(user)
            and check_password_hash(user["password_hash"], password)
        ):
            connection.execute(
                """
                DELETE FROM login_attempts
                WHERE email = ? AND ip_address = ?
                """,
                (email, ip_address),
            )
            connection.commit()

            # Requirement 71: a correct password only gets you as far as the
            # second-factor prompt.  Nothing is signed in yet - the pending
            # state is a user id plus its start time, and it is consumed by the
            # /login/2fa POST.
            if user["totp_enabled"]:
                session["pending_2fa_user_id"] = user["id"]
                session["pending_2fa_started"] = int(time.time())
                return redirect(url_for("auth.login_two_factor"))

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


# ---------------------------------------------------------------------------
# Requirement 71: two-factor authentication
# ---------------------------------------------------------------------------

# How long the password-only state stays valid.  Long enough to find a phone,
# short enough that a stolen session cookie is not a permanent key.
PENDING_2FA_TTL = 300

# Ten consecutive wrong codes and the pending login is abandoned, so the second
# factor cannot be brute-forced with a password alone.
MAX_2FA_ATTEMPTS = 10


def _pending_two_factor_user():
    """Return the user awaiting a second factor, or ``None``.

    Expired or malformed pending state is cleared as a side effect, so the
    caller never has to reason about it.
    """
    user_id = session.get("pending_2fa_user_id")
    started = session.get("pending_2fa_started")

    if not user_id or not started:
        return None

    if time.time() - int(started) > PENDING_2FA_TTL:
        _clear_pending_two_factor()
        return None

    user = get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if user is None or not user["totp_enabled"] or is_anonymized(user):
        _clear_pending_two_factor()
        return None

    return user


def _clear_pending_two_factor():
    session.pop("pending_2fa_user_id", None)
    session.pop("pending_2fa_started", None)
    session.pop("pending_2fa_attempts", None)


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_two_factor():
    """Second step of login for accounts with a confirmed authenticator."""
    user = _pending_two_factor_user()

    if user is None:
        flash("Please log in again.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        attempts = int(session.get("pending_2fa_attempts", 0)) + 1

        if attempts > MAX_2FA_ATTEMPTS:
            _clear_pending_two_factor()
            flash("Too many incorrect codes. Please log in again.", "error")
            return redirect(url_for("auth.login"))

        connection = get_db()
        step = matching_step(user["totp_secret"], request.form.get("code", ""))

        # claim_step makes the code single-use: a replay of an already accepted
        # step fails even though the digits would still validate.
        if step is not None and claim_step(connection, user["id"], step):
            connection.commit()
            _clear_pending_two_factor()
            save_login_session(user)
            flash("Signed in.", "success")
            return redirect(url_for("shop.index"))

        connection.rollback()
        session["pending_2fa_attempts"] = attempts
        flash("That code is not valid. Try the current one.", "error")

    return render_template("login_two_factor.html")


@auth_bp.route("/security/2fa", methods=["GET", "POST"])
@login_required
def manage_two_factor():
    """Enrol or disable the authenticator app on the signed-in account."""
    user = current_user()
    connection = get_db()

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "start":
            # Generate a secret but do not enable it: the user must prove they
            # can produce a code before it becomes a login requirement,
            # otherwise a bad copy-paste locks them out of their account.
            secret = generate_secret()
            session["pending_totp_secret"] = secret
            connection.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
                (secret, user["id"]),
            )
            connection.commit()
            return redirect(url_for("auth.manage_two_factor"))

        if action == "confirm":
            secret = session.get("pending_totp_secret") or user["totp_secret"]
            step = matching_step(secret, request.form.get("code", ""))

            if secret and step is not None and claim_step(
                connection, user["id"], step
            ):
                connection.execute(
                    """
                    UPDATE users SET totp_secret = ?, totp_enabled = 1
                    WHERE id = ?
                    """,
                    (secret, user["id"]),
                )
                connection.commit()
                session.pop("pending_totp_secret", None)
                flash("Two-factor authentication is now on.", "success")
            else:
                connection.rollback()
                flash("That code is not valid. Nothing has changed.", "error")

            return redirect(url_for("auth.manage_two_factor"))

        if action == "disable":
            # Turning it off requires the current password, so a borrowed
            # session alone cannot weaken the account's protection.
            password = request.form.get("password", "")

            if not check_password_hash(user["password_hash"], password):
                flash("Password is incorrect.", "error")
            else:
                connection.execute(
                    """
                    UPDATE users SET totp_secret = NULL, totp_enabled = 0
                    WHERE id = ?
                    """,
                    (user["id"],),
                )
                connection.execute(
                    "DELETE FROM totp_used_codes WHERE user_id = ?", (user["id"],)
                )
                connection.commit()
                session.pop("pending_totp_secret", None)
                flash("Two-factor authentication is off.", "success")

            return redirect(url_for("auth.manage_two_factor"))

    secret = session.get("pending_totp_secret")

    return render_template(
        "two_factor.html",
        enabled=bool(user["totp_enabled"]),
        pending_secret=secret,
        provisioning_uri=(
            provisioning_uri(secret, user["email"]) if secret else None
        ),
    )


# Import route modules after the shared Blueprint and login helpers exist.
from . import auth_profile, auth_addresses  # noqa: E402,F401
