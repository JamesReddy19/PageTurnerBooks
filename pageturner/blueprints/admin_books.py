"""Administrator-only book creation, editing and deletion."""

import sqlite3
from datetime import date

from flask import abort, flash, jsonify, redirect, request, session, url_for

from ..db import get_db
from ..helpers import role_required, log_admin_action, add_notification, audit_action
from ..pricing import apply_percentage, set_book_price
from .admin import admin_bp, read_book_form, save_uploaded_image, save_categories_and_tags


class BookEditConflict(Exception):
    """Raised when a stale edit form loses the optimistic-locking check."""


def read_book_version():
    """Read the optimistic-locking version submitted with an edit form.

    The version travels in a hidden field, so a missing or unparsable value
    means the form was not produced by a current page.  Treat that as a
    conflict rather than as "no check needed": silently overwriting a stale
    edit is exactly what requirement 66 forbids.
    """
    raw = request.form.get("version", "").strip()

    try:
        version = int(raw)
    except ValueError:
        raise ValueError(
            "This page is out of date. Reload the book list and edit again."
        )

    if version < 1:
        raise ValueError(
            "This page is out of date. Reload the book list and edit again."
        )

    return version


def read_release_date():
    """Read and validate the optional book release date."""
    release_date = request.form.get("release_date", "").strip()

    # An empty date means the book is already available.
    if not release_date:
        return None

    try:
        # The HTML date input normally sends YYYY-MM-DD.
        return date.fromisoformat(release_date).isoformat()
    except ValueError:
        raise ValueError("Enter a valid release date.")


