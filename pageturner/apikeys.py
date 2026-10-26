
"""Scoped API keys (requirement 72).

A token is shown to its owner exactly once, at creation time.  What the
database keeps is the first eight characters (an indexed *lookup handle*) and a
SHA-256 digest of the whole token (what is actually compared).  A stolen
database therefore yields neither working tokens nor enough material to forge
one, and lookup stays a single indexed query instead of a scan over every key.

Scopes are a plain set on the key row.  The API checks
``require_scope("account:read")``; the key must carry it or the request is
rejected with 403.  Anonymous calls remain possible for the public catalogue,
but they are rate-limited per IP, while a key gets its own larger bucket - see
``reserve_quota``.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone


# Every scope the API understands.  Adding an entry here is what makes it
# assignable; the description is shown on the key management page.
SCOPES = {
    "books:read": "Read the public catalogue",
    "account:read": "Read the account the key belongs to",
    "inventory:read": "Read stock levels for the catalogue",
}

DEFAULT_SCOPES = ("books:read",)

TOKEN_PREFIX = "ptk"
PREFIX_LENGTH = 8

# Requests per minute.  Anonymous callers share one bucket per IP (requirement
# 25 keeps its 30/min allowance); each key gets a bucket of its own.
ANONYMOUS_QUOTA = 30
KEY_QUOTA = 120
QUOTA_WINDOW = timedelta(minutes=1)


def hash_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _clean_scopes(scopes):
    cleaned = []

    for scope in scopes or ():
        scope = (scope or "").strip()

        if not scope:
            continue

        if scope not in SCOPES:
            raise ValueError(f"Unknown API scope: {scope}")

        if scope not in cleaned:
            cleaned.append(scope)

    return sorted(cleaned)


def scopes_of(key_row):
    """The scopes stored on a key row, as a set."""
    if key_row is None:
        return set()

    return {
        scope.strip()
        for scope in (key_row["scopes"] or "").split(",")
        if scope.strip()
    }


def create_key(connection, user_id, name, scopes=None):
    """Mint a key and return ``(key_row, plaintext_token)``.

    The plaintext is returned rather than stored, so this is the only moment it
    can be shown to the user.
    """
    label = (name or "").strip()

    if not label:
        raise ValueError("Give the key a name so it can be recognised later.")

    cleaned = _clean_scopes(scopes if scopes is not None else DEFAULT_SCOPES)

    if not cleaned:
        raise ValueError("A key needs at least one scope.")

    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    # The secret alphabet must not contain "_" - that is the token's own field
    # delimiter, so a secret containing one would make the token unparseable at
    # authentication time and the key would silently never work.
    secret = secrets.token_hex(24)
    token = f"{TOKEN_PREFIX}_{prefix}_{secret}"

    cursor = connection.execute(
        """
        INSERT INTO api_keys(user_id, name, prefix, key_hash, scopes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, label, prefix, hash_token(token), ",".join(cleaned)),
    )

    row = connection.execute(
        "SELECT * FROM api_keys WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()

    return row, token


def lookup(connection, token):
    """Resolve a presented token to its key row, or ``None``.

    Revoked keys, keys of anonymized accounts (deleted by the CASCADE) and
    malformed tokens all resolve to ``None``.  The digest comparison is constant
    time, so a wrong token cannot be distinguished from a nearly-right one.
    """
    token = (token or "").strip()

    if not token.startswith(TOKEN_PREFIX + "_"):
        return None

    parts = token.split("_")

    if len(parts) != 3 or len(parts[1]) != PREFIX_LENGTH:
        return None

    row = connection.execute(
        "SELECT * FROM api_keys WHERE prefix = ? AND revoked_at IS NULL",
        (parts[1],),
    ).fetchone()

    if row is None:
        return None

    if not hmac.compare_digest(row["key_hash"], hash_token(token)):
        return None

    return row


def touch(connection, key_row):
    """Record that a key was used just now."""
    if key_row is None:
        return

    connection.execute(
        "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
        (key_row["id"],),
    )
    connection.commit()


def revoke(connection, user_id, key_id):
    """Revoke one of ``user_id``'s own keys.  Returns True when a row changed."""
    cursor = connection.execute(
        """
        UPDATE api_keys
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ? AND revoked_at IS NULL
        """,
        (key_id, user_id),
    )
    connection.commit()

    return cursor.rowcount > 0


def keys_for_user(connection, user_id):
    return connection.execute(
        """
        SELECT * FROM api_keys
        WHERE user_id = ?
        ORDER BY revoked_at IS NULL DESC, id DESC
        """,
        (user_id,),
    ).fetchall()


def _prune(connection, cutoff):
    connection.execute(
        "DELETE FROM api_requests WHERE created_at < ?", (cutoff,)
    )


def reserve_quota(connection, ip_address, key_row=None):
    """Count this request against the right bucket.

    Returns ``True`` when the request may proceed.  The bucket is the key when a
    valid key was presented (so several clients behind one NAT do not starve
    each other, and a leaked key cannot spend another caller's allowance), and
    the client IP otherwise.
    """
    cutoff = (
        datetime.now(timezone.utc) - QUOTA_WINDOW
    ).strftime("%Y-%m-%d %H:%M:%S")

    _prune(connection, cutoff)

    if key_row is not None:
        used = connection.execute(
            """
            SELECT COUNT(*) AS count FROM api_requests
            WHERE api_key_id = ? AND created_at >= ?
            """,
            (key_row["id"], cutoff),
        ).fetchone()["count"]
        limit = KEY_QUOTA
    else:
        used = connection.execute(
            """
            SELECT COUNT(*) AS count FROM api_requests
            WHERE ip_address = ? AND api_key_id IS NULL AND created_at >= ?
            """,
            (ip_address, cutoff),
        ).fetchone()["count"]
        limit = ANONYMOUS_QUOTA

    if used >= limit:
        connection.commit()
        return False

    connection.execute(
        "INSERT INTO api_requests(ip_address, api_key_id) VALUES (?, ?)",
        (ip_address, key_row["id"] if key_row is not None else None),
    )
    connection.commit()

    return True
