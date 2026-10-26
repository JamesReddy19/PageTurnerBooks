"""Tamper-evident hash chain for the admin audit log (requirement 68).

Every ``admin_log`` row carries ``prev_hash`` and ``row_hash``.  ``row_hash``
is the SHA-256 of a deterministic canonical serialization of the row content
concatenated with the previous row's hash, so any change to a stored row, or
any inserted/deleted/reordered row, breaks the chain at a detectable point.

Chain initialization (documented deliberately)
-----------------------------------------------
Rows written before this feature existed have no hash material at all, so
they cannot be verified.  When the additive migration adds the columns,
``seal_chain()`` runs exactly once and hashes every legacy row in ``id``
order, starting from ``GENESIS_HASH``.  That sealed prefix is the trusted
origin of the chain: tampering that happened *before* the upgrade can never
be proven, and is out of scope.  Sealing only ever touches rows whose
``row_hash`` is still NULL, so it is idempotent and can never be used to
regenerate a hash that was already written (which would conceal tampering).

``audit_chain_head`` stores the tip of the chain so that deleting trailing
rows (which leaves an otherwise valid prefix) is still detected.  Sealing
never rewrites an existing head pointer.

Limitation: a plain SHA-256 chain detects accidental corruption and any
tampering by someone who only edits/deletes rows in place.  An attacker who
can rewrite the whole table *and* the head pointer can forge a new chain;
detecting that requires external anchoring, which this application does not
provide.
"""

import hashlib
import json

GENESIS_HASH = hashlib.sha256(b"pageturner:admin_log:genesis:v1").hexdigest()

ROW_FIELDS = ("id", "actor_user_id", "action", "detail", "created_at")


def canonical_payload(row):
    """Deterministic serialization of the fields covered by the hash.

    ``sort_keys`` plus a compact separator keeps the byte string identical
    across runs and processes, which is what makes verification meaningful.
    """
    payload = {}
    for field in ROW_FIELDS:
        value = row[field]
        payload[field] = "" if value is None else str(value)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_row_hash(prev_hash, row):
    material = "%s\n%s" % (prev_hash or GENESIS_HASH, canonical_payload(row))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _row_query(connection, where="", params=()):
    return connection.execute(
        "SELECT id, actor_user_id, action, detail, created_at, prev_hash, row_hash "
        "FROM admin_log %s ORDER BY id" % where,
        params,
    ).fetchall()


def _head(connection):
    return connection.execute(
        "SELECT last_row_id, last_hash FROM audit_chain_head WHERE id = 1"
    ).fetchone()


def _set_head(connection, last_row_id, last_hash):
    connection.execute(
        "INSERT INTO audit_chain_head(id, last_row_id, last_hash, updated_at) "
        "VALUES (1, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET "
        "last_row_id = excluded.last_row_id, "
        "last_hash = excluded.last_hash, "
        "updated_at = excluded.updated_at",
        (last_row_id, last_hash),
    )


def append_row(connection, actor_user_id, action, detail):
    """Append one audit row with its chain hashes; returns the new row id."""
    last = connection.execute(
        "SELECT id, row_hash FROM admin_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if last is None:
        prev_hash = GENESIS_HASH
    elif last["row_hash"]:
        prev_hash = last["row_hash"]
    else:
        # An unhashed row can only appear when migrations did not run
        # (init_db seals it).  Refuse to invent history silently.
        raise RuntimeError(
            "admin_log contains unsealed legacy rows; run the database "
            "migration before writing new audit entries."
        )

    cursor = connection.execute(
        "INSERT INTO admin_log(actor_user_id, action, detail) VALUES (?, ?, ?)",
        (actor_user_id, action, detail),
    )
    row_id = cursor.lastrowid

    row = connection.execute(
        "SELECT id, actor_user_id, action, detail, created_at "
        "FROM admin_log WHERE id = ?",
        (row_id,),
    ).fetchone()
    row_hash = compute_row_hash(prev_hash, row)

    connection.execute(
        "UPDATE admin_log SET prev_hash = ?, row_hash = ? WHERE id = ?",
        (prev_hash, row_hash, row_id),
    )
    _set_head(connection, row_id, row_hash)
    return row_id


def seal_chain(connection):
    """Hash legacy rows (row_hash IS NULL) in id order.  Returns count sealed."""
    rows = _row_query(connection)
    prev_hash = GENESIS_HASH
    sealed = 0

    for row in rows:
        if row["row_hash"]:
            prev_hash = row["row_hash"]
            continue

        row_hash = compute_row_hash(prev_hash, row)
        connection.execute(
            "UPDATE admin_log SET prev_hash = ?, row_hash = ? WHERE id = ?",
            (prev_hash, row_hash, row["id"]),
        )
        prev_hash = row_hash
        sealed += 1

    last_row_id = rows[-1]["id"] if rows else 0

    if _head(connection) is None:
        _set_head(connection, last_row_id, prev_hash)

    return sealed


def verify_chain(connection):
    """Walk the chain and report the first broken row.

    Detects: changed row content, inserted rows, deleted middle rows,
    reordered rows and truncated tails (via the stored head pointer).
    """
    rows = _row_query(connection)
    prev_hash = GENESIS_HASH
    last_row_id = 0

    for row in rows:
        if not row["row_hash"]:
            return {
                "ok": False,
                "checked": len(rows),
                "first_broken_row_id": row["id"],
                "reason": (
                    "Row %d has no stored hash (unsealed or hash columns "
                    "were cleared)." % row["id"]
                ),
            }

        if (row["prev_hash"] or "") != prev_hash:
            return {
                "ok": False,
                "checked": len(rows),
                "first_broken_row_id": row["id"],
                "reason": (
                    "Chain link broken at row %d: its prev_hash does not "
                    "match the previous row (a row was inserted, deleted "
                    "or reordered)." % row["id"]
                ),
            }

        expected = compute_row_hash(prev_hash, row)
        if expected != row["row_hash"]:
            return {
                "ok": False,
                "checked": len(rows),
                "first_broken_row_id": row["id"],
                "reason": (
                    "Row %d content has been modified (stored hash does not "
                    "match its content)." % row["id"]
                ),
            }

        prev_hash = row["row_hash"]
        last_row_id = row["id"]

    head = _head(connection)

    if head is not None and (
        head["last_row_id"] != last_row_id or head["last_hash"] != prev_hash
    ):
        return {
            "ok": False,
            "checked": len(rows),
            "first_broken_row_id": head["last_row_id"] or last_row_id,
            "reason": (
                "Chain head mismatch: the recorded tip (row %s) is not the "
                "tip of the stored chain (row %s), so trailing rows were "
                "removed or the head was rewritten."
                % (head["last_row_id"], last_row_id)
            ),
        }

    return {
        "ok": True,
        "checked": len(rows),
        "first_broken_row_id": None,
        "reason": "Chain verified: %d audit rows intact." % len(rows),
    }
