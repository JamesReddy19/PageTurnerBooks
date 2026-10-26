"""Requirement 62 — change an order while it is still only Placed.

Before the warehouse packs an order the customer may want one more copy, one
fewer, or an item removed.  This module owns that edit and the re-pricing that
must follow it: the subtotal, the coupon, the pricing-rule discount, the shipping
threshold, the tax and the total are all recomputed from the lines that survive,
so the stored total always equals the sum of its parts.

Two invariants:

* an order can only be edited while ``status = 'Placed'`` — once it is packed the
  warehouse has a physical pick list and changing it silently would be a lie;
* stock moves with the edit, through ``stock_log``, so the ledger still explains
  every unit in and out.
"""

from flask import abort, flash, redirect, render_template, request, session, url_for

from ..db import get_db
from ..helpers import add_notification, current_user, login_required
from .shop import coupon_discount, shop_bp

# An editable order may not be pushed beyond this many copies of one title: it
# is a guard against a typo, not a business limit.
MAX_QUANTITY_PER_LINE = 50

EDITABLE_STATUSES = ("Placed",)


def _load_editable_order(connection, order_id, user):
    order = connection.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()

    if not order:
        abort(404)

    if order["user_id"] != user["id"] and user["role"] not in ("admin", "staff"):
        abort(404)

    return order


