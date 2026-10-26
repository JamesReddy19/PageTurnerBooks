
"""Admin management for timed book sales."""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from ..helpers import rupees_to_paise, log_admin_action
from .admin import admin_bp


@admin_bp.route("/sales", methods=["GET", "POST"])
@permission_required("pricing.manage")
def manage_sales():
    connection = get_db()

    if request.method == "POST":
        try:
            book_id = int(request.form.get("book_id", ""))
        except (ValueError, TypeError):
            abort(400)

        book = connection.execute(
            "SELECT id, title, price_paise FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()

        if not book:
            abort(404)

        sale_price_text = request.form.get("sale_price", "").strip()
        start_text = request.form.get("sale_start", "").strip()
        end_text = request.form.get("sale_end", "").strip()

        # Empty sale price and times mean the sale should be removed.
        if not sale_price_text and not start_text and not end_text:
            connection.execute(
                """
                UPDATE books
                SET sale_price_paise = NULL,
                    sale_start = NULL,
                    sale_end = NULL
                WHERE id = ?
                """,
                (book_id,),
            )
            log_admin_action("book_sale_removed", f"Book {book_id}")
            connection.commit()
            flash("Flash sale removed.", "success")
            return redirect(url_for("admin.manage_sales"))

        try:
            sale_price = rupees_to_paise(sale_price_text)
            start = datetime.fromisoformat(start_text)
            end = datetime.fromisoformat(end_text)
        except (ValueError, TypeError):
            flash("Enter a valid sale price, start time and end time.", "error")
            return redirect(url_for("admin.manage_sales"))

        if sale_price >= book["price_paise"]:
            flash("The sale price must be lower than the normal price.", "error")
            return redirect(url_for("admin.manage_sales"))

        if start >= end:
            flash("The sale end time must be later than the start time.", "error")
            return redirect(url_for("admin.manage_sales"))

        connection.execute(
            """
            UPDATE books
            SET sale_price_paise = ?,
                sale_start = ?,
                sale_end = ?
            WHERE id = ?
            """,
            (
                sale_price,
                start.isoformat(timespec="seconds"),
                end.isoformat(timespec="seconds"),
                book_id,
            ),
        )

        log_admin_action(
            "book_sale_updated",
            f"Book {book_id}: sale price {sale_price} paise",
        )
        connection.commit()
        flash("Flash sale saved.", "success")
        return redirect(url_for("admin.manage_sales"))

    books = connection.execute(
        "SELECT * FROM books ORDER BY title COLLATE NOCASE"
    ).fetchall()

    return render_template("manage_sales.html", books=books)
