"""Administrator review moderation.

Reviews stay visible to shoppers until a moderator hides them, so existing
reviews and ratings are never silently removed.
"""

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..helpers import audit_action, log_admin_action, role_required
from .admin import admin_bp

REVIEW_STATUSES = ("published", "hidden")


@admin_bp.get("/reviews")
@role_required("admin")
def reviews():
    connection = get_db()
    status = request.args.get("status", "").strip()

    if status not in REVIEW_STATUSES:
        status = ""

    conditions = ""
    params = []

    if status:
        conditions = " WHERE r.status=?"
        params.append(status)

    rows = connection.execute(
        "SELECT r.*, b.title AS book_title "
        "FROM reviews r LEFT JOIN books b ON b.id=r.book_id"
        f"{conditions} ORDER BY r.id DESC LIMIT 200",
        params,
    ).fetchall()

    counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM reviews GROUP BY status"
        ).fetchall()
    }

    return render_template(
        "admin_reviews.html",
        reviews=rows,
        counts=counts,
        status=status,
    )


@admin_bp.post("/reviews/<int:review_id>/status")
@role_required("admin")
@audit_action("review_moderated")
def set_review_status(review_id):
    connection = get_db()
    status = request.form.get("status", "").strip()

    if status not in REVIEW_STATUSES:
        flash("Unknown review status.", "error")
        return redirect(url_for("admin.reviews"))

    connection.execute(
        "UPDATE reviews SET status=? WHERE id=?",
        (status, review_id),
    )

    connection.commit()
    log_admin_action(
        "review_moderated",
        f"review {review_id} set to {status}",
    )

    flash("Review updated.", "success")
    return redirect(request.referrer or url_for("admin.reviews"))


@admin_bp.post("/reviews/<int:review_id>/delete")
@role_required("admin")
@audit_action("review_deleted")
def delete_review(review_id):
    connection = get_db()
    connection.execute("DELETE FROM reviews WHERE id=?", (review_id,))
    connection.commit()
    flash("Review deleted.", "success")
    return redirect(request.referrer or url_for("admin.reviews"))
