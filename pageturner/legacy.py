"""Compatibility routes that keep the original project's URLs working."""
from flask import abort, redirect, session, url_for
from .helpers import current_user, login_required


def register_legacy_routes(app):
    @app.get("/orders")
    def old_orders_url():
        user = current_user()
        if not (user and user["role"] in ("admin", "staff")) and not session.get("admin_password_login"):
            abort(403)
        return redirect(url_for("admin.orders"))

    @app.post("/apply-coupon")
    def old_apply_coupon_url():
        return redirect(url_for("shop.apply_coupon"), code=307)

    @app.post("/remove-coupon")
    def old_remove_coupon_url():
        return redirect(url_for("shop.remove_coupon"), code=307)

    @app.post("/wishlist/add/<int:book_id>")
    @login_required
    def old_wishlist_add_url(book_id):
        return redirect(url_for("shop.toggle_wishlist", book_id=book_id), code=307)

    @app.post("/addresses/add")
    @login_required
    def old_address_add_url():
        return redirect(url_for("auth.addresses"), code=307)
