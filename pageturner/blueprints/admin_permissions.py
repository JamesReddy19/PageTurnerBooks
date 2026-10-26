"""Admin editor for the role/permission matrix (requirement 73).

Permission checks read ``role_permissions`` through :mod:`pageturner.permissions`,
so this page is the whole policy surface: what a ``staff`` account may reach is
a table edit here, not a code change and a deploy.

The ``admin`` role is intentionally not editable.  It always holds the full
catalogue (see ``permissions.SUPERUSER_ROLE``), which keeps the admin area
reachable no matter what the matrix says.
"""

from flask import flash, redirect, render_template, request, url_for

from .. import permissions as registry
from ..db import get_db
from ..helpers import log_admin_action, permission_required
from .admin import admin_bp

# Only the two operational roles are editable; customers get no admin grants.
EDITABLE_ROLES = ("staff", "customer")


def _roles(connection):
    """Roles that appear on the matrix, in display order."""
    known = {
        row["role"]
        for row in connection.execute(
            "SELECT DISTINCT role FROM users WHERE role IS NOT NULL"
        ).fetchall()
    }

    ordered = [registry.SUPERUSER_ROLE]

    for role in EDITABLE_ROLES:
        ordered.append(role)

    # Any role an operator invented by hand still gets a column.
    for role in sorted(known - set(ordered)):
        ordered.append(role)

    return ordered


@admin_bp.get("/permissions")
@permission_required("roles.manage")
def permission_matrix():
    connection = get_db()

    permissions = connection.execute(
        "SELECT code, description FROM permissions ORDER BY code"
    ).fetchall()

    granted = {
        (row["role"], row["permission_code"])
        for row in connection.execute(
            "SELECT role, permission_code FROM role_permissions"
        ).fetchall()
    }

    users_per_role = {
        row["role"]: row["count"]
        for row in connection.execute(
            "SELECT role, COUNT(*) AS count FROM users GROUP BY role"
        ).fetchall()
    }

    roles = _roles(connection)

    # The superuser holds everything; show it that way rather than showing an
    # empty row that implies it has no powers.
    for permission in permissions:
        granted.add((registry.SUPERUSER_ROLE, permission["code"]))

    return render_template(
        "admin_permissions.html",
        roles=roles,
        editable_roles=EDITABLE_ROLES,
        superuser_role=registry.SUPERUSER_ROLE,
        permissions=permissions,
        granted=granted,
        users_per_role=users_per_role,
    )


@admin_bp.post("/permissions")
@permission_required("roles.manage")
def update_permission_matrix():
    """Apply the submitted matrix.

    The form sends one ``grant`` value per *ticked* checkbox, formatted
    ``role:code``.  Because the page renders every pair, the submitted set is
    the complete desired state for the editable roles — so a single submit can
    both grant and revoke, and a role can be emptied.
    """
    connection = get_db()

    valid_permissions = {
        row["code"] for row in connection.execute("SELECT code FROM permissions")
    }

    submitted = set()

    for value in request.form.getlist("grant"):
        role, _, code = value.partition(":")

        # The superuser is not editable, so silently ignore stray values for it
        # instead of letting the form rewrite the catalogue.
        if role in EDITABLE_ROLES and code in valid_permissions:
            submitted.add((role, code))

    current = {
        (row["role"], row["permission_code"])
        for row in connection.execute(
            "SELECT role, permission_code FROM role_permissions WHERE role != ?",
            (registry.SUPERUSER_ROLE,),
        ).fetchall()
    }

    to_grant = sorted(submitted - current)
    to_revoke = sorted(current - submitted)

    try:
        # log_admin_action appends to the hash-chained audit log through this
        # same connection, so the change and its audit row commit together.
        if to_grant:
            connection.executemany(
                """
                INSERT OR IGNORE INTO role_permissions (role, permission_code)
                VALUES (?, ?)
                """,
                to_grant,
            )

        if to_revoke:
            connection.executemany(
                """
                DELETE FROM role_permissions
                WHERE role = ? AND permission_code = ?
                """,
                to_revoke,
            )

        log_admin_action(
            "permissions.update",
            f"granted={len(to_grant)} revoked={len(to_revoke)}",
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    count = len(to_grant) + len(to_revoke)

    if count:
        flash(
            f"Permissions updated: {len(to_grant)} granted, "
            f"{len(to_revoke)} revoked. The next request obeys the new matrix.",
            "success",
        )
    else:
        flash("No permission changes to save.", "error")

    return redirect(url_for("admin.permission_matrix"))
