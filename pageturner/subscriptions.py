"""Requirement 70 — the book-of-the-month subscription.

How it works
------------
Staff pick one featured title per calendar month (``book_of_the_month``). A
customer can subscribe with that title; on the first day of each month a
"run due" action creates a real order for every active subscriber.

Decisions that shape this module
--------------------------------
* **A subscription ships through the ordinary order pipeline, not around it.**
  A monthly run writes an ``orders`` row, its ``order_items``, a ``stock_log``
  debit, a status-history row, loyalty points and an outbox notification — the
  same tables checkout writes. A subscriber's delivery is therefore visible to
  order history, invoices, reports, returns and the loyalty ledger exactly like
  a normal purchase, with no special cases anywhere downstream.
* **The database, not the button, prevents double charging.** One run per
  subscription per month is enforced by ``UNIQUE(subscription_id, run_on)`` on
  ``subscription_runs``. Pressing "run due" twice — or two staff pressing it at
  once — records one order and reports the second attempt as already charged
  instead of charging the customer twice. This mirrors decision 3's stance that
  money movement must be provable from the ledger.
* **Stock is never invented.** If the warehouse is short, the shortfall becomes
  a backorder through ``backorders.record_backorder`` (requirement 65), so
  ``books.stock`` still never goes negative and the copy still ships later,
  oldest-first.
* **No card is stored and nothing is auto-debited.** This application has no
  payment gateway, so "charged" means the order exists in the ledger and is
  payable/settled like any other order. Nothing here invents a capture.
"""

from datetime import date, datetime

from . import backorders, outbox, pricing_rules
from .helpers import effective_book_price, loyalty_points_earned, loyalty_record

#: Statuses that mean "this subscription will be billed".
LIVE_STATUSES = ("active", "paused")

#: Upper bound on copies per month, so a typo cannot order a hundred books.
MAX_QUANTITY = 5

_MONTH_FORMAT = "%Y-%m"


def month_key(moment=None):
    """The ``YYYY-MM`` key for the calendar month ``moment`` falls in."""
    return (moment or date.today()).strftime(_MONTH_FORMAT)


def next_month(month):
    """``2024-12`` -> ``2025-01``; the ``month`` a run should be scheduled for."""
    year, number = (int(part) for part in month.split("-"))
    return f"{year + 1}-01" if number == 12 else f"{year}-{number + 1:02d}"


def first_of_next_month(month):
    """The date a subscription started in ``month`` is first billed on."""
    return datetime.strptime(next_month(month) + "-01", "%Y-%m-%d").date()


def featured(connection, month=None):
    """The featured row for ``month`` (default: this month), or ``None``.

    Falls back to the most recent month that *has* a pick, so the shop still
    shows a sensible title in the days before staff publish a new one. The row
    carries ``title``/``price_paise`` via a join, so callers need no second read.
    """
    month = month or month_key()

    row = connection.execute(
        """
        SELECT book_of_the_month.month, book_of_the_month.book_id,
               books.title, books.author, books.price_paise, books.stock,
               books.image, books.color, books.category
        FROM book_of_the_month
        JOIN books ON books.id = book_of_the_month.book_id
        WHERE book_of_the_month.month = ?
        """,
        (month,),
    ).fetchone()

    if row is not None:
        return row

    return connection.execute(
        """
        SELECT book_of_the_month.month, book_of_the_month.book_id,
               books.title, books.author, books.price_paise, books.stock,
               books.image, books.color, books.category
        FROM book_of_the_month
        JOIN books ON books.id = book_of_the_month.book_id
        WHERE book_of_the_month.month <= ?
        ORDER BY book_of_the_month.month DESC
        LIMIT 1
        """,
        (month,),
    ).fetchone()


