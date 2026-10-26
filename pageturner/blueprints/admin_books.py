"""Administrator-only book creation, editing and deletion."""

import sqlite3
from datetime import date

from flask import abort, flash, redirect, request, session, url_for

from ..db import get_db
from ..helpers import role_required, log_admin_action, add_notification, audit_action
from .admin import admin_bp, read_book_form, save_uploaded_image, save_categories_and_tags


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
        image = save_uploaded_image() or old["image"]

        connection.execute("BEGIN IMMEDIATE")

        stock_change = stock - old["stock"]

        connection.execute(
            """
            UPDATE books
            SET title = ?, author = ?, category = ?, price_paise = ?,
                description = ?, stock = ?, color = ?, image = ?,
                release_date = ?
            WHERE id = ?
            """,
            (
                title, author, category, price, description,
                stock, color, image, release_date, book_id
            )
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
