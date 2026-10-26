
"""Shared shop helpers and the storefront Blueprint."""
from datetime import datetime
from flask import Blueprint, session
from ..db import get_db
from ..helpers import active_reservations, current_user, effective_book_price

shop_bp = Blueprint("shop", __name__)


def get_cart_rows():
    connection = get_db()
    user = current_user()
    cart = {}

    if user:
        rows = connection.execute(
            "SELECT book_id, quantity FROM cart_items WHERE user_id = ?",
            (user["id"],)
        ).fetchall()
        cart = {str(row["book_id"]): row["quantity"] for row in rows}

    for key, quantity in session.get("cart", {}).items():
        cart[key] = max(int(quantity), cart.get(key, 0))

    items = []
    subtotal = 0

    for key, quantity in cart.items():
        try:
            book_id = int(key)
            quantity = max(1, min(99, int(quantity)))
        except (ValueError, TypeError):
            continue

        book = connection.execute(
            "SELECT * FROM books WHERE id = ?",
            (book_id,)
        ).fetchone()

        if not book:
            continue

        # Requirement 47: the cart must charge the same price the catalog shows,
        # so the flash-sale window is evaluated here instead of reading the list
        # price directly. unit_price is kept alongside line_total so the cart and
        # checkout templates can show the price actually charged per copy.
        unit_price = effective_book_price(book)
        line_total = unit_price * quantity
        subtotal += line_total
        items.append({
            "book": book,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total
        })

    return items, subtotal


def save_quantity(book_id, quantity):
    user = current_user()
    connection = get_db()

    book = connection.execute(
        "SELECT stock, release_date FROM books WHERE id = ?",
        (book_id,)
    ).fetchone()

    if not book:
        return False, "That book is no longer available."

    # Prevent adding a book that has not been released yet.
    today = datetime.now().date().isoformat()

    if book["release_date"] and book["release_date"] > today:
        return False, "This book is coming soon and cannot be ordered yet."

    if quantity < 1 or quantity > 99:
        return False, "Choose a quantity from 1 to 99."

    cart_key = f"user:{user['id']}" if user else None

    available = book["stock"] - active_reservations(
        connection, book_id, cart_key
    )

    if quantity > available:
        return False, f"Only {max(available, 0)} copies are currently available."

    if user:
        connection.execute(
            """
            INSERT INTO cart_items(user_id, book_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, book_id)
            DO UPDATE SET quantity = excluded.quantity
            """,
            (user["id"], book_id, quantity)
        )
        connection.commit()
    else:
        cart = session.get("cart", {})
        cart[str(book_id)] = quantity
        session["cart"] = cart

    return True, "Book added to your cart."


def current_app_reservation_minutes():
    from flask import current_app
    return int(current_app.config["RESERVATION_MINUTES"])


def coupon_discount(connection, code, user_id, subtotal, items):
    if not code:
        return None, 0, ""

    coupon = connection.execute(
        "SELECT * FROM coupons WHERE code = ? COLLATE NOCASE",
        (code,)
    ).fetchone()

    if (
        not coupon
        or not coupon["active"]
        or coupon["expiry_date"] < datetime.now().date().isoformat()
    ):
        return None, 0, "That coupon is invalid, expired or deactivated."

    if subtotal < coupon["min_order_paise"]:
        return None, 0, "Your order does not meet this coupon's minimum value."

    if coupon["usage_limit"] is not None:
        used = connection.execute(
            "SELECT COUNT(*) AS count FROM coupon_uses WHERE coupon_id = ?",
            (coupon["id"],)
        ).fetchone()["count"]

        if used >= coupon["usage_limit"]:
            return None, 0, "This coupon has reached its usage limit."

    if connection.execute(
        "SELECT id FROM coupon_uses WHERE coupon_id = ? AND user_id = ?",
        (coupon["id"], user_id)
    ).fetchone():
        return None, 0, "You have already used this coupon."

    if coupon["category_id"]:
        eligible_ids = {
            row["book_id"]
            for row in connection.execute(
                "SELECT book_id FROM book_categories WHERE category_id = ?",
                (coupon["category_id"],)
            ).fetchall()
        }

        eligible = sum(
            item["line_total"]
            for item in items
            if item["book"]["id"] in eligible_ids
        )

        if eligible == 0:
            return None, 0, "This coupon does not apply to the books in your cart."

        base = eligible
    else:
        base = subtotal

    discount = base * coupon["percent"] // 100

    if coupon["max_discount_paise"] is not None:
        discount = min(discount, coupon["max_discount_paise"])

    return coupon, discount, ""


def customers_also_bought(book_id):
    return get_db().execute(
        """
        SELECT related_book.id, related_book.title,
               COUNT(*) AS together_count
        FROM order_items chosen
        JOIN order_items related_item
          ON related_item.order_id = chosen.order_id
         AND related_item.book_id <> chosen.book_id
        JOIN books related_book
          ON related_book.id = related_item.book_id
        WHERE chosen.book_id = ?
        GROUP BY related_book.id, related_book.title
        ORDER BY together_count DESC
        LIMIT 5
        """,
        (book_id,)
    ).fetchall()


# Import route modules after the Blueprint and helpers exist.
from . import shop_catalog, shop_cart, shop_checkout, shop_orders  # noqa: E402,F401
