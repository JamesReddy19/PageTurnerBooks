"""Book request grouping primitives (requirement 52).

Kept free of Flask blueprint imports so both the customer routes and the admin
routes can share it without creating an import cycle between ``shop`` and
``admin``.
"""

import re

_TITLE_NOISE = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_title(title):
    """Collapse case, punctuation and spacing so near-duplicates group."""
    cleaned = _TITLE_NOISE.sub(" ", (title or "").lower())
    return _SPACES.sub(" ", cleaned).strip()


def record_book_request(connection, title, author, user_id=None):
    """Increment the counter for this title, creating the row when new.

    Returns ``(request_count, created)``.  The increment happens in a single
    UPSERT so two simultaneous requests can never both read the old count.
    """
    normalized = normalize_title(title)

    if not normalized:
        return 0, False

    connection.execute(
        """
        INSERT INTO book_requests
            (normalized_title, title, author, request_count, first_user_id)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(normalized_title) DO UPDATE SET
            request_count = request_count + 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (normalized, title.strip()[:200], (author or "").strip()[:120], user_id),
    )

    row = connection.execute(
        "SELECT request_count FROM book_requests WHERE normalized_title = ?",
        (normalized,),
    ).fetchone()

    return row["request_count"], row["request_count"] == 1
