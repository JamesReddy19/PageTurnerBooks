
"""Stock reservation and transactional checkout."""
from datetime import datetime, timedelta
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, session, url_for
)
from ..db import get_db
from ..helpers import login_required, active_reservations
from ..payments import ALLOWED_METHODS, charge, record_payment
from .shop import (
    shop_bp, get_cart_rows, coupon_discount,
    current_app_reservation_minutes
)


@shop_bp.get("/checkout")
@login_required
def checkout():
    connection = get_db()
    user_id = session["user_id"]
    items, subtotal = get_cart_rows()

    if not items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("shop.cart"))

    cart_key = f"user:{user_id}"
    now = datetime.now()
    expires = (
        now + timedelta(minutes=current_app_reservation_minutes())
    ).isoformat(timespec="seconds")

    if request.method == "GET":
        try:
            connection.execute("BEGIN IMMEDIATE")

            connection.execute(
                "DELETE FROM reservations WHERE expires_at <= ?",
                (now.isoformat(timespec="seconds"),)
            )

            for item in items:
                book_id = item["book"]["id"]

                book = connection.execute(
                    "SELECT * FROM books WHERE id = ?",
                    (book_id,)
                ).fetchone()

                if not book:
                    raise ValueError(
                        "A book in your cart is no longer available."
                    )

                today = datetime.now().date().isoformat()

                if book["release_date"] and book["release_date"] > today:
                    raise ValueError(
                        f"{book['title']} is coming soon and cannot be ordered yet."
                    )

                reserved = active_reservations(
                    connection, book_id, cart_key
                )

                if item["quantity"] > book["stock"] - reserved:
                    raise ValueError(
                        f"There are not enough copies of {book['title']} available."
                    )

                connection.execute(
                    "DELETE FROM reservations WHERE cart_key = ? AND book_id = ?",
                    (cart_key, book_id)
                )

                connection.execute(
                    """
                    INSERT INTO reservations(
                        user_id, cart_key, book_id, quantity, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, cart_key, book_id,
                        item["quantity"], expires
                    )
                )

            connection.commit()

        except Exception as error:
            connection.rollback()
            flash(
                str(error) if isinstance(error, ValueError)
                else "Could not reserve your cart. Please try again.",
                "error"
            )
            return redirect(url_for("shop.cart"))

    addresses = connection.execute(
        """
        SELECT * FROM addresses
        WHERE user_id = ?
        ORDER BY is_default DESC, id DESC
        """,
        (user_id,)
    ).fetchall()

    default_address = next(
        (address for address in addresses if address["is_default"]),
        None
    )

    return render_template(
        "checkout.html",
        items=items,
        subtotal=subtotal,
        addresses=addresses,
        default_address=default_address
    )


@shop_bp.post("/checkout")
@login_required
def place_order():
    connection = get_db()
    user_id = session["user_id"]
    items, _subtotal = get_cart_rows()

    if not items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("shop.cart"))

    name = request.form.get("name", "").strip()[:100]
    phone = request.form.get("phone", "").strip()
    address_id = request.form.get("address_id", "").strip()
    new_address = request.form.get("new_address", "").strip()[:300]
    pincode = request.form.get("pincode", "").strip()
    payment_method = request.form.get("payment_method", "").strip()

    # No real gateway is called: this choice only decides the simulated
    # payment status recorded with the order.
    if payment_method not in ALLOWED_METHODS:
        flash("Choose a payment method.", "error")
        return redirect(url_for("shop.checkout"))

    if not name or not phone.isdigit() or len(phone) != 10:
        flash(
            "Enter your name and a valid 10-digit phone number.",
            "error"
        )
        return redirect(url_for("shop.checkout"))

    if address_id:
        address_row = connection.execute(
            "SELECT * FROM addresses WHERE id = ? AND user_id = ?",
            (address_id, user_id)
        ).fetchone()

        if not address_row:
            abort(403)

        address = (
            f"{address_row['line1']}, {address_row['city']} "
            f"- {address_row['pincode']}"
        )
        pincode = address_row["pincode"]
    else:
        address = new_address

    if not address or not pincode.isdigit() or len(pincode) != 6:
        flash(
            "Choose a saved address or enter an address and a valid 6-digit pincode.",
            "error"
        )
        return redirect(url_for("shop.checkout"))

    cart_key = f"user:{user_id}"

    try:
        connection.execute("BEGIN IMMEDIATE")

        subtotal = 0
        checked_items = []

        for item in items:
            book_id = item["book"]["id"]

            book = connection.execute(
                "SELECT * FROM books WHERE id = ?",
                (book_id,)
            ).fetchone()

            # First check that the book still exists.
            if not book:
                raise ValueError(
                    "A book in your cart is no longer available."
                )

            # Block checkout for books that have not been released.
            today = datetime.now().date().isoformat()

            if book["release_date"] and book["release_date"] > today:
                raise ValueError(
                    f"{book['title']} is coming soon and cannot be ordered yet."
                )

            # Then validate the stock reservation.
            reservation = connection.execute(
                """
                SELECT quantity, expires_at
                FROM reservations
                WHERE cart_key = ? AND book_id = ?
                """,
                (cart_key, book_id)
            ).fetchone()

            if (
                not reservation
                or reservation["expires_at"]
                <= datetime.now().isoformat(timespec="seconds")
                or reservation["quantity"] < item["quantity"]
            ):
                raise ValueError(
                    "Your checkout reservation expired. Please open checkout again."
                )

            reserved_others = active_reservations(
                connection, book_id, cart_key
            )

            if book["stock"] - reserved_others < item["quantity"]:
                raise ValueError(
                    f"Not enough stock remains for {book['title']}."
                )

            line_total = book["price_paise"] * item["quantity"]
            subtotal += line_total
            checked_items.append(
                (book, item["quantity"], line_total)
            )

        coupon, discount, coupon_error = coupon_discount(
            connection,
            session.get("coupon_code", ""),
            user_id,
            subtotal,
            [
                {"book": book, "quantity": quantity, "line_total": line}
                for book, quantity, line in checked_items
            ]
        )

        if coupon_error:
            raise ValueError(coupon_error)

        shipping_row = connection.execute(
            "SELECT fee_paise FROM shipping_zones WHERE prefix = ?",
            (pincode[:2],)
        ).fetchone()

        shipping = (
            0
            if subtotal - discount >= current_app.config["FREE_SHIPPING_PAISE"]
            else (shipping_row["fee_paise"] if shipping_row else 8000)
        )

        taxable_subtotal = max(0, subtotal - discount)
        tax_percent = current_app.config["TAX_PERCENT"]

        # Allocate coupon discounts across eligible books.
        if coupon and coupon["category_id"]:
            eligible_ids = {
                row["book_id"]
                for row in connection.execute(
                    "SELECT book_id FROM book_categories WHERE category_id = ?",
                    (coupon["category_id"],)
                ).fetchall()
            }
        else:
            eligible_ids = {
                book["id"]
                for book, _quantity, _line_total in checked_items
            }

        eligible_total = sum(
            line
            for book, _quantity, line in checked_items
            if book["id"] in eligible_ids
        )

        eligible_lines = [
            book["id"]
            for book, _quantity, _line_total in checked_items
            if book["id"] in eligible_ids
        ]

        last_eligible_id = eligible_lines[-1] if eligible_lines else None
        allocated_discount = 0
        tax = 0

        for book, _quantity, line_total in checked_items:
            line_discount = 0

            if book["id"] in eligible_ids and eligible_total:
                if book["id"] == last_eligible_id:
                    line_discount = discount - allocated_discount
                else:
                    line_discount = discount * line_total // eligible_total
                    allocated_discount += line_discount

            taxable_line = line_total - line_discount
            tax += (taxable_line * tax_percent + 50) // 100

        total = taxable_subtotal + shipping + tax

        cursor = connection.execute(
            """
            INSERT INTO orders(
                user_id, name, phone, address,
                subtotal_paise, discount_paise, shipping_paise,
                tax_paise, total_paise, coupon_code, pincode, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Placed')
            """,
            (
                user_id, name, phone, address, subtotal, discount,
                shipping, tax, total,
                coupon["code"] if coupon else None, pincode
            )
        )

        order_id = cursor.lastrowid

        for book, quantity, line_total in checked_items:
            changed = connection.execute(
                """
                UPDATE books
                SET stock = stock - ?
                WHERE id = ? AND stock >= ?
                """,
                (quantity, book["id"], quantity)
            ).rowcount

            if changed != 1:
                raise ValueError(
                    f"Stock changed for {book['title']}. Please try again."
                )

            connection.execute(
                """
                INSERT INTO order_items(
                    order_id, book_id, quantity, price_paise
                )
                VALUES (?, ?, ?, ?)
                """,
                (order_id, book["id"], quantity, book["price_paise"])
            )

            connection.execute(
                """
                INSERT INTO stock_log(
                    book_id, change_amount, reason, order_id, actor_user_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (book["id"], -quantity, "purchase", order_id, user_id)
            )

        connection.execute(
            """
            INSERT INTO order_status_history(
                order_id, old_status, new_status, changed_by, note
            )
            VALUES (?, NULL, 'Placed', ?, 'Order created')
            """,
            (order_id, user_id)
        )

        # Simulated payment: no gateway is contacted and no money moves.
        record_payment(
            connection, order_id, charge(payment_method, total)
        )

        if coupon:
            connection.execute(
                """
                INSERT INTO coupon_uses(coupon_id, user_id, order_id)
                VALUES (?, ?, ?)
                """,
                (coupon["id"], user_id, order_id)
            )

        connection.execute(
            "DELETE FROM reservations WHERE cart_key = ?",
            (cart_key,)
        )

        connection.execute(
            "DELETE FROM cart_items WHERE user_id = ?",
            (user_id,)
        )

        connection.commit()

    except Exception as error:
        connection.rollback()
        flash(
            str(error) if isinstance(error, ValueError)
            else "We could not place your order. Please try again.",
            "error"
        )
        return redirect(url_for("shop.checkout"))

    session.pop("cart", None)
    session.pop("coupon_code", None)

    flash("Your order has been placed successfully.", "success")
    return redirect(url_for("shop.order_detail", order_id=order_id))
