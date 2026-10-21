"""Saved customer address management."""
import sqlite3
from flask import abort, flash, redirect, render_template, request, session, url_for
from ..db import get_db
from ..helpers import current_user, login_required
from .auth import auth_bp

@auth_bp.route("/addresses", methods=["GET", "POST"])
@login_required
def addresses():
    connection = get_db()
    user_id = session["user_id"]
    if request.method == "POST":
        label = request.form.get("label", "Home").strip()[:40]
        line1 = request.form.get("line1", "").strip()[:250]
        city = request.form.get("city", "").strip()[:80]
        pincode = request.form.get("pincode", "").strip()
        is_default = 1 if request.form.get("is_default") else 0
        if not label or not line1 or not city or not pincode.isdigit() or len(pincode) != 6:
            flash("Enter an address, city and a valid 6-digit pincode.", "error")
        else:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if is_default:
                    connection.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
                connection.execute("INSERT INTO addresses(user_id,label,line1,city,pincode,is_default) VALUES (?,?,?,?,?,?)", (user_id,label,line1,city,pincode,is_default))
                connection.commit()
                flash("Address saved.", "success")
                return redirect(url_for("auth.addresses"))
            except sqlite3.IntegrityError:
                connection.rollback()
                flash("Could not save this address.", "error")
    rows = connection.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC", (user_id,)).fetchall()
    return render_template("addresses.html", addresses=rows)


@auth_bp.post("/addresses/edit/<int:address_id>")
@login_required
def edit_address(address_id):
    connection = get_db()
    label = request.form.get("label", "").strip()[:40]
    line1 = request.form.get("line1", "").strip()[:250]
    city = request.form.get("city", "").strip()[:80]
    pincode = request.form.get("pincode", "").strip()
    is_default = 1 if request.form.get("is_default") else 0
    if not label or not line1 or not city or not pincode.isdigit() or len(pincode) != 6:
        flash("Enter an address, city and a valid 6-digit pincode.", "error")
        return redirect(url_for("auth.addresses"))
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT id FROM addresses WHERE id=? AND user_id=?", (address_id, session["user_id"])).fetchone()
        if not existing:
            connection.rollback()
            abort(404)
        if is_default:
            connection.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (session["user_id"],))
        connection.execute("UPDATE addresses SET label=?,line1=?,city=?,pincode=?,is_default=? WHERE id=? AND user_id=?", (label,line1,city,pincode,is_default,address_id,session["user_id"]))
        connection.commit()
        flash("Address updated.", "success")
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("auth.addresses"))


@auth_bp.post("/addresses/default/<int:address_id>")
@login_required
def default_address(address_id):
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute("SELECT id FROM addresses WHERE id = ? AND user_id = ?", (address_id, session["user_id"])).fetchone()
        if not exists:
            connection.rollback()
            abort(404)
        connection.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (session["user_id"],))
        connection.execute("UPDATE addresses SET is_default = 1 WHERE id = ? AND user_id = ?", (address_id, session["user_id"]))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("auth.addresses"))


@auth_bp.post("/addresses/delete/<int:address_id>")
@login_required
def delete_address(address_id):
    connection = get_db()
    connection.execute("DELETE FROM addresses WHERE id = ? AND user_id = ?", (address_id, session["user_id"]))
    connection.commit()
    flash("Address removed if it belonged to your account.", "success")
    return redirect(url_for("auth.addresses"))
