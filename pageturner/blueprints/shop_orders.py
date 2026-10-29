"""Order history, tracking, cancellation, invoices and returns."""
from flask import abort, flash, redirect, render_template, request, session, url_for
from ..db import get_db
from ..payments import payment_for_order
from ..helpers import current_user, login_required, add_notification, ORDER_STATUS_TRANSITIONS
from .shop import shop_bp

@shop_bp.route("/order-history", methods=["GET", "POST"])
@login_required
def order_history():
    if request.method == "POST":
        flash("Order history is now linked securely to your account. Your own orders are listed below.", "success")
    rows = get_db().execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    return render_template("order_history.html", orders=rows)


@shop_bp.get("/order/<int:order_id>")
@login_required
def order_detail(order_id):
    connection = get_db()
    order = connection.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    user = current_user()
    if order["user_id"] != user["id"] and user["role"] not in ("admin", "staff"):
        abort(404)
    items = connection.execute("SELECT oi.*,b.title,b.author,b.image FROM order_items oi JOIN books b ON b.id=oi.book_id WHERE oi.order_id=?", (order_id,)).fetchall()
    history = connection.execute("SELECT * FROM order_status_history WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    payment = payment_for_order(connection, order_id)
    return render_template("order.html", order=order, items=items, history=history, payment=payment)


@shop_bp.post("/order/<int:order_id>/cancel")
@login_required
def cancel_order(order_id):
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, session["user_id"])).fetchone()
        if not order:
            abort(404)
        if "Cancelled" not in ORDER_STATUS_TRANSITIONS.get(order["status"], set()):
            raise ValueError("Only orders with status Placed can be cancelled.")
        connection.execute("UPDATE orders SET status='Cancelled' WHERE id=? AND status='Placed'", (order_id,))
        items = connection.execute("SELECT book_id,quantity FROM order_items WHERE order_id=?", (order_id,)).fetchall()
        for item in items:
            connection.execute("UPDATE books SET stock=stock+? WHERE id=?", (item["quantity"],item["book_id"]))
            connection.execute("INSERT INTO stock_log(book_id,change_amount,reason,order_id,actor_user_id) VALUES (?,?,?,?,?)", (item["book_id"],item["quantity"],"order cancellation",order_id,session["user_id"]))
            for wish in connection.execute("SELECT user_id FROM wishlist WHERE book_id=?", (item["book_id"],)).fetchall():
                add_notification(wish["user_id"], "A wishlisted book is back in stock.", url_for("shop.book_detail", book_id=item["book_id"]))
        connection.execute("INSERT INTO order_status_history(order_id,old_status,new_status,changed_by,note) VALUES (?, 'Placed','Cancelled',?,'Cancelled by customer')", (order_id,session["user_id"]))
        connection.commit()
        flash("Order cancelled and stock restored.", "success")
    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("shop.order_detail", order_id=order_id))


@shop_bp.get("/order/<int:order_id>/invoice")
@login_required
def invoice(order_id):
    connection = get_db()
    order = connection.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    user = current_user()
    if not order:
        abort(404)
    if order["user_id"] != user["id"] and user["role"] not in ("admin", "staff"):
        abort(404)
    items = connection.execute("SELECT oi.*,b.title FROM order_items oi JOIN books b ON b.id=oi.book_id WHERE oi.order_id=?", (order_id,)).fetchall()
    return render_template("invoice.html", order=order, items=items)


@shop_bp.post("/order-item/<int:item_id>/return")
@login_required
def request_return(item_id):
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        item = connection.execute("SELECT oi.*,o.user_id,o.status FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE oi.id=?", (item_id,)).fetchone()
        if not item or item["user_id"] != session["user_id"]:
            abort(404)
        try:
            quantity = int(request.form.get("quantity", "0"))
        except ValueError:
            quantity = 0
        reason = request.form.get("reason", "").strip()[:500]
        pending = connection.execute("SELECT COALESCE(SUM(quantity),0) AS amount FROM returns WHERE order_item_id=? AND status='Pending'", (item_id,)).fetchone()["amount"]
        available = item["quantity"] - item["returned_quantity"] - pending
        if item["status"] != "Delivered" or quantity < 1 or quantity > available or not reason:
            raise ValueError("Returns require a delivered order and a valid quantity/reason.")
        connection.execute("INSERT INTO returns(order_item_id,user_id,quantity,reason) VALUES (?,?,?,?)", (item_id,session["user_id"],quantity,reason))
        connection.commit()
        flash("Return request submitted for review.", "success")
        order_id = item["order_id"]
    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
        row = connection.execute("SELECT order_id FROM order_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            abort(404)
        order_id = row["order_id"]
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("shop.order_detail", order_id=order_id))
