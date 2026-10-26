
"""Admin management for combo bundles."""

import sqlite3

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..db import get_db
from ..helpers import role_required, rupees_to_paise, log_admin_action
from .admin import admin_bp


@admin_bp.route("/bundles", methods=["GET", "POST"])
@role_required("admin")
def manage_bundles():
    connection = get_db()

    if request.method == "POST":
        action = request.form.get("action", "create")

        # Activate or deactivate an existing bundle.
        if action == "toggle":
            try:
                bundle_id = int(request.form.get("bundle_id", ""))
            except (ValueError, TypeError):
                abort(400)

            try:
                connection.execute("BEGIN IMMEDIATE")

                bundle = connection.execute(
                    "SELECT id, active FROM bundles WHERE id = ?",
                    (bundle_id,),
                ).fetchone()

                if not bundle:
                    connection.rollback()
                    abort(404)

                new_status = 0 if bundle["active"] else 1

                connection.execute(
                    "UPDATE bundles SET active = ? WHERE id = ?",
                    (new_status, bundle_id),
                )

                log_admin_action(
                    "bundle_status_changed",
                    f"Bundle {bundle_id}: active={new_status}",
                )

                connection.commit()
                flash("Bundle status updated.", "success")

            except Exception:
                connection.rollback()
                raise

            return redirect(url_for("admin.manage_bundles"))

        # Read the new bundle details.
        name = request.form.get("name", "").strip()[:150]
        description = request.form.get("description", "").strip()[:1000]

        try:
            price_paise = rupees_to_paise(
                request.form.get("price", "")
            )

            book_ids = [
                int(value)
                for value in request.form.getlist("book_ids")
            ]

            # Each selected book has its own quantity field.
            quantities = [
                int(request.form.get(f"quantity_{book_id}", "1"))
                for book_id in book_ids
            ]

        except (ValueError, TypeError):
            flash(
                "Enter a valid price, book selection and quantity.",
                "error",
            )
            return redirect(url_for("admin.manage_bundles"))

        if not name:
            flash("Enter a bundle name.", "error")
            return redirect(url_for("admin.manage_bundles"))

        if price_paise < 0:
            flash("Bundle price cannot be negative.", "error")
            return redirect(url_for("admin.manage_bundles"))

        if len(book_ids) < 2:
            flash(
                "Choose at least two different books for a bundle.",
                "error",
            )
            return redirect(url_for("admin.manage_bundles"))

        if len(set(book_ids)) != len(book_ids):
            flash("Do not select the same book more than once.", "error")
            return redirect(url_for("admin.manage_bundles"))

        if any(book_id < 1 for book_id in book_ids):
            flash("Invalid book selection.", "error")
            return redirect(url_for("admin.manage_bundles"))

        if any(quantity < 1 or quantity > 99 for quantity in quantities):
            flash(
                "Each selected book must have a quantity from 1 to 99.",
                "error",
            )
            return redirect(url_for("admin.manage_bundles"))

        # Create the bundle and its items as one transaction.
        try:
            connection.execute("BEGIN IMMEDIATE")

            placeholders = ",".join("?" for _ in book_ids)

            found_books = connection.execute(
                f"SELECT id FROM books WHERE id IN ({placeholders})",
                book_ids,
            ).fetchall()

            if len(found_books) != len(book_ids):
                raise ValueError(
                    "One or more selected books no longer exist."
                )

            cursor = connection.execute(
                """
                INSERT INTO bundles (
                    name,
                    description,
                    price_paise,
                    active
                )
                VALUES (?, ?, ?, 1)
                """,
                (name, description, price_paise),
            )

            bundle_id = cursor.lastrowid

            connection.executemany(
                """
                INSERT INTO bundle_items (
                    bundle_id,
                    book_id,
                    quantity
                )
                VALUES (?, ?, ?)
                """,
                [
                    (bundle_id, book_id, quantity)
                    for book_id, quantity in zip(book_ids, quantities)
                ],
            )

            log_admin_action(
                "bundle_created",
                f"Bundle {bundle_id}: {name}",
            )

            connection.commit()
            flash("Combo bundle created successfully.", "success")

        except sqlite3.IntegrityError:
            connection.rollback()
            flash(
                "A bundle with that name already exists, or its items are invalid.",
                "error",
            )

        except ValueError as error:
            connection.rollback()
            flash(str(error), "error")

        except Exception:
            connection.rollback()
            raise

        return redirect(url_for("admin.manage_bundles"))

    # Load books for the creation form.
    books = connection.execute(
        """
        SELECT id, title, author, stock
        FROM books
        ORDER BY title
        """
    ).fetchall()

    # Load existing bundles and their included books.
    bundles = connection.execute(
        """
        SELECT
            b.*,
            GROUP_CONCAT(
                books.title || ' × ' || bi.quantity,
                ', '
            ) AS items_text
        FROM bundles b
        LEFT JOIN bundle_items bi
            ON bi.bundle_id = b.id
        LEFT JOIN books
            ON books.id = bi.book_id
        GROUP BY b.id
        ORDER BY b.id DESC
        """
    ).fetchall()

    return render_template(
        "manage_bundles.html",
        books=books,
        bundles=bundles,
    )
