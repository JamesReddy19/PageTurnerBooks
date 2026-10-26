
"""Role-based permissions stored as data (requirement 73).

Historically every admin route carried ``@role_required("admin")`` or
``@staff_or_admin``.  That hard-codes who may do what in the source: changing
what a staff member can reach needs a code change and a deploy.

Here the grants live in two tables.  ``permissions`` is the catalogue of things
that can be done, ``role_permissions`` says which role may do them.  Routes ask
``permission_required("orders.manage")`` and the answer comes from the database,
so an operator can grant or revoke a capability for the ``staff`` role with a
single INSERT/DELETE and the next request obeys it.

Seeding rule: a role's defaults are inserted only when that role has no rows at
all.  That makes the very first boot populate the matrix, while a deliberate
``DELETE FROM role_permissions WHERE role='staff' AND permission_code=...``
survives later boots instead of being silently restored.
"""

from flask import g

from .db import get_db


# The catalogue.  Adding a code here is what makes it grantable; the
# description is shown on the admin matrix page.
DEFAULT_PERMISSIONS = {
    "admin.access": "Open the admin dashboard",
    "catalog.manage": "Create, edit, import and delete catalogue entries",
    "pricing.manage": "Manage coupons, bundles and sale prices",
    "pricing.rules.manage": "Manage automatic pricing rules",
    "stock.manage": "Manage backorders and stock allocation",
    "orders.manage": "Fulfil orders and answer customer messages",
    "returns.manage": "Approve or reject refunds",
    "reviews.moderate": "Hide or keep customer reviews",
    "requests.manage": "Fulfil customer book requests",
    "outbox.manage": "Process the notification outbox",
    "subscriptions.manage": "Choose the book of the month and run subscriptions",
    "reports.view": "View and export sales reports",
    "customers.manage": "Manage customer accounts and roles",
    "audit.view": "Read the audit trail",
    "roles.manage": "Grant and revoke permissions for each role",
    "keys.manage": "Revoke any customer's API keys",
}


# The ``admin`` role is the superuser and always holds the whole catalogue.
# Two reasons to special-case it in code rather than in the table:
#
#   * bootstrapping - a brand new permission code is immediately usable by an
#     admin without a migration that back-fills the grant, and
#   * lockout safety - no edit to ``role_permissions`` can make the admin area
#     unreachable, which is otherwise an unrecoverable state through the UI.
#
# Every other role is entirely data-driven, which is the point of the
# requirement.
SUPERUSER_ROLE = "admin"


# What each role may do out of the box.  This reproduces the pre-refactor
# behaviour exactly: staff already could fulfil orders, moderate reviews,
# answer messages, handle book requests, run the outbox/subscriptions, manage
# pricing rules and allocate backorders - and nothing else.
DEFAULT_ROLE_PERMISSIONS = {
    "admin": set(DEFAULT_PERMISSIONS),
    "staff": {
        "orders.manage",
        "reviews.moderate",
        "requests.manage",
        "outbox.manage",
        "subscriptions.manage",
        "pricing.rules.manage",
        "stock.manage",
    },
    "customer": set(),
}


def sync_default_permissions(connection):
    """Create the permission catalogue and per-role defaults (idempotent).

    Called from ``init_db`` so a fresh database and an upgraded one both end up
    with a usable matrix.
    """
    for code, description in DEFAULT_PERMISSIONS.items():
        connection.execute(
            """
            INSERT INTO permissions(code, description)
            VALUES (?, ?)
            ON CONFLICT(code) DO UPDATE SET description = excluded.description
            """,
            (code, description),
        )

    for role, codes in DEFAULT_ROLE_PERMISSIONS.items():
        existing = connection.execute(
            "SELECT COUNT(*) AS count FROM role_permissions WHERE role = ?",
            (role,),
        ).fetchone()["count"]

        if existing:
            # The operator owns this role's grants now; leave them alone.
            continue

        for code in sorted(codes):
            connection.execute(
                """
                INSERT OR IGNORE INTO role_permissions(role, permission_code)
                VALUES (?, ?)
                """,
                (role, code),
            )


def permissions_for_role(role):
    """Return the set of permission codes granted to ``role``.

    Cached on ``g`` for the life of the request so a page that checks a dozen
    permissions costs one query.
    """
    if not role:
        return set()

    cache = getattr(g, "_permission_cache", None)

    if cache is None:
        cache = {}
        g._permission_cache = cache

    if role not in cache:
        connection = get_db()

        if role == SUPERUSER_ROLE:
            rows = connection.execute("SELECT code FROM permissions").fetchall()
        else:
            rows = connection.execute(
                "SELECT permission_code AS code FROM role_permissions WHERE role = ?",
                (role,),
            ).fetchall()

        cache[role] = {row["code"] for row in rows}

    return cache[role]


def role_can(role, code):
    return code in permissions_for_role(role)


def has_permission(user, code):
    """True when the signed-in ``user`` row holds ``code``."""
    if user is None:
        return False

    return role_can(user["role"], code)


def grant(connection, role, code):
    connection.execute(
        """
        INSERT OR IGNORE INTO role_permissions(role, permission_code)
        VALUES (?, ?)
        """,
        (role, code),
    )


def revoke(connection, role, code):
    connection.execute(
        "DELETE FROM role_permissions WHERE role = ? AND permission_code = ?",
        (role, code),
    )


def matrix(connection):
    """Return the role x permission grid for the admin page."""
    codes = [
        row["code"]
        for row in connection.execute(
            "SELECT code FROM permissions ORDER BY code"
        ).fetchall()
    ]

    granted = {}

    for row in connection.execute(
        "SELECT role, permission_code FROM role_permissions"
    ).fetchall():
        granted.setdefault(row["role"], set()).add(row["permission_code"])

    return codes, granted
