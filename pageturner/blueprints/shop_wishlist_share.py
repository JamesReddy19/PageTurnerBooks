"""Public wishlist sharing (requirement 53).

A shared wishlist is addressed by a random token whose SHA-256 digest is what
the database stores, so a leaked database dump cannot be replayed to read
anyone's list.  Revoking writes ``revoked_at``; a revoked or unknown token
returns 404 rather than an empty page, so a revoked link is indistinguishable
from one that never existed.
"""

import hashlib
import secrets

from flask import abort, flash, redirect, render_template, url_for

from ..db import get_db
from ..helpers import current_user, login_required
from .shop import shop_bp


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def active_share_token(connection, user_id):
    """Return the plaintext token for this user, if a live share exists.

    The plaintext cannot be recovered from the digest, so the caller is
    expected to create a new token when it needs one to show.  This helper
    only answers "is there a live share?" for the settings panel.
    """
    return connection.execute(
        """
        SELECT id, created_at, revoked_at
        FROM wishlist_shares
        WHERE user_id = ? AND revoked_at IS NULL
        """,
        (user_id,),
    ).fetchone()


def create_share(connection, user_id):
    """Create (or replace) this user's share token and return the plaintext."""
    token = secrets.token_urlsafe(32)

    # One row per user, enforced by the UNIQUE(user_id) constraint.
    connection.execute(
        """
        INSERT INTO wishlist_shares (user_id, token_hash, created_at, revoked_at)
        VALUES (?, ?, CURRENT_TIMESTAMP, NULL)
        ON CONFLICT(user_id) DO UPDATE SET
            token_hash = excluded.token_hash,
            created_at = CURRENT_TIMESTAMP,
            revoked_at = NULL
        """,
        (user_id, _digest(token)),
    )

    return token


def share_for_token(connection, token):
    """Return the live share row for a plaintext token, else None."""
    if not token:
        return None

    return connection.execute(
        """
        SELECT *
        FROM wishlist_shares
        WHERE token_hash = ? AND revoked_at IS NULL
        """,
        (_digest(token),),
    ).fetchone()


@shop_bp.post("/wishlist/share")
@login_required
def create_wishlist_share():
    connection = get_db()
    user = current_user()

    token = create_share(connection, user["id"])
    connection.commit()

    flash("Share link created. Anyone with the link can view your wishlist.", "success")

    return redirect(url_for("shop.wishlist"))


@shop_bp.post("/wishlist/share/revoke")
@login_required
def revoke_wishlist_share():
    connection = get_db()
    user = current_user()

    connection.execute(
        """
        UPDATE wishlist_shares
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND revoked_at IS NULL
        """,
        (user["id"],),
    )
    connection.commit()

    flash("Share link revoked. The old link no longer works.", "success")

    return redirect(url_for("shop.wishlist"))


@shop_bp.get("/wishlist/shared/<token>")
def shared_wishlist(token):
    """Read-only public view of a shared wishlist."""
    connection = get_db()
    share = share_for_token(connection, token)

    if share is None:
        abort(404)

    owner = connection.execute(
        "SELECT id, name FROM users WHERE id = ?", (share["user_id"],)
    ).fetchone()

    if owner is None:
        abort(404)

    books = connection.execute(
        """
        SELECT b.*
        FROM wishlist w
        JOIN books b ON b.id = w.book_id
        WHERE w.user_id = ?
        ORDER BY w.id DESC
        """,
        (share["user_id"],),
    ).fetchall()

    return render_template(
        "shared_wishlist.html",
        books=books,
        owner_name=owner["name"],
    )
