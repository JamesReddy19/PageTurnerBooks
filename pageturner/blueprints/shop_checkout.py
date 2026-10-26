
"""Stock reservation and transactional checkout."""
from datetime import datetime, timedelta
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, session, url_for
)
from ..db import get_db
from ..helpers import (
    login_required,
    active_reservations,
    effective_book_price,
    loyalty_points_balance,
    loyalty_points_earned,
    loyalty_discount_paise,
    loyalty_record,
)
from .. import backorders, giftcards, idempotency, outbox, pricing_rules
from ..helpers import add_notification
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

                # Requirement 65: we never sell stock we do not have, but
                # asking for more copies than are on the shelf is a backorder
                # rather than an error. Reserve what exists and queue the rest
                # when the order is placed.
                available = max(0, book["stock"] - reserved)
                reserve_quantity = min(item["quantity"], available)
                item["backordered_quantity"] = item["quantity"] - reserve_quantity

                connection.execute(
                    "DELETE FROM reservations WHERE cart_key = ? AND book_id = ?",
                    (cart_key, book_id)
                )

                if reserve_quantity > 0:
                    connection.execute(
                        """
                        INSERT INTO reservations(
                            user_id, cart_key, book_id, quantity, expires_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            user_id, cart_key, book_id,
                            reserve_quantity, expires
                        )
                    )

            # Requirement 61: the form gets a single-use token, committed with
            # the reservation so a token can never outlive its reservation.
            checkout_token = idempotency.new_checkout_token(
                connection, user_id
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
        checkout_token=checkout_token,
        items=items,
        subtotal=subtotal,
        addresses=addresses,
        default_address=default_address,
        loyalty_balance=loyalty_points_balance(connection, user_id),
        loyalty_points_per_rupee=loyalty_discount_paise(1),
        gift_wrap_paise=current_app.config["GIFT_WRAP_PAISE"],
        gift_message_max_length=current_app.config["GIFT_MESSAGE_MAX_LENGTH"]
    )


@shop_bp.post("/checkout")
@login_required
def place_order():
    connection = get_db()
    user_id = session["user_id"]
    items, _subtotal = get_cart_rows()

    if not items:
        # Requirement 61: a refresh arrives *after* the first submission
        # emptied the cart, so an empty cart must not swallow the replay — it
        # has to land on the order that was already placed.
        replayed = idempotency.replayed_order_id(
            connection, request.form.get("checkout_token", ""), user_id
        )

        if replayed:
            flash(
                "That order has already been placed - it is open below.",
                "success"
            )
            return redirect(url_for("shop.order_detail", order_id=replayed))

        flash("Your cart is empty.", "error")
        return redirect(url_for("shop.cart"))

    name = request.form.get("name", "").strip()[:100]
    phone = request.form.get("phone", "").strip()
    address_id = request.form.get("address_id", "").strip()
    new_address = request.form.get("new_address", "").strip()[:300]
    pincode = request.form.get("pincode", "").strip()

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
    checkout_token = request.form.get("checkout_token", "").strip()

    try:
        connection.execute("BEGIN IMMEDIATE")

        # Requirement 61: claim the single-use token in the same transaction as
        # the order. A replayed POST finds it spent and is answered with the
        # order it already produced instead of placing a second one.
        token_status, existing_order_id = idempotency.claim_checkout_token(
            connection, checkout_token, user_id
        )

        if token_status == "replayed":
            connection.rollback()
            flash(
                "That order has already been placed - it is open below.",
                "success"
            )

            if existing_order_id:
                return redirect(
                    url_for("shop.order_detail", order_id=existing_order_id)
                )

            return redirect(url_for("shop.order_history"))

        if token_status == "unknown":
            connection.rollback()
            flash(
                "Your checkout form expired. Please confirm your order again.",
                "error"
            )
            return redirect(url_for("shop.checkout"))

        subtotal = 0
        checked_items = []
        ship_now_by_book = {}

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

            reservation_live = bool(
                reservation
                and reservation["expires_at"]
                > datetime.now().isoformat(timespec="seconds")
            )

            reserved_others = active_reservations(
                connection, book_id, cart_key
            )

            # Requirement 65: too few copies is no longer an error. Sell what
            # the warehouse holds and queue the remainder as a backorder; the
            # customer pays the same total either way. `books.stock` is debited
            # by `ship_now` only, so it can never go negative. `ship_now` is
            # also capped by the reserved quantity, so a reservation made when
            # fewer copies existed is never oversold by a later restock race.
            available_now = max(0, book["stock"] - reserved_others)

            # A live reservation is still required to buy stock, so checkout
            # cannot be replayed to skip the reservation queue. Once nothing is
            # in the warehouse there is nothing to reserve, and the whole line
            # simply becomes a backorder.
            if available_now and not reservation_live:
                raise ValueError(
                    "Your checkout reservation expired. Please open checkout again."
                )

            ship_now = min(
                item["quantity"],
                available_now,
                reservation["quantity"] if reservation_live else 0,
            )

            # Requirement 47: charge the price that is active right now, using
            # the same helper the catalog and cart use. The resulting per-copy
            # price is snapshotted onto the order line below, so the invoice
            # keeps showing what was actually paid even after the sale ends.
            unit_price = effective_book_price(book)
            line_total = unit_price * item["quantity"]
            subtotal += line_total
            checked_items.append(
                (book, item["quantity"], line_total)
            )
            ship_now_by_book[book_id] = ship_now

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

        # Requirement 63: automatic pricing rules are data, not code. They are
        # evaluated here so the order total, the invoice and the ledger all use
        # one number.
        rules_discount, _per_book_rules = pricing_rules.evaluate(
            connection,
            [
                {
                    "book_id": book["id"],
                    "quantity": quantity,
                    "line_total": line,
                }
                for book, quantity, line in checked_items
            ]
        )
        rules_discount = min(rules_discount, max(0, subtotal - discount))
        total_discount = discount + rules_discount

        shipping_row = connection.execute(
            "SELECT fee_paise FROM shipping_zones WHERE prefix = ?",
            (pincode[:2],)
        ).fetchone()

        shipping = (
            0
            if subtotal - total_discount >= current_app.config["FREE_SHIPPING_PAISE"]
            else (shipping_row["fee_paise"] if shipping_row else 8000)
        )

        taxable_subtotal = max(0, subtotal - total_discount)
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

            # Pricing-rule discounts reduce the taxable value of their own line.
            line_discount += min(
                _per_book_rules.get(book["id"], 0), line_total - line_discount
            )

            taxable_line = line_total - line_discount
            tax += (taxable_line * tax_percent + 50) // 100

        # Requirement 48: gift wrapping is an optional flat extra charge. The
        # message is stored verbatim and escaped on render, so a customer who
        # types <script> sees those characters rather than executing them.
        gift_wrap_paise = 0
        gift_message = ""

        if request.form.get("gift_wrap"):
            gift_wrap_paise = int(current_app.config["GIFT_WRAP_PAISE"])
            gift_message = (request.form.get("gift_message") or "").strip()

            if len(gift_message) > current_app.config["GIFT_MESSAGE_MAX_LENGTH"]:
                raise ValueError(
                    "A gift message can be at most "
                    f"{current_app.config['GIFT_MESSAGE_MAX_LENGTH']} characters."
                )

        payable_before_points = taxable_subtotal + shipping + tax + gift_wrap_paise

        # Requirement 49: redeem loyalty points. The requested amount is clamped
        # to the stored balance and to what is still payable, so the total can
        # never become negative and a tampered form cannot spend more points
        # than the ledger says this customer owns.
        try:
            requested_points = int(request.form.get("points_to_redeem") or 0)
        except (TypeError, ValueError):
            requested_points = 0

        requested_points = max(0, requested_points)
        requested_points = min(
            requested_points, loyalty_points_balance(connection, user_id)
        )

        points_used = min(
            loyalty_discount_paise(requested_points), payable_before_points
        ) // 10

        points_discount = points_used * 10
        total = payable_before_points - points_discount

        # Requirement 64: a gift card pays last, and only for what is left. The
        # debit is a ledger row inside this transaction, so a failed order cannot
        # consume the card and a replayed order cannot spend it twice.
        gift_card_code = (request.form.get("gift_card_code") or "").strip()
        gift_card_id = None
        gift_card_paise = 0

        if gift_card_code:
            gift_card_id, gift_card_paise = giftcards.redeem_gift_card(
                connection, gift_card_code, None, total
            )

            if gift_card_id is None:
                raise ValueError(
                    "That gift card cannot be used - check the code, or whether "
                    "it has expired or been cancelled."
                )

            total -= gift_card_paise

        # Points are earned on the amount actually paid, not the pre-discount
        # subtotal, so redeeming points cannot inflate future earnings.
        points_earned = loyalty_points_earned(total)

        cursor = connection.execute(
            """
            INSERT INTO orders(
                user_id, name, phone, address,
                subtotal_paise, discount_paise, shipping_paise,
                tax_paise, total_paise, coupon_code, pincode, status,
                gift_wrap_paise, gift_message,
                loyalty_points_earned, loyalty_points_redeemed,
                rules_discount_paise, gift_card_paise, gift_card_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Placed', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, name, phone, address, subtotal, discount,
                shipping, tax, total,
                coupon["code"] if coupon else None, pincode,
                gift_wrap_paise, gift_message or None,
                points_earned, points_used,
                rules_discount, gift_card_paise, gift_card_id
            )
        )

        order_id = cursor.lastrowid

        # Requirement 64: the card was priced before this order existed, so its
        # ledger row is written now that there is a real order id to point at.
        # Same transaction as the order: neither can exist without the other.
        if gift_card_id and gift_card_paise:
            giftcards.record_redemption(
                connection, gift_card_id, order_id, gift_card_paise
            )

        for book, quantity, line_total in checked_items:
            ship_now = ship_now_by_book.get(book["id"], quantity)

            if ship_now:
                changed = connection.execute(
                    """
                    UPDATE books
                    SET stock = stock - ?
                    WHERE id = ? AND stock >= ?
                    """,
                    (ship_now, book["id"], ship_now)
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
                (order_id, book["id"], quantity, line_total // quantity)
            )

            # One ledger row per shipped line, inside the same transaction as
            # the stock debit above, so the ledger and `books.stock` can never
            # disagree about a purchase.
            if ship_now:
                connection.execute(
                    """
                    INSERT INTO stock_log(
                        book_id, change_amount, reason, order_id, actor_user_id
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (book["id"], -ship_now, "purchase", order_id, user_id)
                )

            # Requirement 65: whatever could not ship is queued, oldest-first on
            # restock, so nothing is silently dropped and no stock is invented.
            backordered = quantity - ship_now

            if backordered > 0:
                backorders.record_backorder(
                    connection, book["id"], user_id, order_id, backordered
                )

                connection.execute(
                    """
                    INSERT INTO order_status_history(
                        order_id, old_status, new_status, changed_by, note
                    )
                    VALUES (?, NULL, 'Placed', ?, ?)
                    """,
                    (
                        order_id, user_id,
                        f"{backordered} x {book['title']} on backorder"
                    )
                )

        # Requirement 49: the ledger is the single source of truth for the
        # balance, so both movements are written in this same transaction as the
        # order. If anything below fails, the order and its points vanish
        # together instead of leaving a half-credited customer.
        if points_used:
            loyalty_record(
                connection, user_id, order_id, -points_used, "order redemption"
            )

        if points_earned:
            loyalty_record(
                connection, user_id, order_id, points_earned, "order earned"
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

        # Requirement 69: the confirmation email is an *outbox row*, written
        # here inside the order's own transaction and delivered later by
        # `outbox.process_due()`. Sending inline would either announce an order
        # that can still roll back, or lose the message if the process died
        # between commit and send. The dedupe key is derived from the order, so
        # the outbox can never hold two confirmations for one order.
        outbox.enqueue(
            connection,
            user_id,
            f"Order #{order_id} confirmed — PageTurner Books",
            (
                f"Hello {name},\n\n"
                f"Thank you for your order #{order_id}.\n"
                f"Items: {sum(quantity for _b, quantity, _t in checked_items)}\n"
                f"Total: Rs {total / 100:.2f}\n\n"
                "We will email you again when your parcel ships."
            ),
            recipient=email,
            dedupe_key=f"order:{order_id}:confirmed",
        )

        if coupon:
            connection.execute(
                """
                INSERT INTO coupon_uses(coupon_id, user_id, order_id)
                VALUES (?, ?, ?)
                """,
                (coupon["id"], user_id, order_id)
            )

        # Requirement 61: remember the order this token produced, so the next
        # POST with the same token can be redirected instead of repeated.
        idempotency.attach_order(connection, checkout_token, order_id)

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
