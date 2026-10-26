"""Single choke point for book price changes (requirements 54 and 55).

Every write to ``books.price_paise`` goes through :func:`set_book_price`, which
records an immutable ``book_price_history`` row (old price, new price, actor,
timestamp, reason).  Sale pricing lives in ``books.sale_price_paise`` and is
deliberately untouched here, as are the ``order_items`` price snapshots that
past invoices rely on.
"""

from decimal import Decimal, ROUND_HALF_UP

MAX_PERCENT = Decimal("1000")


def _to_decimal(value):
    return Decimal(str(value))


def apply_percentage(price_paise, percent, mode):
    """Return the new price in integer paise with half-up rounding.

    ``mode`` is ``"increase"`` (``percent`` above the current price) or
    ``"discount"`` (``percent`` off the current price).  Prices are integers
    in paise, so the fractional paise produced by a percentage is rounded
    half-up exactly once, never via float arithmetic.
    """
    if price_paise is None or price_paise < 0:
        raise ValueError("Current price must be a non-negative integer in paise.")

    rate = _to_decimal(percent)
    if rate < 0 or rate > MAX_PERCENT:
        raise ValueError("Percentage must be between 0 and %s." % MAX_PERCENT)

    base = _to_decimal(price_paise)

    if mode == "increase":
        raw = base * (Decimal("100") + rate) / Decimal("100")
    elif mode == "discount":
        if rate > Decimal("100"):
            raise ValueError("A discount cannot exceed 100 percent.")
        raw = base * (Decimal("100") - rate) / Decimal("100")
    else:
        raise ValueError("Unknown price change mode: %r" % (mode,))

    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def set_book_price(connection, book_id, new_price_paise, actor_user_id=None,
                   reason=None):
    """Set a book's price and log the change.  Returns True when it changed.

    * The price is read from the database inside the caller's transaction, so
      the recorded ``old_price_paise`` is always the value actually replaced.
    * A no-op call records nothing, except for a book that has no history at
      all yet (its creation baseline, stored with ``old_price_paise NULL``).
    """
    if new_price_paise is None or int(new_price_paise) < 0:
        raise ValueError("Price must be a non-negative integer in paise.")
    new_price_paise = int(new_price_paise)

    book = connection.execute(
        "SELECT id, price_paise FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if book is None:
        raise ValueError("Book %s does not exist." % (book_id,))

    old_price_paise = book["price_paise"]
    has_history = connection.execute(
        "SELECT 1 FROM book_price_history WHERE book_id = ? LIMIT 1",
        (book_id,),
    ).fetchone() is not None

    changed = old_price_paise != new_price_paise

    if not changed:
        if has_history:
            return False
        # First recorded price for this book: creation baseline.
        connection.execute(
            "INSERT INTO book_price_history"
            "(book_id, old_price_paise, new_price_paise, reason, actor_user_id) "
            "VALUES (?, NULL, ?, ?, ?)",
            (book_id, new_price_paise, reason or "initial price", actor_user_id),
        )
        return True

    connection.execute(
        "UPDATE books SET price_paise = ? WHERE id = ?",
        (new_price_paise, book_id),
    )
    connection.execute(
        "INSERT INTO book_price_history"
        "(book_id, old_price_paise, new_price_paise, reason, actor_user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (book_id, old_price_paise, new_price_paise, reason, actor_user_id),
    )
    return True


def price_history_for_books(connection, book_ids, per_book=5):
    """Return {book_id: [history rows]} newest first, in one query."""
    if not book_ids:
        return {}

    placeholders = ",".join("?" for _ in book_ids)
    rows = connection.execute(
        "SELECT h.*, u.email AS actor_email "
        "FROM book_price_history h "
        "LEFT JOIN users u ON u.id = h.actor_user_id "
        "WHERE h.book_id IN (%s) "
        "ORDER BY h.book_id, h.changed_at DESC, h.id DESC" % placeholders,
        tuple(book_ids),
    ).fetchall()

    grouped = {}
    for row in rows:
        bucket = grouped.setdefault(row["book_id"], [])
        if len(bucket) < per_book:
            bucket.append(row)
    return grouped
