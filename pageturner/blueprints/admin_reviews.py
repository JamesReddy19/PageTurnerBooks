"""Admin review moderation queue (requirement 51).

Customer reports collect here.  Hiding a review removes it from the storefront
*and* from the rating average (the exclusion lives in ``pageturner.reviews``),
so a single decision updates every rating number on the site.
"""

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..helpers import current_user, log_admin_action, permission_required
from ..reviews import REPORT_REASONS, hide_review, keep_review
from .admin import admin_bp

STATUS_FILTERS = ("open", "hidden", "kept", "all")


@admin_bp.get("/reviews")
@permission_required("reviews.moderate")
def review_moderation():
    connection = get_db()
    status = (request.args.get("status") or "open").strip()

    if status not in STATUS_FILTERS:
        status = "open"

    where = ""

    if status == "open":
        where = (
            "WHERE r.hidden = 0 AND EXISTS ("
            "SELECT 1 FROM review_reports rr "
            "WHERE rr.review_id = r.id AND rr.status = 'open')"
        )
    elif status == "hidden":
        where = "WHERE r.hidden = 1"
    elif status == "kept":
        where = (
            "WHERE r.hidden = 0 AND EXISTS ("
            "SELECT 1 FROM review_reports rr "
            "WHERE rr.review_id = r.id AND rr.status = 'kept')"
        )

    reviews = connection.execute(
        f"""
        SELECT r.*, b.title AS book_title, b.id AS book_id,
               (SELECT COUNT(*) FROM review_reports rr
                 WHERE rr.review_id = r.id AND rr.status = 'open')
                   AS open_reports,
               (SELECT COUNT(*) FROM review_reports rr
                 WHERE rr.review_id = r.id) AS total_reports,
               (SELECT COUNT(*) FROM review_votes rv
                 WHERE rv.review_id = r.id) AS helpful_votes
        FROM reviews r
        JOIN books b ON b.id = r.book_id
        {where}
        ORDER BY open_reports DESC, r.id DESC
        LIMIT 200
        """
    ).fetchall()

    reasons = {}
    if reviews:
        placeholders = ",".join("?" for _ in reviews)
        for row in connection.execute(
            f"""
            SELECT rr.review_id, rr.reason, rr.note, rr.status, rr.created_at,
                   u.name AS reporter
            FROM review_reports rr
            LEFT JOIN users u ON u.id = rr.reporter_user_id
            WHERE rr.review_id IN ({placeholders})
            ORDER BY rr.id DESC
            """,
            [review["id"] for review in reviews],
        ).fetchall():
            reasons.setdefault(row["review_id"], []).append(row)

    return render_template(
        "admin_reviews.html",
        reviews=reviews,
        reports_by_review=reasons,
        status=status,
        reason_labels=REPORT_REASONS,
    )


@admin_bp.post("/reviews/<int:review_id>/hide")
@permission_required("reviews.moderate")
def hide_review_route(review_id):
    connection = get_db()

    review = connection.execute(
        "SELECT id, book_id FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()

    if review is None:
        flash("That review no longer exists.", "error")
        return redirect(url_for("admin.review_moderation"))

    hide_review(connection, review_id, current_user()["id"])
    connection.commit()

    log_admin_action(
        "review.hide",
        f"review {review_id} on book {review['book_id']}",
    )

    flash("Review hidden. It no longer counts towards the book's rating.", "success")

    return redirect(url_for("admin.review_moderation", status=request.args.get("status", "open")))


@admin_bp.post("/reviews/<int:review_id>/keep")
@permission_required("reviews.moderate")
def keep_review_route(review_id):
    connection = get_db()

    review = connection.execute(
        "SELECT id, book_id FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()

    if review is None:
        flash("That review no longer exists.", "error")
        return redirect(url_for("admin.review_moderation"))

    keep_review(connection, review_id, current_user()["id"])
    connection.commit()

    log_admin_action(
        "review.keep",
        f"review {review_id} on book {review['book_id']}",
    )

    flash("Review kept and public. Its reports were dismissed.", "success")

    return redirect(url_for("admin.review_moderation", status=request.args.get("status", "open")))