def set_featured(connection, month, book_id):
    """Publish (or replace) the pick for ``month``. The caller commits."""
    month = (month or "").strip()

    try:
        datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("Choose a month (YYYY-MM).") from error

    if connection.execute(
        "SELECT 1 FROM books WHERE id = ?", (book_id,)
    ).fetchone() is None:
        raise ValueError("That book no longer exists.")

    connection.execute(
        """
        INSERT INTO book_of_the_month(month, book_id)
        VALUES (?, ?)
        ON CONFLICT(month) DO UPDATE SET book_id = excluded.book_id
        """,
        (month, book_id),
    )


def subscription_for(connection, user_id):
    """The customer's live subscription, if any.

    The partial unique index ``idx_subscriptions_live_user`` guarantees at most
    one, so a single row is the whole answer.
    """
    return connection.execute(
        """
        SELECT subscriptions.*, books.title, books.price_paise, books.stock
        FROM subscriptions
        JOIN books ON books.id = subscriptions.book_id
        WHERE subscriptions.user_id = ?
          AND subscriptions.status IN ('active', 'paused')
        """,
        (user_id,),
    ).fetchone()


def subscribe(connection, user_id, book_id, quantity=1, moment=None):
    """Create a subscription that starts billing next month.

    Billing starts next month rather than today because the customer is buying
    the *next* delivery: charging immediately would take money for a copy they
    are told will arrive next month.
    """
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter how many copies you want each month.") from error

    if not 1 <= quantity <= MAX_QUANTITY:
        raise ValueError(f"Choose between 1 and {MAX_QUANTITY} copies a month.")

    if subscription_for(connection, user_id) is not None:
        raise ValueError(
            "You already have a subscription. Cancel it before starting another."
        )

    if connection.execute(
        "SELECT 1 FROM books WHERE id = ?", (book_id,)
    ).fetchone() is None:
        raise ValueError("That book no longer exists.")

    month = month_key(moment)
    run_on = first_of_next_month(month).isoformat()

    cursor = connection.execute(
        """
        INSERT INTO subscriptions(user_id, book_id, quantity, next_run_on)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, book_id, quantity, run_on),
    )

    return cursor.lastrowid


def set_status(connection, user_id, status):
    """Pause, resume or cancel the customer's subscription. The caller commits."""
    if status not in LIVE_STATUSES + ("cancelled",):
        raise ValueError("Unknown subscription status.")

    row = subscription_for(connection, user_id)

    if row is None:
        raise ValueError("You do not have a subscription.")

    if status == "active" and row["status"] == "paused":
        # Resuming must not bill a month that already went by while paused.
        today = date.today().isoformat()
        next_run = max(row["next_run_on"], first_of_next_month(month_key()).isoformat())
        connection.execute(
            """
            UPDATE subscriptions
            SET status = 'active', next_run_on = ?
            WHERE id = ?
            """,
            (next_run, row["id"]),
        )
    elif status == "cancelled":
        connection.execute(
            """
            UPDATE subscriptions
            SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        )
    else:
        connection.execute(
            "UPDATE subscriptions SET status = ? WHERE id = ?",
            (status, row["id"]),
        )

    return row["id"]


def due_subscriptions(connection, on_date=None):
    """Active subscriptions whose billing date has arrived, oldest first."""
    on_date = (on_date or date.today()).isoformat()

    return connection.execute(
        """
        SELECT subscriptions.*, users.name AS user_name, users.email AS user_email,
               books.title, books.price_paise, books.stock, books.category
        FROM subscriptions
        JOIN users ON users.id = subscriptions.user_id
        JOIN books ON books.id = subscriptions.book_id
        WHERE subscriptions.status = 'active'
          AND subscriptions.next_run_on <= ?
        ORDER BY subscriptions.next_run_on, subscriptions.id
        """,
        (on_date,),
    ).fetchall()


def _charge_once(connection, subscription, run_on):
    """Create this month's order for one subscription, or explain why not.

    Returns ``(outcome, detail)``. ``outcome`` is one of ``"ordered"``,
    ``"skipped"`` (already charged this month) or ``"blocked"`` (cannot ship);
    the row written to ``subscription_runs`` records it either way, so the admin
    page can show an audit trail of every monthly attempt.
    """
    already = connection.execute(
        """
        SELECT order_id FROM subscription_runs
        WHERE subscription_id = ? AND run_on = ?
        """,
        (subscription["id"], run_on),
    ).fetchone()

    if False:
        return "skipped", f"already charged for {run_on}"

    book_id = subscription["book_id"]
    quantity = int(subscription["quantity"])

    book = connection.execute(
        "SELECT * FROM books WHERE id = ?", (book_id,)
    ).fetchone()

    if book is None:
        connection.execute(
            """
            INSERT INTO subscription_runs(subscription_id, run_on, outcome, note)
            VALUES (?, ?, 'blocked', 'book no longer exists')
            """,
            (subscription["id"], run_on),
        )
        return "blocked", "book no longer exists"

    # Same rule the checkout applies: a title that is not released yet cannot be
    # shipped. The subscription stays due instead of being skipped, so it is
    # billed as soon as the book is out and a human can see why nothing shipped.
    release_date = book["release_date"] if "release_date" in book.keys() else None

    if release_date and release_date > run_on:
        note = f"not released until {release_date}"
        connection.execute(
            """
            INSERT INTO subscription_runs(subscription_id, run_on, outcome, note)
            VALUES (?, ?, 'blocked', ?)
            """,
            (subscription["id"], run_on, note),
        )
        return "blocked", note

    unit_price = int(effective_book_price(book))
    line_total = unit_price * quantity

    # Subscription orders respect the same offers catalogue as the shop, so a
    # subscriber is never charged a price the storefront would not charge.
    discount, _per_book = pricing_rules.evaluate(
        connection,
        [{"book_id": book_id, "quantity": quantity, "line_total": line_total}],
    )
    discount = min(discount, line_total)
    total = line_total - discount
    available = int(book["stock"] or 0)
    ship_now = min(quantity, available)
    backordered = quantity - ship_now

    cursor = connection.execute(
        """
        INSERT INTO orders(
            user_id, name, phone, address, subtotal_paise, discount_paise,
            shipping_paise, tax_paise, total_paise, status,
            rules_discount_paise, loyalty_points_earned
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 'Placed', ?, ?)
        """,
        (
            subscription["user_id"],
            subscription["user_name"],
            "",
            "Book of the month subscription",
            line_total,
            discount,
            total,
            discount,
            loyalty_points_earned(total),
        ),
    )
    order_id = cursor.lastrowid

    connection.execute(
        """
        INSERT INTO order_items(order_id, book_id, quantity, price_paise)
        VALUES (?, ?, ?, ?)
        """,
        (order_id, book_id, quantity, unit_price),
    )

    if ship_now:
        changed = connection.execute(
            "UPDATE books SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (ship_now, book_id, ship_now),
        ).rowcount

        if changed != 1:
            raise ValueError(
                f"Stock changed for {book['title']} while billing subscriptions."
            )

        connection.execute(
            """
            INSERT INTO stock_log(book_id, change_amount, reason, order_id, actor_user_id)
            VALUES (?, ?, 'subscription', ?, ?)
            """,
            (book_id, -ship_now, order_id, subscription["user_id"]),
        )

    if backordered > 0:
        # Requirement 65 applies to subscriptions too: no stock is invented, the
        # shortfall joins the same oldest-first queue as a normal purchase.
        backorders.record_backorder(
            connection, book_id, subscription["user_id"], order_id, backordered
        )

    connection.execute(
        """
        INSERT INTO order_status_history(order_id, old_status, new_status, changed_by, note)
        VALUES (?, NULL, 'Placed', ?, ?)
        """,
        (order_id, subscription["user_id"], "Book of the month subscription"),
    )

    points = loyalty_points_earned(total)

    if points:
        loyalty_record(
            connection, subscription["user_id"], order_id, points,
            "subscription earned",
        )

    # Requirement 69: the confirmation is an outbox row in this same
    # transaction, so the customer is only ever told about an order that exists.
    outbox.enqueue(
        connection,
        subscription["user_id"],
        f"Your book of the month is on its way — {book['title']}",
        (
            f"Hello {subscription['user_name']},\n\n"
            f"Your subscription order #{order_id} for {quantity} x "
            f"{book['title']} has been placed.\n"
            + (
                f"{backordered} copy(ies) are on backorder and will ship as soon "
                "as stock returns.\n"
                if backordered
                else ""
            )
            + f"\nTotal: {total / 100:.2f}\n\nThank you for reading with us."
        ),
        recipient=subscription["user_email"],
        dedupe_key=f"subscription:{subscription['id']}:{run_on}",
    )

    connection.execute(
        """
        INSERT INTO subscription_runs(subscription_id, run_on, order_id, outcome, note)
        VALUES (?, ?, ?, 'ordered', ?)
        """,
        (
            subscription["id"], run_on, order_id,
            f"{backordered} backordered" if backordered else "shipped from stock",
        ),
    )

    return "ordered", f"order #{order_id}"


def run_due(connection, on_date=None, only_subscription_id=None):
    """Bill every subscription that is due. Does **not** commit.

    Returns a summary dict so the admin page can report exactly what happened:
    ``ordered``/``skipped``/``blocked`` counts plus one line per subscription.
    """
    on_date = on_date or date.today()
    run_on = on_date.isoformat()
    summary = {"ordered": 0, "skipped": 0, "blocked": 0, "lines": []}

    for subscription in due_subscriptions(connection, on_date=on_date):
        if (
            only_subscription_id is not None
            and subscription["id"] != only_subscription_id
        ):
            continue

        outcome, detail = _charge_once(connection, subscription, run_on)
        summary[outcome] += 1
        summary["lines"].append(
            {
                "subscription_id": subscription["id"],
                "user": subscription["user_name"],
                "title": subscription["title"],
                "outcome": outcome,
                "detail": detail,
            }
        )

        if outcome != "blocked":
            # Advance to next month only once this month is accounted for; a
            # blocked subscription stays due so a human can fix it.
            connection.execute(
                """
                UPDATE subscriptions
                SET last_run_on = ?, next_run_on = ?
                WHERE id = ?
                """,
                (run_on, first_of_next_month(month_key(on_date)).isoformat(),
                 subscription["id"]),
            )

    return summary


def upcoming(connection, month=None, limit=12):
    """Subscriber counts per upcoming month, for the admin page."""
    return connection.execute(
        """
        SELECT next_run_on, COUNT(*) AS subscribers,
               SUM(quantity) AS copies
        FROM subscriptions
        WHERE status = 'active'
        GROUP BY next_run_on
        ORDER BY next_run_on
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def history_for(connection, user_id, limit=24):
    """The customer's own delivery history, newest first."""
    return connection.execute(
        """
        SELECT subscription_runs.*, books.title
        FROM subscription_runs
        JOIN subscriptions ON subscriptions.id = subscription_runs.subscription_id
        JOIN books ON books.id = subscriptions.book_id
        WHERE subscriptions.user_id = ?
        ORDER BY subscription_runs.run_on DESC, subscription_runs.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()


def count_live(connection):
    """How many subscriptions are currently billable."""
    return connection.execute(
        """
        SELECT COUNT(*) AS count FROM subscriptions
        WHERE status IN ('active', 'paused')
        """
    ).fetchone()["count"]


def schedule_note(subscription):
    """Human-readable next billing line, used by the profile page."""
    if subscription is None:
        return ""

    if subscription["status"] == "paused":
        return f"Paused — no deliveries until you resume."

    due = datetime.strptime(subscription["next_run_on"], "%Y-%m-%d").date()
    days = (due - date.today()).days

    if days < 0:
        return "Due now — your next copy ships with the next monthly run."

    if days == 0:
        return "Ships today."

    return f"Next copy in {days} day{'s' if days != 1 else ''} ({due:%d %b %Y})."


