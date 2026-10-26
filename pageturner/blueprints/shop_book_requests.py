"""Customer book requests (requirement 52).

Only the shop blueprint is touched here; the shared counter lives in
``pageturner.book_requests`` so the admin view does not force an import cycle
between the shop and admin blueprints.
"""

from flask import flash, redirect, render_template, request, url_for

from ..book_requests import normalize_title, record_book_request
from ..db import get_db
from ..helpers import current_user, login_required
from .shop import shop_bp


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

    if len(normalize_title(title)) < 2:
        flash("Enter the title you would like us to stock.", "error")
        return redirect(url_for("shop.request_book_form"))

    count, created = record_book_request(connection, title, author, user["id"])
    connection.commit()

    display_title = title[:200]

    if created:
        flash(f"Thanks - “{display_title}” is now on our request list.", "success")
    else:
        flash(f"Noted. “{display_title}” now has {count} requests.", "success")

    return redirect(url_for("shop.request_book_form"))
