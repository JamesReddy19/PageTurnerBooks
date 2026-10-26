"""Requirement 65 — backorders, with a fair oldest-first queue.

The invariant this module exists to protect: **``books.stock`` never goes
negative.**  When a customer orders more copies than the warehouse holds, the
checkout ships what exists and records the remainder here.  The customer has
paid for the full quantity and the money side of the order is unchanged; only
the physical movement is split in two.

When stock returns, :func:`allocate_backorders` walks the queue in
``created_at, id`` order and hands out copies until either the queue or the
stock is exhausted.  That ordering is the whole fairness argument: an earlier
order can never be overtaken by a later one.
"""

from datetime import datetime


def record_backorder(connection, book_id, user_id, order_id, quantity):
    """Queue ``quantity`` copies that cannot ship yet. Returns the row id."""
    quantity = int(quantity)

    if quantity <= 0:
        raise ValueError("A backorder must be for at least one copy.")

    cursor = connection.execute(
        """
        INSERT INTO backorders(book_id, user_id, order_id, quantity)
        VALUES (?, ?, ?, ?)
        """,
        (book_id, user_id, order_id, quantity),
    )

    return cursor.lastrowid


def pending_quantity(connection, book_id):
    row = connection.execute(
        """
        SELECT COALESCE(SUM(quantity - fulfilled_quantity), 0) AS pending
        FROM backorders
        WHERE book_id = ? AND fulfilled_at IS NULL
        """,
        (book_id,),
    ).fetchone()

    return row["pending"]


def open_backorders(connection, book_id=None):
    """The live queue, oldest first — also what the admin page shows."""
    query = """
        SELECT b.*, books.title, books.stock, users.email,
               orders.status AS order_status
        FROM backorders b
        JOIN books ON books.id = b.book_id
        JOIN users ON users.id = b.user_id
        JOIN orders ON orders.id = b.order_id
        WHERE b.fulfilled_at IS NULL
    """
    parameters = []

    if book_id is not None:
        query += " AND b.book_id = ?"
        parameters.append(book_id)

    query += " ORDER BY b.created_at, b.id"

    return connection.execute(query, parameters).fetchall()


def allocate_backorders(connection, book_id, notify=None):
    """Ship queued copies against whatever stock is now available.

    ``notify`` is an optional ``callable(user_id, message, link)`` so the caller
    decides how a customer hears the good news (the repository already has
    ``helpers.add_notification`` for exactly that).

    Returns the number of copies allocated.  Call this inside the transaction
    that increased stock, so the allocation can never run against a stale
    number.
    """
    allocated = 0
    queue = connection.execute(
        """
        SELECT * FROM backorders
        WHERE book_id = ? AND fulfilled_at IS NULL
        ORDER BY created_at, id
        """,
        (book_id,),
    ).fetchall()

    for row in queue:
        remaining = row["quantity"] - row["fulfilled_quantity"]

        if remaining <= 0:
            continue

        stock_row = connection.execute(
            "SELECT stock, title FROM books WHERE id = ?", (book_id,)
        ).fetchone()

        if not stock_row or stock_row["stock"] <= 0:
            break

        take = min(remaining, stock_row["stock"])
        now = datetime.now().isoformat(timespec="seconds")

        # Conditional UPDATE: the guard is repeated in SQL so stock can never be
        # driven below zero even if this function is ever called concurrently.
        changed = connection.execute(
            "UPDATE books SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (take, book_id, take),
        ).rowcount

        if changed != 1:
            break

        fulfilled = row["fulfilled_quantity"] + take
        still_open = fulfilled < row["quantity"]

        connection.execute(
            """
            UPDATE backorders
            SET fulfilled_quantity = ?, fulfilled_at = ?
            WHERE id = ?
            """,
            (fulfilled, None if still_open else now, row["id"]),
        )

        connection.execute(
            """
            INSERT INTO stock_log(
                book_id, change_amount, reason, order_id, actor_user_id
            )
            VALUES (?, ?, 'backorder shipped', ?, NULL)
            """,
            (book_id, -take, row["order_id"]),
        )

        allocated += take

        if not still_open and notify is not None:
            notify(
                row["user_id"],
                f"{stock_row['title']} is on its way. "
                f"Your backordered copy has shipped.",
                "/order-history",
            )

    return allocated