@admin_bp.post("/books/add")
@role_required("admin")
@audit_action("book_added")
def add_book():
    connection = get_db()

    try:
        title, author, category, price, description, stock, color = read_book_form()
        release_date = read_release_date()
        image = save_uploaded_image()

        connection.execute("BEGIN IMMEDIATE")

        cursor = connection.execute(
            """
            INSERT INTO books (
                title, author, category, price_paise, description,
                stock, color, image, release_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, author, category, price, description,
                stock, color, image, release_date
            )
        )

        book_id = cursor.lastrowid

        # Record the creation price in the price history (req 54): the book now
        # has a price, so set_book_price's baseline path logs old_price = NULL.
        set_book_price(
            connection, book_id, price, session.get("user_id"),
            reason="book created",
        )

        save_categories_and_tags(
            book_id, category, request.form.get("tags", "")
        )

        if stock:
            connection.execute(
                """
                INSERT INTO stock_log (
                    book_id, change_amount, reason, actor_user_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (book_id, stock, "initial stock", session.get("user_id"))
            )

        log_admin_action("book_added", title)
        connection.commit()
        flash("Book added.", "success")

    except (ValueError, sqlite3.IntegrityError) as error:
        connection.rollback()

        if isinstance(error, ValueError):
            flash(str(error), "error")
        else:
            flash(
                "A book with that title and author already exists.",
                "error"
            )

    return redirect(url_for("admin.manage_books"))


@admin_bp.post("/books/edit/<int:book_id>")
@role_required("admin")
@audit_action("book_edited")
def edit_book(book_id):
    connection = get_db()
    old = connection.execute(
        "SELECT * FROM books WHERE id = ?", (book_id,)
    ).fetchone()

    if not old:
        abort(404)

    try:
        title, author, category, price, description, stock, color = read_book_form()
        release_date = read_release_date()
        version = read_book_version()
        image = save_uploaded_image() or old["image"]

        connection.execute("BEGIN IMMEDIATE")

        stock_change = stock - old["stock"]

        # Optimistic locking (req 66): only apply the edit if nobody else has
        # saved this book since the form was rendered.  The version is bumped
        # in the same statement, so two concurrent editors cannot both win.
        updated = connection.execute(
            """
            UPDATE books
            SET title = ?, author = ?, category = ?,
                description = ?, stock = ?, color = ?, image = ?,
                release_date = ?, version = version + 1
            WHERE id = ? AND version = ?
            """,
            (
                title, author, category, description,
                stock, color, image, release_date, book_id, version
            )
        )

        if updated.rowcount == 0:
            raise BookEditConflict(
                "%s was changed by someone else after you opened this form. "
                "Your edits were not saved; reload the page to see the "
                "current values." % (old["title"],)
            )

        # Price writes go through the single choke point that records history
        # (req 54) and applies the optimistic lock's new row state.
        set_book_price(
            connection, book_id, price, session.get("user_id"),
            reason="manual edit",
        )

        save_categories_and_tags(
            book_id, category, request.form.get("tags", "")
        )

        if stock_change:
            connection.execute(
                """
                INSERT INTO stock_log (
                    book_id, change_amount, reason, actor_user_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    book_id, stock_change,
                    "admin stock edit", session.get("user_id")
                )
            )

            if stock_change > 0:
                wishes = connection.execute(
                    "SELECT user_id FROM wishlist WHERE book_id = ?",
                    (book_id,)
                ).fetchall()

                for wish in wishes:
                    add_notification(
                        wish["user_id"],
                        f"{title} is back in stock.",
                        url_for("shop.book_detail", book_id=book_id)
                    )

        log_admin_action("book_edited", title)
        connection.commit()
        flash("Book updated.", "success")

    except BookEditConflict as error:
        connection.rollback()
        flash(str(error), "error")

    except (ValueError, sqlite3.IntegrityError) as error:
        connection.rollback()

        if isinstance(error, ValueError):
            flash(str(error), "error")
        else:
            flash(
                "Could not update this book. Check for a duplicate "
                "title and author.",
                "error"
            )

    return redirect(url_for("admin.manage_books"))


@admin_bp.post("/books/prices/bulk")
@role_required("admin")
@audit_action("book_prices_bulk")
def bulk_update_prices():
    """Apply one percentage price change to several books at once (req 55).

    Every adjusted price still travels through set_book_price, so bulk edits
    are recorded in the same immutable history as single edits.
    """
    connection = get_db()

    book_ids = []
    for raw in request.form.getlist("book_id"):
        try:
            book_ids.append(int(raw))
        except ValueError:
            continue

    if not book_ids:
        flash("Select at least one book to reprice.", "error")
        return redirect(url_for("admin.manage_books"))

    try:
        percent = int(request.form.get("percent", "").strip())
    except ValueError:
        flash("Enter a whole-number percentage between -90 and 500.", "error")
        return redirect(url_for("admin.manage_books"))

    if percent < -90 or percent > 500:
        flash("Enter a whole-number percentage between -90 and 500.", "error")
        return redirect(url_for("admin.manage_books"))

    mode = "increase" if percent >= 0 else "discount"

    try:
        connection.execute("BEGIN IMMEDIATE")
        changed = 0

        for book_id in book_ids:
            row = connection.execute(
                "SELECT price_paise FROM books WHERE id = ?", (book_id,)
            ).fetchone()

            if not row:
                continue

            new_price = apply_percentage(
                row["price_paise"], abs(percent), mode
            )

            if new_price != row["price_paise"]:
                set_book_price(
                    connection, book_id, new_price, session.get("user_id"),
                    reason="bulk %+d%% price change" % percent,
                )
                changed += 1

        log_admin_action(
            "book_prices_bulk", "%+d%% on %d book(s)" % (percent, changed)
        )
        connection.commit()

    except (ValueError, sqlite3.IntegrityError) as error:
        connection.rollback()
        flash(str(error) or "Could not update those prices.", "error")
        return redirect(url_for("admin.manage_books"))

    if changed:
        flash(
            "Updated %d price(s) by %+d%%." % (changed, percent),
            "success"
        )
    else:
        flash("Those prices already had that value; nothing changed.", "error")

    return redirect(url_for("admin.manage_books"))


@admin_bp.get("/books/<int:book_id>/price-history")
@role_required("admin")
def book_price_history(book_id):
    """JSON price history for one book, newest first (req 54)."""
    book = get_db().execute(
        "SELECT id, title, price_paise FROM books WHERE id = ?", (book_id,)
    ).fetchone()

    if not book:
        abort(404)

    rows = get_db().execute(
        """
        SELECT old_price_paise, new_price_paise, reason, changed_at,
               actor_user_id
        FROM book_price_history
        WHERE book_id = ?
        ORDER BY id DESC
        """,
        (book_id,)
    ).fetchall()

    return jsonify(
        book_id=book["id"],
        title=book["title"],
        current_price_paise=book["price_paise"],
        history=[dict(row) for row in rows],
    )


@admin_bp.post("/books/delete/<int:book_id>")
@role_required("admin")
@audit_action("book_deleted")
def delete_book(book_id):
    connection = get_db()

    try:
        connection.execute(
            "DELETE FROM books WHERE id = ?", (book_id,)
        )
        log_admin_action("book_deleted", book_id)
        connection.commit()
        flash("Book deleted if it was not used in an order.", "success")

    except sqlite3.IntegrityError:
        connection.rollback()
        flash(
            "This book is part of an order and cannot be deleted. "
            "Set its stock to zero instead.",
            "error"
        )

    return redirect(url_for("admin.manage_books"))


# Compatibility URLs retained from the first project version.
@admin_bp.post("/add")
@role_required("admin")
def old_add_book_url():
    return redirect(url_for("admin.add_book"), code=307)


@admin_bp.post("/edit/<int:book_id>")
@role_required("admin")
def old_edit_book_url(book_id):
    return redirect(
        url_for("admin.edit_book", book_id=book_id),
        code=307
    )


@admin_bp.post("/delete/<int:book_id>")
@role_required("admin")
def old_delete_book_url(book_id):
    return redirect(
        url_for("admin.delete_book", book_id=book_id),
        code=307
    )
