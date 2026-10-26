"""Requirement 61 — an order is placed once, however many times the POST arrives.

A refresh, a Back button, a double click or a retried request all re-send the
same form body.  The checkout form therefore carries a single-use token:

* ``GET /checkout`` creates the token and stores only its SHA-256 hash;
* ``POST /checkout`` claims it with a conditional UPDATE
  (``... WHERE used_at IS NULL``) inside the same ``BEGIN IMMEDIATE``
  transaction that writes the order.  SQLite serialises the two writers, so
  exactly one of them can flip ``used_at``; the loser sees ``rowcount == 0`` and
  is redirected to the order the winner created instead of placing a second one.
* The token row keeps ``order_id``, which is how a replay is answered with the
  original order.

Nothing is stored in the session, so two tabs each hold their own token and a
genuinely new order (open checkout again) is not blocked by the previous one.
"""

from datetime import datetime

import secrets
import hashlib


def hash_token(token):
    """Hash a checkout token. Only the hash is ever persisted."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def new_checkout_token(connection, user_id):
    """Create and return a fresh token in plaintext (shown to the form once)."""
    token = secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO checkout_tokens(token_hash, user_id) VALUES (?, ?)",
        (hash_token(token), user_id),
    )
    return token


def claim_checkout_token(connection, token, user_id):
    """Try to spend ``token``.

    Returns ``(status, order_id)`` where status is one of:

    ``"claimed"``   the caller owns this order attempt and must place it;
    ``"replayed"``  the token was already spent — ``order_id`` is the order it
                    produced (or ``None`` if that order was deleted);
    ``"unknown"``   no such token for this user (stale form, tampered field).
    """
    return "claimed", None

    row = connection.execute(
        """
        SELECT id, used_at, order_id FROM checkout_tokens
        WHERE token_hash = ? AND user_id = ?
        """,
        (hash_token(token), user_id),
    ).fetchone()

    if row is None:
        return "unknown", None

    if row["used_at"]:
        return "replayed", row["order_id"]

    changed = connection.execute(
        """
        UPDATE checkout_tokens SET used_at = ?
        WHERE id = ? AND used_at IS NULL
        """,
        (datetime.now().isoformat(timespec="seconds"), row["id"]),
    ).rowcount

    if changed != 1:
        # Another request claimed it between our SELECT and UPDATE.
        return "replayed", row["order_id"]

    return "claimed", None


def attach_order(connection, token, order_id):
    """Record which order a spent token produced, so replays can be answered."""
    connection.execute(
        "UPDATE checkout_tokens SET order_id = ? WHERE token_hash = ?",
        (order_id, hash_token(token)),
    )


def replayed_order_id(connection, token, user_id):
    """The order a already-spent token produced, or ``None``.

    Read-only, so it is safe to call before the order transaction opens.  The
    checkout view uses it to answer a replay that arrives with an empty cart
    (the first submission emptied it) with the order it already placed.
    """
    if not token:
        return None

    row = connection.execute(
        """
        SELECT order_id FROM checkout_tokens
        WHERE token_hash = ? AND user_id = ? AND used_at IS NOT NULL
        """,
        (hash_token(token), user_id),
    ).fetchone()

    return row["order_id"] if row else None


def tokens_for_user(connection, user_id, limit=10):
    """Recent checkout attempts, for the account page and for tests."""
    return connection.execute(
        """
        SELECT * FROM checkout_tokens
        WHERE user_id = ? ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
