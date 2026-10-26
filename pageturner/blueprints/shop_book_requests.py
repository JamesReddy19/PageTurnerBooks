"""Customer book requests and the admin wishlist (requirement 52).

One row per normalized title.  Asking for a title that already has a row
increments ``request_count`` instead of creating a duplicate, so the admin list
sorted by count is a real demand ranking.  The increment is a single SQL
statement, which keeps concurrent requests from losing a vote.
"""

import re

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..helpers import current_user, log_admin_action, login_required, role_required
from .shop import shop_bp
from .admin import admin_bp

_TITLE_NOISE = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_title(title):
    """Collapse case, punctuation and spacing so near-duplicates group."""
    cleaned = _TITLE_NOISE.sub(" ", (title or "").lower())
    return _SPACES.sub(" ", cleaned).strip()


def record_book_request(connection, title, author, user_id=None):
    """Increment the counter for this title, creating the row when new.

    Returns (request_count, created).
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


@shop_bp.get("/request-book")
@login_required
def request_book_form():
    return render_template("request_book.html")


@shop_bp.post("/request-book")
@login_required
def request_book():
    connection = get_db()
    user = current_user()

    title = (request.form.get("title") or "").strip()
    author = (request.form.get("author") or "").strip()

    if len(title) < 2:
        flash("Enter the title you would like us to stock.", "error")
        return redirect(url_for("shop.request_book_form"))

    count, created = record_book_request(
        connection, title, author, user["id"]
    )
    connection.commit()

    if created:
        flash(
            f"Thanks - “{title.strip()[:200]}” is now on our request list.",
            "success",
        )
    else:
        flash(
            f"Noted. “{title.strip()[:200]}” now has {count} requests.",
            "success",
        )

    return redirect(url_for("shop.request_book_form"))


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
    """Remove a request row once the book has been stocked."""
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
