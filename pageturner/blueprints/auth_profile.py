"""Customer profile and signed-in device sessions."""
import hashlib
from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from ..db import get_db
from ..helpers import current_user, login_required
from .auth import auth_bp

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    connection = get_db()
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        if not check_password_hash(user["password_hash"], current_password):
            flash("Your current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("The new password must contain at least 8 characters.", "error")
        else:
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user["id"]))
            connection.commit()
            flash("Your password has been changed.", "success")
            return redirect(url_for("auth.profile"))
    stats = connection.execute("SELECT COUNT(*) AS order_count, COALESCE(SUM(total_paise),0) AS spent FROM orders WHERE user_id = ?", (user["id"],)).fetchone()
    return render_template("profile.html", stats=stats)


@auth_bp.get("/devices")
@login_required
def devices():
    rows = get_db().execute("SELECT * FROM user_sessions WHERE user_id = ? ORDER BY id DESC", (session["user_id"],)).fetchall()
    return render_template("devices.html", devices=rows)


@auth_bp.post("/devices/revoke/<int:session_id>")
@login_required
def revoke_device(session_id):
    connection = get_db()
    connection.execute("UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?", (session_id, session["user_id"]))
    connection.commit()
    flash("That device session has been revoked.", "success")
    return redirect(url_for("auth.devices"))


@auth_bp.post("/devices/revoke-all")
@login_required
def revoke_all_devices():
    connection = get_db()
    connection.execute("UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = ?", (session["user_id"],))
    connection.commit()
    session.clear()
    flash("All device sessions have been revoked. Please log in again.", "success")
    return redirect(url_for("auth.login"))
