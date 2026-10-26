"""Administrator user-role management (staff appointment).

Admins can promote a customer to staff so they can process orders and
returns, and can demote staff back to customer. Guard rails:

* only admins can reach these routes;
* an admin cannot change their own role (no accidental self-lockout);
* the last remaining admin cannot be demoted.
"""
from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..helpers import current_user, log_admin_action, role_required
from .admin import admin_bp

ASSIGNABLE_ROLES = ("customer", "staff", "admin")


@admin_bp.get("/users")
@permission_required("customers.manage")
def users():
    rows = get_db().execute(
        """
        SELECT u.id, u.name, u.email, u.role, u.created_at,
               (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS order_count
        FROM users u
        ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'staff' THEN 1 ELSE 2 END,
                 u.name COLLATE NOCASE
        """
    ).fetchall()
    return render_template("admin_users.html", users=rows, roles=ASSIGNABLE_ROLES)


@admin_bp.post("/users/<int:user_id>/role")
@permission_required("customers.manage")
def set_user_role(user_id):
    new_role = (request.form.get("role") or "").strip().lower()
    if new_role not in ASSIGNABLE_ROLES:
        flash("Unknown role.", "error")
        return redirect(url_for("admin.users"))

    connection = get_db()
    target = connection.execute(
        "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if target is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))

    actor = current_user()
    if actor and actor["id"] == target["id"]:
        flash("You cannot change your own role.", "error")
        return redirect(url_for("admin.users"))

    if target["role"] == new_role:
        flash(f"{target['name']} is already {new_role}.", "info")
        return redirect(url_for("admin.users"))

    if target["role"] == "admin" and new_role != "admin":
        remaining_admins = connection.execute(
            "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND id <> ?",
            (target["id"],),
        ).fetchone()["total"]
        if remaining_admins == 0:
            flash("At least one administrator must remain.", "error")
            return redirect(url_for("admin.users"))

    connection.execute(
        "UPDATE users SET role = ? WHERE id = ?", (new_role, target["id"])
    )
    log_admin_action(
        "user_role_changed",
        f"user={target['email']} from={target['role']} to={new_role}",
    )
    connection.commit()
    flash(f"{target['name']} is now {new_role}.", "success")
    return redirect(url_for("admin.users"))
