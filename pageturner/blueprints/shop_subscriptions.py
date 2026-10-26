"""Requirement 70 (customer side) — the book-of-the-month subscription.

The page shows the current featured title, lets a signed-in customer start,
pause, resume or cancel a subscription, and lists the deliveries already billed.
All of the money lives in :mod:`pageturner.subscriptions`; the routes here only
translate form input into those calls.
"""

from flask import flash, redirect, render_template, request, session, url_for

from .. import subscriptions
from ..db import get_db
from ..helpers import current_user, login_required
from .shop import shop_bp


@shop_bp.route("/subscription", methods=["GET", "POST"])
@login_required
def subscription():
    connection = get_db()
    user = current_user()
    mine = subscriptions.subscription_for(connection, user["id"])

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        try:
            if action == "subscribe":
                book = subscriptions.featured(connection)

                if book is None:
                    raise ValueError(
                        "There is no book of the month yet — please check back soon."
                    )

                subscriptions.subscribe(
                    connection,
                    user["id"],
                    book["book_id"],
                    request.form.get("quantity") or 1,
                )
                message = f"You are subscribed to {book['title']}."
            elif action in ("pause", "resume", "cancel"):
                subscriptions.set_status(
                    connection,
                    user["id"],
                    {"pause": "paused", "resume": "active", "cancel": "cancelled"}[
                        action
                    ],
                )
                message = {
                    "pause": "Subscription paused — no copies will be charged.",
                    "resume": "Subscription resumed.",
                    "cancel": "Subscription cancelled. You can start a new one any time.",
                }[action]
            else:
                raise ValueError("Unknown action.")

            connection.commit()
        except ValueError as error:
            connection.rollback()
            flash(str(error), "error")
            return redirect(url_for("shop.subscription"))
        except Exception:
            connection.rollback()
            raise

        flash(message, "success")
        return redirect(url_for("shop.subscription"))

    return render_template(
        "subscription.html",
        featured=subscriptions.featured(connection),
        subscription=mine,
        schedule=subscriptions.schedule_note(mine),
        history=subscriptions.history_for(connection, user["id"]),
        max_quantity=subscriptions.MAX_QUANTITY,
    )


@shop_bp.post("/subscription/skip")
@login_required
def skip_subscription_month():
    """Skip exactly one delivery: push the next billing date on by a month."""
    connection = get_db()
    mine = subscriptions.subscription_for(connection, session["user_id"])

    if mine is None or mine["status"] != "active":
        flash("You do not have an active subscription to skip.", "error")
        return redirect(url_for("shop.subscription"))

    next_run = subscriptions.first_of_next_month(
        mine["next_run_on"][:7]
    ).isoformat()

    connection.execute(
        "UPDATE subscriptions SET next_run_on = ? WHERE id = ?",
        (next_run, mine["id"]),
    )
    connection.commit()

    flash("This month's delivery is skipped.", "success")
    return redirect(url_for("shop.subscription"))
