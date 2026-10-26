"""Admin side of requirements 63 and 65 — offers and the backorder queue.

Both pages exist so that the *policy* lives in the database and the shop code
only reads it.  Adding a promotion or shipping a backorder never needs a deploy.
"""

from flask import flash, redirect, render_template, request, url_for

from .. import backorders, pricing_rules
from ..db import get_db
from ..helpers import add_notification, log_admin_action
from .admin import admin_bp

RULE_TYPES = ("category_percent", "book_percent", "min_quantity_percent")


@admin_bp.get("/pricing-rules")
@permission_required("pricing.rules.manage")
def pricing_rules_page():
    connection = get_db()

    return render_template(
        "admin_pricing_rules.html",
        rules=connection.execute(
            "SELECT * FROM pricing_rules ORDER BY active DESC, id DESC"
        ).fetchall(),
        categories=[
            row["category"] for row in connection.execute(
                """
                SELECT DISTINCT category FROM books
                WHERE category <> '' ORDER BY category
                """
            ).fetchall()
        ],
        books=connection.execute(
            "SELECT id, title FROM books ORDER BY title LIMIT 500"
        ).fetchall(),
        rule_types=RULE_TYPES,
    )


@admin_bp.post("/pricing-rules")
@permission_required("pricing.rules.manage")
def create_pricing_rule():
    connection = get_db()

    name = (request.form.get("name") or "").strip()
    rule_type = (request.form.get("rule_type") or "").strip()
    category = (request.form.get("category") or "").strip()
    book_id = (request.form.get("book_id") or "").strip()
    min_quantity = (request.form.get("min_quantity") or "1").strip()
    percent_off = (request.form.get("percent_off") or "").strip()

    try:
        if not name:
            raise ValueError("Give the rule a name.")

        if rule_type not in RULE_TYPES:
            raise ValueError("Choose a rule type.")

        percent = int(percent_off)

        if not 1 <= percent <= 90:
            raise ValueError("The discount must be between 1% and 90%.")

        quantity = int(min_quantity or 1)

        if quantity < 1:
            raise ValueError("The minimum quantity must be at least 1.")

        if rule_type == "category_percent" and not category:
            raise ValueError("Choose the category this rule applies to.")

        if rule_type == "book_percent":
            if not book_id:
                raise ValueError("Choose the book this rule applies to.")

            book_id = int(book_id)
        else:
            book_id = None

        connection.execute(
            """
            INSERT INTO pricing_rules(
                name, rule_type, category, book_id, min_quantity, percent_off
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name, rule_type,
                category if rule_type == "category_percent" else None,
                book_id, quantity, percent,
            ),
        )
        connection.commit()

    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
        return redirect(url_for("admin.pricing_rules_page"))
    except Exception:
        connection.rollback()
        flash("A rule with that name already exists.", "error")
        return redirect(url_for("admin.pricing_rules_page"))

    log_admin_action("pricing_rule.create", f"{name} ({percent}%)")
    flash(f"Rule “{name}” is live.", "success")

    return redirect(url_for("admin.pricing_rules_page"))


@admin_bp.post("/pricing-rules/<int:rule_id>/toggle")
@permission_required("pricing.rules.manage")
def toggle_pricing_rule(rule_id):
    connection = get_db()

    row = connection.execute(
        "SELECT name, active FROM pricing_rules WHERE id = ?", (rule_id,)
    ).fetchone()

    if row is None:
        flash("That rule no longer exists.", "error")
        return redirect(url_for("admin.pricing_rules_page"))

    connection.execute(
        "UPDATE pricing_rules SET active = ? WHERE id = ?",
        (0 if row["active"] else 1, rule_id),
    )
    connection.commit()

    log_admin_action(
        "pricing_rule.toggle", f"{row['name']} -> {'off' if row['active'] else 'on'}"
    )
    flash(f"Rule “{row['name']}” switched {'off' if row['active'] else 'on'}.",
          "success")

    return redirect(url_for("admin.pricing_rules_page"))


@admin_bp.get("/backorders")
@permission_required("stock.manage")
def backorders_page():
    connection = get_db()

    rows = backorders.open_backorders(connection)

    return render_template(
        "admin_backorders.html",
        backorders=rows,
        pending_total=sum(
            row["quantity"] - row["fulfilled_quantity"] for row in rows
        ),
    )


@admin_bp.post("/backorders/allocate/<int:book_id>")
@permission_required("stock.manage")
def allocate_backorders(book_id):
    """Hand newly arrived stock to the oldest waiting orders, first come first served."""
    connection = get_db()

    try:
        connection.execute("BEGIN IMMEDIATE")

        allocated = backorders.allocate_backorders(
            connection, book_id, notify=add_notification
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    if allocated:
        log_admin_action("backorder.allocate", f"book {book_id}: {allocated}")
        flash(f"{allocated} backordered copies allocated, oldest first.",
              "success")
    else:
        flash("No stock is available to allocate for that title.", "error")

    return redirect(url_for("admin.backorders_page"))


@admin_bp.get("/pricing-rules/preview")
@permission_required("pricing.rules.manage")
def pricing_preview():
    """Explain what the current rules would do to a basket, for staff sanity."""
    connection = get_db()

    books = connection.execute(
        "SELECT * FROM books ORDER BY id LIMIT 40"
    ).fetchall()

    lines = [
        {
            "book_id": book["id"],
            "quantity": 1,
            "line_total": book["price_paise"],
        }
        for book in books
    ]

    total, per_book = pricing_rules.evaluate(connection, lines)

    return render_template(
        "admin_pricing_preview.html",
        books=books,
        per_book=per_book,
        total=total,
        rule_count=len(pricing_rules.active_rules(connection)),
    )
