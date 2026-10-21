"""Cart, coupons, wishlist and customer reviews."""
import sqlite3
from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from ..db import get_db
from ..helpers import current_user, login_required
from .shop import shop_bp, get_cart_rows, save_quantity, coupon_discount

@shop_bp.post("/cart/add/<int:book_id>")
def add_to_cart(book_id):
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    ok, message = save_quantity(book_id, quantity)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"ok": ok, "message": message, "count": sum(int(value) for value in session.get("cart", {}).values()) + (get_db().execute("SELECT COALESCE(SUM(quantity),0) AS count FROM cart_items WHERE user_id=?", (session["user_id"],)).fetchone()["count"] if current_user() else 0)}), (200 if ok else 400)
    flash(message, "success" if ok else "error")
    return redirect(request.referrer or url_for("shop.index"))


@shop_bp.get("/cart")
def cart():
    items, subtotal = get_cart_rows()
    coupon_code = session.get("coupon_code", "")
    discount = 0
    coupon_message = ""
    if coupon_code:
        coupon, discount, coupon_message = coupon_discount(get_db(), coupon_code, session.get("user_id", 0), subtotal, items)
        if coupon_message:
            session.pop("coupon_code", None)
            coupon_code = ""
    return render_template("cart.html", items=items, subtotal=subtotal, discount=discount, total=max(0, subtotal-discount), coupon_code=coupon_code, coupon_message=coupon_message)


@shop_bp.post("/cart/update/<int:book_id>")
def update_cart(book_id):
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    ok, message = save_quantity(book_id, quantity)
    flash(message if ok else message, "success" if ok else "error")
    return redirect(url_for("shop.cart"))


@shop_bp.post("/cart/remove/<int:book_id>")
def remove_cart_item(book_id):
    user = current_user()
    if user:
        get_db().execute("DELETE FROM cart_items WHERE user_id=? AND book_id=?", (user["id"], book_id))
        get_db().commit()
    cart_data = session.get("cart", {})
    cart_data.pop(str(book_id), None)
    session["cart"] = cart_data
    flash("Book removed from your cart.", "success")
    return redirect(url_for("shop.cart"))


@shop_bp.post("/cart/coupon")
def apply_coupon():
    code = request.form.get("code", "").strip().upper()
    items, subtotal = get_cart_rows()
    coupon, _discount, error = coupon_discount(get_db(), code, session.get("user_id", 0), subtotal, items)
    if error:
        flash(error, "error")
    else:
        session["coupon_code"] = coupon["code"]
        flash("Coupon applied.", "success")
    return redirect(url_for("shop.cart"))


@shop_bp.post("/cart/coupon/remove")
def remove_coupon():
    session.pop("coupon_code", None)
    flash("Coupon removed.", "success")
    return redirect(url_for("shop.cart"))


@shop_bp.post("/wishlist/toggle/<int:book_id>")
@login_required
def toggle_wishlist(book_id):
    connection = get_db()
    exists = connection.execute("SELECT id FROM wishlist WHERE user_id=? AND book_id=?", (session["user_id"], book_id)).fetchone()
    if exists:
        connection.execute("DELETE FROM wishlist WHERE id=?", (exists["id"],))
        added = False
    else:
        if not connection.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            abort(404)
        connection.execute("INSERT OR IGNORE INTO wishlist(user_id,book_id) VALUES (?,?)", (session["user_id"], book_id))
        added = True
    connection.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"added": added})
    return redirect(request.referrer or url_for("shop.index"))


@shop_bp.get("/wishlist")
@login_required
def wishlist():
    rows = get_db().execute("SELECT b.* FROM wishlist w JOIN books b ON b.id=w.book_id WHERE w.user_id=? ORDER BY w.id DESC", (session["user_id"],)).fetchall()
    return render_template("wishlist.html", books=rows)


@shop_bp.post("/wishlist/remove/<int:book_id>")
@login_required
def remove_wishlist(book_id):
    get_db().execute("DELETE FROM wishlist WHERE user_id=? AND book_id=?", (session["user_id"], book_id))
    get_db().commit()
    return redirect(url_for("shop.wishlist"))


@shop_bp.post("/book/<int:book_id>/review")
@login_required
def add_review(book_id):
    connection = get_db()
    bought = connection.execute("SELECT 1 FROM orders o JOIN order_items oi ON oi.order_id=o.id WHERE o.user_id=? AND oi.book_id=? AND o.status='Delivered'", (session["user_id"], book_id)).fetchone()
    try:
        rating = int(request.form.get("rating", "0"))
    except ValueError:
        rating = 0
    comment = request.form.get("comment", "").strip()[:2000]
    user = current_user()
    if not bought:
        flash("Reviews are available after a delivered purchase.", "error")
    elif not 1 <= rating <= 5 or not comment:
        flash("Choose a rating from 1 to 5 and enter a comment.", "error")
    else:
        try:
            connection.execute("INSERT INTO reviews(book_id,user_id,name,rating,comment) VALUES (?,?,?,?,?)", (book_id, user["id"], user["name"], rating, comment))
            connection.commit()
            flash("Thank you for reviewing this book.", "success")
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("You have already reviewed this book.", "error")
    return redirect(url_for("shop.book_detail", book_id=book_id))


