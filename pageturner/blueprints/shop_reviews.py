"""Customer-facing review actions: helpful votes and reports.

Requirements 50 and 51.  The database holds the invariants (one vote per
user/review, one report per user/review, only visible reviews are actionable);
this module only validates input and reports the outcome.
"""

from flask import abort, flash, redirect, request, url_for

from ..db import get_db
from ..helpers import current_user, login_required
from ..reviews import (
    REPORT_REASONS,
    remove_helpful_vote,
    report_review,
    review_is_visible,
    vote_helpful,
)
from .shop import shop_bp


def _actionable_review(connection, review_id):
    """Return the review row, or abort if it is missing or hidden."""
    review = connection.execute(
        "SELECT * FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()

    if review is None or review_is_visible(connection, review_id) is False:
        abort(404)

    return review


@shop_bp.post("/reviews/<int:review_id>/helpful")
@login_required
def mark_review_helpful(review_id):
    """Idempotently add one helpful vote for the current reader."""
    connection = get_db()
    review = _actionable_review(connection, review_id)
    user = current_user()

    added = vote_helpful(connection, review_id, user["id"])
    connection.commit()

    flash(
        "Thanks - your vote was counted." if added
        else "You already marked this review as helpful.",
        "success",
    )

    return redirect(
        url_for("shop.book_detail", book_id=review["book_id"])
    )


@shop_bp.post("/reviews/<int:review_id>/helpful/remove")
@login_required
def unmark_review_helpful(review_id):
    connection = get_db()
    review = _actionable_review(connection, review_id)
    user = current_user()

    remove_helpful_vote(connection, review_id, user["id"])
    connection.commit()

    flash("Your helpful vote was removed.", "success")

    return redirect(
        url_for("shop.book_detail", book_id=review["book_id"])
    )


@shop_bp.post("/reviews/<int:review_id>/report")
@login_required
def report_review_route(review_id):
    """Send a review to the admin moderation queue."""
    connection = get_db()
    review = _actionable_review(connection, review_id)
    user = current_user()

    if review["user_id"] == user["id"]:
        flash("You cannot report your own review.", "error")
        return redirect(
            url_for("shop.book_detail", book_id=review["book_id"])
        )

    reason = (request.form.get("reason") or "").strip()

    if reason not in REPORT_REASONS:
        flash("Please choose a reason for the report.", "error")
        return redirect(
            url_for("shop.book_detail", book_id=review["book_id"])
        )

    note = (request.form.get("note") or "").strip()[:500]

    created = report_review(
        connection, review_id, user["id"], reason, note
    )
    connection.commit()

    flash(
        "Thanks - a moderator will look at this review." if created
        else "You have already reported this review.",
        "success" if created else "error",
    )

    return redirect(
        url_for("shop.book_detail", book_id=review["book_id"])
    )
