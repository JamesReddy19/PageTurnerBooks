"""Review visibility, helpful votes and moderation.

Requirements 50 and 51.

The rating average used to be computed by seven separate hand-written SQL
fragments.  Requirement 51 says a hidden review must influence *neither* the
average rating nor the review count, so the visibility filter now lives here
and every caller interpolates these constants.  Adding a new rating query
without the filter is the one mistake this module exists to prevent.
"""

#: Rating average, already filtered to visible reviews. Callers alias their
#: books table as ``b``.
AVERAGE_RATING_SQL = (
    "(SELECT AVG(r.rating) FROM reviews r "
    "WHERE r.book_id = b.id AND r.hidden = 0)"
)

#: Number of visible reviews, so a hidden review is not counted either.
REVIEW_COUNT_SQL = (
    "(SELECT COUNT(*) FROM reviews r "
    "WHERE r.book_id = b.id AND r.hidden = 0)"
)

#: Reports must use one of these reasons; free text goes in ``note``.
REPORT_REASONS = {
    "spam": "Spam or advertising",
    "abuse": "Abusive or hateful language",
    "spoiler": "Spoilers without warning",
    "irrelevant": "Not about this book",
}


def review_is_visible(connection, review_id):
    row = connection.execute(
        "SELECT hidden FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()

    if row is None:
        return None

    return not row["hidden"]


def vote_helpful(connection, review_id, user_id):
    """Record a helpful vote. Returns True if this call added the vote.

    One vote per (review, user) is guaranteed by the UNIQUE constraint on
    review_votes, so a double submit is a no-op rather than a second vote.
    """
    cursor = connection.execute(
        "INSERT OR IGNORE INTO review_votes(review_id, user_id) VALUES (?, ?)",
        (review_id, user_id),
    )

    return cursor.rowcount > 0


def remove_helpful_vote(connection, review_id, user_id):
    """Withdraw a vote. Returns True if a vote was actually removed."""
    cursor = connection.execute(
        "DELETE FROM review_votes WHERE review_id = ? AND user_id = ?",
        (review_id, user_id),
    )

    return cursor.rowcount > 0


def helpful_vote_counts(connection, review_ids, viewer_id=None):
    """Return {review_id: {"count": n, "voted": bool}} for the given reviews."""
    review_ids = [int(review_id) for review_id in review_ids]

    result = {
        review_id: {"count": 0, "voted": False}
        for review_id in review_ids
    }

    if not review_ids:
        return result

    placeholders = ",".join("?" for _ in review_ids)

    rows = connection.execute(
        f"""
        SELECT review_id, COUNT(*) AS vote_count,
               MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS voted
        FROM review_votes
        WHERE review_id IN ({placeholders})
        GROUP BY review_id
        """,
        [viewer_id if viewer_id is not None else -1] + review_ids,
    ).fetchall()

    for row in rows:
        result[row["review_id"]] = {
            "count": row["vote_count"],
            "voted": bool(row["voted"]),
        }

    return result


def report_review(connection, review_id, reporter_user_id, reason, note=""):
    """File one report per reviewer per review.

    Returns True when a new report is stored and False when this reviewer has
    already reported the review (the UNIQUE constraint decides, not the app).
    """
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO review_reports
            (review_id, reporter_user_id, reason, note)
        VALUES (?, ?, ?, ?)
        """,
        (review_id, reporter_user_id, reason, note),
    )

    return cursor.rowcount > 0


def open_report_count(connection, review_id):
    return connection.execute(
        """
        SELECT COUNT(*) FROM review_reports
        WHERE review_id = ? AND status = 'open'
        """,
        (review_id,),
    ).fetchone()[0]


def hide_review(connection, review_id, actor_user_id):
    """Take a review out of the public rating and resolve its open reports."""
    connection.execute(
        """
        UPDATE reviews
        SET hidden = 1, hidden_at = CURRENT_TIMESTAMP, hidden_by = ?
        WHERE id = ?
        """,
        (actor_user_id, review_id),
    )

    connection.execute(
        """
        UPDATE review_reports
        SET status = 'hidden', resolved_at = CURRENT_TIMESTAMP,
            resolved_by = ?
        WHERE review_id = ? AND status = 'open'
        """,
        (actor_user_id, review_id),
    )


def keep_review(connection, review_id, actor_user_id):
    """Make a review public again and record that the reports were rejected."""
    connection.execute(
        """
        UPDATE reviews
        SET hidden = 0, hidden_at = NULL, hidden_by = NULL
        WHERE id = ?
        """,
        (review_id,),
    )

    connection.execute(
        """
        UPDATE review_reports
        SET status = 'kept', resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
        WHERE review_id = ? AND status = 'open'
        """,
        (actor_user_id, review_id),
    )
