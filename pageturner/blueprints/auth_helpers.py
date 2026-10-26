"""Small helper functions for creating a login session and merging a cart."""
import secrets
from flask import request, session
from ..db import get_db
from ..helpers import make_session_token

def save_login_session(user):
    connection = get_db()
    token, token_hash = make_session_token()
    connection.execute(
        "INSERT INTO user_sessions(user_id,token_hash,device_name) VALUES (?,?,?)",
        (user["id"], token_hash, request.user_agent.string[:150] or "Unknown device"),
    )
    connection.commit()
    session.clear()
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["session_token"] = token
    session["csrf_token"] = secrets.token_urlsafe(32)
    merge_guest_cart(user["id"])


def merge_guest_cart(user_id):
    connection = get_db()
    cart = session.get("cart", {})
    for book_id, quantity in cart.items():
        try:
            book_id = int(book_id)
            quantity = max(1, min(99, int(quantity)))
        except (ValueError, TypeError):
            continue
        book = connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            continue
        old = connection.execute("SELECT quantity FROM cart_items WHERE user_id = ? AND book_id = ?", (user_id, book_id)).fetchone()
        new_quantity = min(book["stock"], quantity + (old["quantity"] if old else 0))
        if new_quantity:
            connection.execute("INSERT INTO cart_items(user_id,book_id,quantity) VALUES (?,?,?) ON CONFLICT(user_id,book_id) DO UPDATE SET quantity=excluded.quantity", (user_id, book_id, new_quantity))
    session.pop("cart", None)
    connection.commit()
