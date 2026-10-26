
"""Shared shop helpers and the storefront Blueprint."""
from datetime import datetime
from flask import Blueprint, session
from ..db import get_db
from ..helpers import active_reservations, current_user, effective_book_price
from ..reviews import AVERAGE_RATING_SQL

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


def owned_book_ids(connection, user_id):
    """Ids of books the shopper already bought (cancelled orders do not count)."""
    rows = connection.execute(
        "SELECT DISTINCT oi.book_id FROM order_items oi "
        "JOIN orders o ON o.id = oi.order_id "
        "WHERE o.user_id = ? AND o.status <> 'Cancelled'",
        (user_id,)
    ).fetchall()
    return {row["book_id"] for row in rows}


def recommended_books(user, limit=5):
    """
    Requirement 75: personalised picks for the home page.

    The cheapest useful signal wins:

    1. Co-purchase — books other shoppers bought in the same orders as the books
       this reader already owns, ranked by how often that happened.
    2. Bestsellers by units sold — the fallback for a signed-out visitor or a
       brand-new account with no order history at all.

    Either way, books the reader already owns and books with no stock are
    excluded, so the block never recommends something they cannot buy.
    """
    connection = get_db()

    excluded = owned_book_ids(connection, user["id"]) if user else set()

    rows = []
    if user:
        # Anchor on every order line containing a book this reader owns (whoever
        # placed that order) and count the *other* books in those same orders.
        # That is the "readers who bought your books also bought" signal; a
        # reader's own orders alone could only ever return books they own.
        rows = connection.execute(
            f"""
            SELECT b.*, COUNT(DISTINCT co.order_id) AS score,
                   {AVERAGE_RATING_SQL} AS average_rating
            FROM order_items anchor
            JOIN order_items co
              ON co.order_id = anchor.order_id
             AND co.book_id <> anchor.book_id
            JOIN orders co_order
              ON co_order.id = co.order_id
             AND co_order.status <> 'Cancelled'
            JOIN books b ON b.id = co.book_id
            WHERE anchor.book_id IN (
                    SELECT oi.book_id
                    FROM order_items oi
                    JOIN orders o ON o.id = oi.order_id
                    WHERE o.user_id = ? AND o.status <> 'Cancelled'
                  )
              AND b.stock > 0
            GROUP BY b.id
            ORDER BY score DESC, b.title COLLATE NOCASE
            LIMIT ?
            """,
            (user["id"], limit + len(excluded))
        ).fetchall()

        rows = [row for row in rows if row["id"] not in excluded][:limit]

    if len(rows) < limit:
        chosen = {row["id"] for row in rows}
        fallback = connection.execute(
            f"""
            SELECT b.*, COALESCE(SUM(oi.quantity), 0) AS units_sold,
                   {AVERAGE_RATING_SQL} AS average_rating
            FROM books b
            LEFT JOIN order_items oi ON oi.book_id = b.id
            WHERE b.stock > 0
            GROUP BY b.id
            ORDER BY units_sold DESC, b.title COLLATE NOCASE
            LIMIT ?
            """,
            (limit + len(excluded) + len(chosen),)
        ).fetchall()

        for row in fallback:
            if len(rows) >= limit:
                break
            if row["id"] in excluded or row["id"] in chosen:
                continue
            rows.append(row)

    return rows


# Import route modules after the Blueprint and helpers exist.
from . import (  # noqa: E402,F401
    shop_catalog,
    shop_cart,
    shop_checkout,
    shop_orders,
    shop_reviews,
    shop_book_requests,
    shop_wishlist_share,
)
