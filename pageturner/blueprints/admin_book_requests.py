"""Admin demand view for requested titles (requirement 52).

Sorted by request count, so the most-wanted books are literally the top rows.
"""

from flask import flash, redirect, render_template, url_for

from ..db import get_db
from ..helpers import log_admin_action, role_required
from .admin import admin_bp


@admin_bp.get("/book-requests")
@role_required("staff")
def book_requests():
    connection = get_db()

    requests = connection.execute(
        """
        SELECT br.*, u.name AS first_requester
        FROM book_requests br
        LEFT JOIN users u ON u.id = br.first_user_id
        ORDER BY br.request_count DESC, br.updated_at DESC
        LIMIT 300
        """
    ).fetchall()

    total = sum(row["request_count"] for row in requests)

    return render_template(
        "admin_book_requests.html",
        requests=requests,
        total_requests=total,
    )


@admin_bp.post("/book-requests/<int:request_id>/fulfil")
@role_required("staff")
def fulfil_book_request(request_id):
    """Drop a request row once the title has actually been stocked."""
    connection = get_db()

    row = connection.execute(
        "SELECT id, title FROM book_requests WHERE id = ?", (request_id,)
    ).fetchone()

    if row is None:
        flash("That request no longer exists.", "error")
        return redirect(url_for("admin.book_requests"))

    connection.execute("DELETE FROM book_requests WHERE id = ?", (request_id,))
    connection.commit()

    log_admin_action("book_request.fulfil", row["title"])

    flash(f"“{row['title']}” removed from the request list.", "success")

    return redirect(url_for("admin.book_requests"))