@shop_bp.route("/order/<int:order_id>/edit", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    connection = get_db()
    user = current_user()

    if request.method == "GET":
        order = _load_editable_order(connection, order_id, user)

        if order["status"] not in EDITABLE_STATUSES:
            flash(
                "That order has already been packed, so it can no longer be "
                "edited. You can cancel it instead if it has not shipped.",
                "error",
            )
            return redirect(url_for("shop.order_detail", order_id=order_id))

        items = connection.execute(
            """
            SELECT oi.*, b.title, b.stock
            FROM order_items oi JOIN books b ON b.id = oi.book_id
            WHERE oi.order_id = ? ORDER BY oi.id
            """,
            (order_id,),
        ).fetchall()

        return render_template(
            "order_edit.html",
            order=order,
            items=items,
            max_quantity=MAX_QUANTITY_PER_LINE,
        )

    try:
        connection.execute("BEGIN IMMEDIATE")

        # Re-read inside the transaction: the status could have changed while
        # the form was open, and packing wins over editing.
        order = _load_editable_order(connection, order_id, user)

        if order["status"] not in EDITABLE_STATUSES:
            raise ValueError(
                "That order has already been packed, so it can no longer be "
                "edited."
            )

        items = connection.execute(
            """
            SELECT oi.*, b.title, b.stock
            FROM order_items oi JOIN books b ON b.id = oi.book_id
            WHERE oi.order_id = ? ORDER BY oi.id
            """,
            (order_id,),
        ).fetchall()

        changes = []

        for item in items:
            raw = request.form.get(f"quantity_{item['id']}", "").strip()

            if raw == "":
                new_quantity = item["quantity"]
            else:
                try:
                    new_quantity = int(raw)
                except ValueError:
                    raise ValueError("Quantities must be whole numbers.")

            if new_quantity < 0 or new_quantity > MAX_QUANTITY_PER_LINE:
                raise ValueError(
                    f"Quantities must be between 0 and {MAX_QUANTITY_PER_LINE}."
                )

            if new_quantity == item["quantity"]:
                continue

            delta = new_quantity - item["quantity"]

            if delta > 0:
                available = connection.execute(
                    "SELECT stock FROM books WHERE id = ?", (item["book_id"],)
                ).fetchone()["stock"]

                shipped = min(delta, max(0, available))

                if shipped:
                    connection.execute(
                        """
                        UPDATE books SET stock = stock - ?
                        WHERE id = ? AND stock >= ?
                        """,
                        (shipped, item["book_id"], shipped),
                    )

                    connection.execute(
                        """
                        INSERT INTO stock_log(
                            book_id, change_amount, reason, order_id,
                            actor_user_id
                        )
                        VALUES (?, ?, 'order edit', ?, ?)
                        """,
                        (item["book_id"], -shipped, order_id, user["id"]),
                    )

                queued = delta - shipped

                if queued:
                    from .. import backorders

                    backorders.record_backorder(
                        connection, item["book_id"], order["user_id"],
                        order_id, queued
                    )

                    changes.append(
                        f"+{delta} {item['title']} ({queued} on backorder)"
                    )
                else:
                    changes.append(f"+{delta} {item['title']}")
            else:
                restock = -delta

                connection.execute(
                    "UPDATE books SET stock = stock + ? WHERE id = ?",
                    (restock, item["book_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO stock_log(
                        book_id, change_amount, reason, order_id, actor_user_id
                    )
                    VALUES (?, ?, 'order edit', ?, ?)
                    """,
                    (item["book_id"], restock, order_id, user["id"]),
                )
                changes.append(f"-{restock} {item['title']}")

            if new_quantity == 0:
                connection.execute(
                    "DELETE FROM order_items WHERE id = ?", (item["id"],)
                )
            else:
                connection.execute(
                    "UPDATE order_items SET quantity = ? WHERE id = ?",
                    (new_quantity, item["id"]),
                )

        if not changes:
            connection.rollback()
            flash("Nothing changed.", "success")
            return redirect(url_for("shop.order_detail", order_id=order_id))

        remaining = connection.execute(
            """
            SELECT book_id, quantity, price_paise FROM order_items
            WHERE order_id = ? ORDER BY id
            """,
            (order_id,),
        ).fetchall()

        if not remaining:
            raise ValueError(
                "An order cannot be emptied. Cancel it instead."
            )

        _reprice(connection, order, remaining)

        connection.execute(
            """
            INSERT INTO order_status_history(
                order_id, old_status, new_status, changed_by, note
            )
            VALUES (?, 'Placed', 'Placed', ?, ?)
            """,
            (order_id, user["id"], "Order edited: " + ", ".join(changes)),
        )

        connection.commit()

    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
        return redirect(url_for("shop.order_detail", order_id=order_id))
    except Exception:
        connection.rollback()
        raise

    flash("Order updated: " + ", ".join(changes), "success")
    return redirect(url_for("shop.order_detail", order_id=order_id))


def _reprice(connection, order, remaining):
    """Recompute every money column from the surviving lines.

    The rule is that the stored total must always equal the sum of its parts, so
    a coupon that no longer meets its minimum is dropped (with a note) rather
    than quietly overcharging.
    """
    from .. import pricing_rules
    from flask import current_app

    subtotal = sum(
        row["price_paise"] * row["quantity"] for row in remaining
    )

    lines = [
        {
            "book_id": row["book_id"],
            "quantity": row["quantity"],
            "line_total": row["price_paise"] * row["quantity"],
        }
        for row in remaining
    ]

    discount = 0
    coupon_code = order["coupon_code"]
    coupon_note = ""

    if coupon_code:
        coupon, discount, error = coupon_discount(
            connection, coupon_code, order["user_id"], subtotal, lines
        )

        if error:
            coupon_code = None
            discount = 0
            coupon_note = (
                f" Coupon {order['coupon_code']} was removed: {error}"
            )

    rules_discount, _per_book = pricing_rules.evaluate(connection, lines)
    rules_discount = min(rules_discount, max(0, subtotal - discount))

    total_discount = discount + rules_discount
    taxable_subtotal = max(0, subtotal - total_discount)

    shipping = order["shipping_paise"]

    if subtotal - total_discount >= current_app.config["FREE_SHIPPING_PAISE"]:
        shipping = 0
    elif shipping == 0:
        # The order stopped qualifying for free shipping.
        shipping = current_app.config["SHIPPING_PAISE"]

    tax_percent = current_app.config["TAX_PERCENT"]
    tax = (taxable_subtotal * tax_percent + 50) // 100

    gift_wrap_paise = order["gift_wrap_paise"]
    points_redeemed = order["loyalty_points_redeemed"]
    points_discount = points_redeemed * 10
    gift_card_paise = order["gift_card_paise"]

    payable = taxable_subtotal + shipping + tax + gift_wrap_paise

    if points_discount:
        payable -= min(points_discount, payable)

    if gift_card_paise:
        payable -= min(gift_card_paise, payable)

    connection.execute(
        """
        UPDATE orders
        SET subtotal_paise = ?, discount_paise = ?, rules_discount_paise = ?,
            shipping_paise = ?, tax_paise = ?, total_paise = ?,
            coupon_code = ?
        WHERE id = ? AND status = 'Placed'
        """,
        (
            subtotal, discount, rules_discount, shipping, tax,
            max(0, payable), coupon_code, order["id"],
        ),
    )

    if coupon_note:
        connection.execute(
            """
            INSERT INTO order_status_history(
                order_id, old_status, new_status, changed_by, note
            )
            VALUES (?, 'Placed', 'Placed', NULL, ?)
            """,
            (order["id"], coupon_note.strip()),
        )

    return subtotal, payable
