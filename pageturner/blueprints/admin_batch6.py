"""Admin side of requirements 69 and 70 — the outbox and the subscription.

Neither page edits policy in code. The outbox page shows what is owed and lets
staff retry a parked message; the subscription page publishes the featured title
and performs the monthly run.
"""

from flask import flash, redirect, render_template, request, url_for

from .. import outbox, subscriptions
from ..db import get_db
from ..helpers import log_admin_action, permission_required
from .admin import admin_bp


@admin_bp.get("/outbox")
@permission_required("outbox.manage")
def outbox_page():
    connection = get_db()
    status = (request.args.get("status") or "pending").strip()

    if status not in ("pending", "sent", "failed", "all"):
        status = "pending"

    query = "SELECT * FROM notification_outbox"
    parameters = []

    if status != "all":
        query += " WHERE status = ?"
        parameters.append(status)

    query += " ORDER BY id DESC LIMIT 200"

    return render_template(
        "admin_outbox.html",
        messages=connection.execute(query, parameters).fetchall(),
        counts=outbox.status_counts(connection),
        status=status,
        counts_total=sum(outbox.status_counts(connection).values()),
    )


@admin_bp.post("/outbox/process")
@permission_required("outbox.manage")
def process_outbox():
    """Deliver every message whose backoff has elapsed, then report the result."""
    connection = get_db()

    try:
        summary = outbox.process_due(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    log_admin_action(
        "outbox.process",
        f"sent={summary['sent']} retried={summary['retried']} "
        f"failed={summary['failed']}",
    )

    if summary["sent"] or summary["retried"] or summary["failed"]:
        flash(
            f"{summary['sent']} sent, {summary['retried']} retrying, "
            f"{summary['failed']} given up.",
            "success" if not summary["failed"] else "error",
        )
    else:
        flash("Nothing was due to be delivered.", "error")

    return redirect(url_for("admin.outbox_page"))


@admin_bp.post("/outbox/<int:message_id>/retry")
@permission_required("outbox.manage")
def retry_outbox_message(message_id):
    connection = get_db()

    if outbox.retry_now(connection, message_id):
        connection.commit()
        log_admin_action("outbox.retry", f"message {message_id}")
        flash("Message re-queued for delivery.", "success")
    else:
        connection.rollback()
        flash("Only a message that has been given up on can be retried.", "error")

    return redirect(url_for("admin.outbox_page", status="failed"))


@admin_bp.route("/book-of-the-month", methods=["GET", "POST"])
@permission_required("subscriptions.manage")
def book_of_the_month():
    connection = get_db()

    if request.method == "POST":
        month = (request.form.get("month") or subscriptions.month_key()).strip()
        book_id = (request.form.get("book_id") or "").strip()

        try:
            if not book_id:
                raise ValueError("Choose the book to feature.")

            subscriptions.set_featured(connection, month, int(book_id))
            connection.commit()
        except ValueError as error:
            connection.rollback()
            flash(str(error), "error")
            return redirect(url_for("admin.book_of_the_month"))
        except Exception:
            connection.rollback()
            raise

        log_admin_action("subscription.feature", f"{month} -> book {book_id}")
        flash(f"Featured book published for {month}.", "success")

        return redirect(url_for("admin.book_of_the_month"))

    return render_template(
        "admin_book_of_the_month.html",
        featured=subscriptions.featured(connection),
        picks=connection.execute(
            """
            SELECT book_of_the_month.month, book_of_the_month.book_id, books.title
            FROM book_of_the_month
            JOIN books ON books.id = book_of_the_month.book_id
            ORDER BY book_of_the_month.month DESC
            LIMIT 12
            """
        ).fetchall(),
        books=connection.execute(
            "SELECT id, title, price_paise FROM books ORDER BY title LIMIT 500"
        ).fetchall(),
        subscribers=connection.execute(
            """
            SELECT subscriptions.*, users.name AS user_name, users.email,
                   books.title
            FROM subscriptions
            JOIN users ON users.id = subscriptions.user_id
            JOIN books ON books.id = subscriptions.book_id
            ORDER BY subscriptions.status, subscriptions.next_run_on
            LIMIT 200
            """
        ).fetchall(),
        live=subscriptions.count_live(connection),
        due=subscriptions.due_subscriptions(connection),
        upcoming=subscriptions.upcoming(connection),
        today=subscriptions.month_key(),
        runs=connection.execute(
            """
            SELECT subscription_runs.*, users.name AS user_name, books.title
            FROM subscription_runs
            JOIN subscriptions ON subscriptions.id = subscription_runs.subscription_id
            JOIN users ON users.id = subscriptions.user_id
            JOIN books ON books.id = subscriptions.book_id
            ORDER BY subscription_runs.id DESC
            LIMIT 25
            """
        ).fetchall(),
    )


@admin_bp.post("/book-of-the-month/run")
@permission_required("subscriptions.manage")
def run_subscriptions():
    """Create this month's order for every subscription that is due."""
    connection = get_db()

    try:
        connection.execute("BEGIN IMMEDIATE")
        summary = subscriptions.run_due(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    log_admin_action(
        "subscription.run",
        f"ordered={summary['ordered']} skipped={summary['skipped']} "
        f"blocked={summary['blocked']}",
    )

    if not summary["lines"]:
        flash("No subscriptions were due.", "error")
    else:
        flash(
            f"{summary['ordered']} order(s) created, "
            f"{summary['skipped']} already charged, "
            f"{summary['blocked']} blocked.",
            "success" if not summary["blocked"] else "error",
        )

    return redirect(url_for("admin.book_of_the_month"))
