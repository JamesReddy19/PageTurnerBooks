"""Account anonymization (requirement 59).

The user's decision: deletion is confirmed with the account password, is
irreversible, keeps historical orders and invoices for accounting, blocks all
future logins and invalidates existing sessions.

The user row is *not* deleted.  Deleting it would cascade away order ownership
(financial records) and review history.  Instead every personal identifier is
replaced with a non-identifying placeholder and ``anonymized_at`` is stamped,
which every authentication path treats as "this account no longer exists".
"""

import secrets

from werkzeug.security import generate_password_hash

DELETED_NAME = "Deleted account"
DELETED_ORDER_NAME = "Deleted customer"
DELETED_ADDRESS = "Address removed"
DELETED_REVIEW_NAME = "Deleted reader"

#: A reserved TLD: mail to it can never be delivered.
DELETED_EMAIL_DOMAIN = "deleted.invalid"

#: Tables whose rows are personal state, not financial history.
PURGE_QUERIES = (
    "DELETE FROM user_sessions WHERE user_id = ?",
    "DELETE FROM password_reset_tokens WHERE user_id = ?",
    "DELETE FROM cart_items WHERE user_id = ?",
    "DELETE FROM wishlist WHERE user_id = ?",
    "DELETE FROM wishlist_shares WHERE user_id = ?",
    "DELETE FROM notifications WHERE user_id = ?",
    "DELETE FROM review_votes WHERE user_id = ?",
    "DELETE FROM review_reports WHERE reporter_user_id = ?",
    "DELETE FROM addresses WHERE user_id = ?",
    "DELETE FROM points_ledger WHERE user_id = ?",
)


def anonymized_email(user_id):
    """Unique, non-deliverable placeholder address for a deleted account."""
    return f"deleted-{user_id}-{secrets.token_hex(8)}@{DELETED_EMAIL_DOMAIN}"


def is_anonymized(user_row):
    """True when the row is a tombstone rather than a usable account."""
    if user_row is None:
        return True

    try:
        return user_row["anonymized_at"] is not None
    except (IndexError, KeyError):
        return False


def anonymize_user(connection, user_id):
    """Replace every personal identifier but keep the financial history.

    Returns the placeholder email, or None when the user does not exist or has
    already been anonymized (so repeating the request is harmless).
    """
    user = connection.execute(
        "SELECT id, email, anonymized_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if user is None or user["anonymized_at"] is not None:
        return None

    old_email = user["email"]
    placeholder = anonymized_email(user_id)

    connection.execute(
        """
        UPDATE users
        SET name = ?, email = ?, password_hash = ?, role = 'customer',
            anonymized_at = CURRENT_TIMESTAMP
        WHERE id = ? AND anonymized_at IS NULL
        """,
        (
            DELETED_NAME,
            placeholder,
            # An unguessable password so the old one can never match again.
            generate_password_hash(secrets.token_urlsafe(32)),
            user_id,
        ),
    )

    # Orders stay (accounting) but the personal delivery details go.
    connection.execute(
        """
        UPDATE orders
        SET name = ?, phone = '', address = ?, pincode = ''
        WHERE user_id = ?
        """,
        (DELETED_ORDER_NAME, DELETED_ADDRESS, user_id),
    )

    # Reviews keep their public content; the author's name snapshot goes.
    connection.execute(
        "UPDATE reviews SET name = ? WHERE user_id = ?",
        (DELETED_REVIEW_NAME, user_id),
    )

    for statement in PURGE_QUERIES:
        connection.execute(statement, (user_id,))

    connection.execute(
        "DELETE FROM login_attempts WHERE email = ?", (old_email,)
    )

    return placeholder
