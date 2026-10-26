"""Staff and administrator order management."""
from flask import abort, flash, redirect, request, session, url_for, render_template
from ..db import get_db
from ..helpers import log_admin_action, add_notification
from .admin import admin_bp, ALLOWED_STATUSES

@admin_bp.get("/orders")
@permission_required("orders.manage")
def orders():
    rows = get_db().execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    return render_template("orders.html", orders=rows, transitions=ALLOWED_STATUSES)


@admin_bp.post("/orders/<int:order_id>/status")
@permission_required("orders.manage")
def update_order_status(order_id):
    new_status = request.form.get("status", "")
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            abort(404)
        old_status = order["status"]
        if new_status not in ALLOWED_STATUSES.get(old_status, set()):
            raise ValueError(f"The status cannot change from {old_status} to {new_status}.")
        changed = connection.execute("UPDATE orders SET status=? WHERE id=? AND status=?", (new_status,order_id,old_status)).rowcount
        if changed != 1:
            raise ValueError("The order status changed in another request. Refresh and try again.")
        if new_status == "Cancelled":
            for item in connection.execute("SELECT book_id,quantity FROM order_items WHERE order_id=?", (order_id,)).fetchall():
                connection.execute("UPDATE books SET stock=stock+? WHERE id=?", (item["quantity"], item["book_id"]))
                connection.execute("INSERT INTO stock_log(book_id,change_amount,reason,order_id,actor_user_id) VALUES (?,?,?,?,?)", (item["book_id"],item["quantity"],"admin cancellation",order_id,session.get("user_id")))
                title = connection.execute("SELECT title FROM books WHERE id=?", (item["book_id"],)).fetchone()["title"]
                for wish in connection.execute("SELECT user_id FROM wishlist WHERE book_id=?", (item["book_id"],)).fetchall():
                    add_notification(wish["user_id"], f"{title} is back in stock.", url_for("shop.book_detail", book_id=item["book_id"]))
        connection.execute("INSERT INTO order_status_history(order_id,old_status,new_status,changed_by) VALUES (?,?,?,?)", (order_id,old_status,new_status,session.get("user_id")))
        add_notification(order["user_id"], f"Order #{order_id} status changed to {new_status}.", url_for("shop.order_detail", order_id=order_id))
        log_admin_action("order_status_changed", f"Order {order_id}: {old_status} -> {new_status}")
        connection.commit()
        flash("Order status updated.", "success")
    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("admin.orders"))




@admin_bp.post("/order-status/<int:order_id>")
@permission_required("orders.manage")
def old_order_status_url(order_id):
    return redirect(url_for("admin.update_order_status", order_id=order_id), code=307)
