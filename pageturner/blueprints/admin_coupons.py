"""Administrator coupon management."""
import sqlite3
from datetime import datetime
from flask import flash, redirect, request, url_for, render_template
from ..db import get_db
from ..helpers import log_admin_action, rupees_to_paise, permission_required
from .admin import admin_bp

@admin_bp.get("/coupons")
@permission_required("pricing.manage")
def coupons():
    rows = get_db().execute("SELECT cp.*,c.name AS category_name FROM coupons cp LEFT JOIN categories c ON c.id=cp.category_id ORDER BY cp.id DESC").fetchall()
    categories = get_db().execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template("coupons.html", coupons=rows, categories=categories)


@admin_bp.post("/coupons/add")
@permission_required("pricing.manage")
def add_coupon():
    connection = get_db()
    code = request.form.get("code", "").strip().upper()[:40]
    expiry = request.form.get("expiry_date", "")
    try:
        percent = int(request.form.get("percent", "0"))
        minimum = rupees_to_paise(request.form.get("min_order", "0"))
        max_text = request.form.get("max_discount", "").strip()
        maximum = rupees_to_paise(max_text) if max_text else None
        limit_text = request.form.get("usage_limit", "").strip()
        limit = int(limit_text) if limit_text else None
        category_text = request.form.get("category_id", "").strip()
        category_id = int(category_text) if category_text else None
        datetime.strptime(expiry, "%Y-%m-%d")
        if not code or not 1 <= percent <= 100 or (limit is not None and limit < 1):
            raise ValueError("Enter a code, 1–100 percent, valid expiry and positive optional limit.")
        connection.execute("INSERT INTO coupons(code,percent,expiry_date,min_order_paise,max_discount_paise,usage_limit,category_id) VALUES (?,?,?,?,?,?,?)", (code,percent,expiry,minimum,maximum,limit,category_id))
        log_admin_action("coupon_added", code)
        connection.commit()
        flash("Coupon created.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        connection.rollback()
        flash(str(error) if isinstance(error, ValueError) else "That coupon code already exists.", "error")
    return redirect(url_for("admin.coupons"))


@admin_bp.post("/coupons/toggle/<int:coupon_id>")
@permission_required("pricing.manage")
def toggle_coupon(coupon_id):
    connection = get_db()
    connection.execute("UPDATE coupons SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (coupon_id,))
    log_admin_action("coupon_toggled", coupon_id)
    connection.commit()
    return redirect(url_for("admin.coupons"))


@admin_bp.post("/coupons/delete/<int:coupon_id>")
@permission_required("pricing.manage")
def delete_coupon(coupon_id):
    try:
        get_db().execute("DELETE FROM coupons WHERE id=?", (coupon_id,))
        get_db().commit()
    except sqlite3.IntegrityError:
        get_db().rollback()
        flash("A coupon used by an order cannot be deleted; deactivate it instead.", "error")
    return redirect(url_for("admin.coupons"))
