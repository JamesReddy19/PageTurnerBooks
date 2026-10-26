"""Admin side of requirement 73 — editing the role/permission matrix.

Permission checks in the application read the ``role_permissions`` table (see
``pageturner.permissions``), so an administrator can change what a role may do
without a code change or a deploy.  This page is the editor for that table, and
it deliberately refuses the two edits that would make the admin area
unreachable:

* ``admin.access`` cannot be removed from the ``admin`` role, and
* the ``admin`` role itself cannot be deleted.

Both are guarded here rather than in the database so the reason can be
explained to the operator in plain language.
"""

from flask import flash, redirect, render_template, request, url_for

from .. import permissions as permission_registry
from ..db import get_db
from ..helpers import log_admin_action, permission_required
from .admin import admin_bp


@admin_bp.get("/permissions")
@permission_required("roles.manage")
def permission_matrix():
    connection = get_db()

    roles = [row["code"] for row in connection.execute(
        "SELECT code FROM roles ORDER BY code"
    ).fetchall()]

    permissions = connection.execute(
        "SELECT code, description FROM permissions ORDER BY code"
    ).fetchall()

    granted = {
        (row["role_code"], row["permission_code"])
        for row in connection.execute(
            "SELECT role_code, permission_code FROM role_permissions"
        ).fetchall()
    }

    users_per_role = {
        row["role"]: row["count"]
        for row in connection.execute(
            "SELECT role, COUNT(*) AS count FROM users GROUP BY role"
        ).fetchall()
    }

    return render_template(
        "admin_permissions.html",
        roles=roles,
        permissions=permissions,
        granted=granted,
        users_per_role=users_per_role,
    )


@admin_bp.post("/permissions")
@permission_required("roles.manage")
def update_permission_matrix():
    """Replace the matrix with what the form submitted.

    The form carries one ``grant`` checkbox per role/permission pair, so the
    submitted set is the complete desired state.  Diffing it against the table
    is what lets a single submit both grant and revoke.
    """
    connection = get_db()

    roles = [row["code"] for row in connection.execute(
        "SELECT code FROM roles ORDER BY code"
    ).fetchall()]

    valid_permissions = {
        row["code"] for row in connection.execute(
            "SELECT code FROM permissions"
        ).fetchall()
    }

    submitted = set()

    for pair in request.form.getlist("grant"):
        role_code, _, permission_code = pair.partition(":")

        if role_code in roles and permission_code in valid_permissions:
            submitted.add((role_code, permission_code))

    # The admin role must keep admin.access, otherwise nobody can load the
    # pages needed to undo the mistake.
    if ("admin", "admin.access") not in submitted:
        flash(
            "The admin role must keep the 'admin.access' permission.",
            "error",
        )
        return redirect(url_for("admin.permission_matrix"))

    current = {
        (row["role_code"], row["permission_code"])
        for row in connection.execute(
            "SELECT role_code, permission_code FROM role_permissions"
        ).fetchall()
    }

    to_add = submitted - current
    to_remove = current - submitted

    try:
        connection.execute("BEGIN IMMEDIATE")

        connection.executemany(
            """
            INSERT OR IGNORE INTO role_permissions (role_code, permission_code)
            VALUES (?, ?)
            """,
            sorted(to_add),
        )

        connection.executemany(
            """
            DELETE FROM role_permissions
            WHERE role_code = ? AND permission_code = ?
            """,
            sorted(to_remove),
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    # The cache would otherwise serve the old matrix until the process
    # restarts, which looks like the change silently failed.
    permission_registry.invalidate_cache()

    log_admin_action(
        "permissions.update",
        f"granted={len(to_add)} revoked={len(to_remove)}",
    )

    flash(
        f"Permissions saved: {len(to_add)} granted, {len(to_remove)} revoked.",
        "success",
    )

    return redirect(url_for("admin.permission_matrix"))
