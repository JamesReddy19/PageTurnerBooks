"""Return approvals and contact-message moderation."""
from flask import abort, flash, redirect, request, session, url_for, render_template
from ..db import get_db
from ..helpers import role_required, log_admin_action, add_notification, audit_action, loyalty_claw_back_for_refund
from .admin import admin_bp

@admin_bp.get("/returns")
@role_required("admin")
def returns():
    rows = get_db().execute("SELECT r.*,oi.order_id,oi.book_id,b.title,u.name AS customer_name FROM returns r JOIN order_items oi ON oi.id=r.order_item_id JOIN books b ON b.id=oi.book_id JOIN users u ON u.id=r.user_id ORDER BY r.id DESC").fetchall()
    return render_template("returns.html", returns=rows)


@admin_bp.post("/returns/<int:return_id>/<decision>")
@role_required("admin")
@audit_action("return_reviewed")
def review_return(return_id, decision):
    if decision not in ("approve", "reject"):
        abort(404)
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        returned = connection.execute("SELECT r.*,oi.order_id,oi.book_id,oi.price_paise FROM returns r JOIN order_items oi ON oi.id=r.order_item_id WHERE r.id=?", (return_id,)).fetchone()
        if not returned:
            abort(404)
        if returned["status"] != "Pending":
            raise ValueError("This return has already been reviewed.")
        status = "Approved" if decision == "approve" else "Rejected"
        connection.execute("UPDATE returns SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='Pending'", (status,session.get("user_id"),return_id))
        if status == "Approved":
            connection.execute("UPDATE order_items SET returned_quantity=returned_quantity+? WHERE id=?", (returned["quantity"],returned["order_item_id"]))
            connection.execute("UPDATE books SET stock=stock+? WHERE id=?", (returned["quantity"],returned["book_id"]))
            connection.execute("INSERT INTO stock_log(book_id,change_amount,reason,order_id,actor_user_id) VALUES (?,?,?,?,?)", (returned["book_id"],returned["quantity"],"approved return",returned["order_id"],session.get("user_id")))

            # Requirement 49: points were earned on the refunded value, so take
            # them back proportionally. price_paise is the per-copy price that
            # was actually charged (requirement 47), so this matches the refund.
            refunded_paise = returned["price_paise"] * returned["quantity"]
            loyalty_claw_back_for_refund(
                connection, returned["user_id"], returned["order_id"], refunded_paise
            )
            title = connection.execute("SELECT title FROM books WHERE id=?", (returned["book_id"],)).fetchone()["title"]
            for wish in connection.execute("SELECT user_id FROM wishlist WHERE book_id=?", (returned["book_id"],)).fetchall():
                add_notification(wish["user_id"], f"{title} is back in stock.", url_for("shop.book_detail", book_id=returned["book_id"]))
        add_notification(returned["user_id"], f"Your return request #{return_id} was {status.lower()}.", url_for("shop.order_detail", order_id=returned["order_id"]))
        log_admin_action("return_reviewed", f"Return {return_id}: {status}")
        connection.commit()
    except ValueError as error:
        connection.rollback()
        flash(str(error), "error")
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("admin.returns"))


@admin_bp.get("/messages")
@role_required("admin", "staff")
def messages():
    rows = get_db().execute("SELECT * FROM contact_messages ORDER BY is_read,id DESC").fetchall()
    return render_template("messages.html", messages=rows)


@admin_bp.post("/messages/<int:message_id>/read")
@role_required("admin", "staff")
@audit_action("contact_message_read")
def mark_message_read(message_id):
    get_db().execute("UPDATE contact_messages SET is_read=1 WHERE id=?", (message_id,))
    get_db().commit()
    return redirect(url_for("admin.messages"))
