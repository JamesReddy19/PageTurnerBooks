"""Requirement 64 (customer side) — buy and redeem gift cards.

A card is bought like any other line: paying for it issues a card with the same
value, and the plaintext code is shown exactly once.  A card is never stored in
the session — it is a bearer instrument, and the only copy of its code is the one
on the buyer's screen.
"""

from flask import (
    abort, flash, redirect, render_template, request, session, url_for
)

from .. import giftcards
from ..db import get_db
from ..helpers import current_user, login_required
from .shop import shop_bp

# Preset amounts, in whole rupees, so the form cannot be used to mint an
# arbitrary balance.
GIFT_CARD_AMOUNTS = (250, 500, 1000, 2000, 5000)


@shop_bp.route("/gift-cards", methods=["GET", "POST"])
@login_required
def gift_cards():
    connection = get_db()
    user = current_user()

    if request.method == "POST":
        try:
            amount = int(request.form.get("amount_paise", "0"))
        except ValueError:
            amount = 0

        if amount not in [value * 100 for value in GIFT_CARD_AMOUNTS]:
            flash("Choose one of the listed gift card amounts.", "error")
            return redirect(url_for("shop.gift_cards"))

        try:
            connection.execute("BEGIN IMMEDIATE")

            # Buying a card is an order, so the money and the card are written
            # in one transaction: a failed order can never mint a free card.
            cursor = connection.execute(
                """
                INSERT INTO orders(
                    user_id, name, phone, address, subtotal_paise,
                    discount_paise, shipping_paise, tax_paise, total_paise,
                    status, pincode
                )
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, 'Placed', ?)
                """,
                (
                    # A gift card is delivered by email, so there is no
                    # shipping address to record. The columns are NOT NULL,
                    # so they carry an explicit "digital" marker rather than
                    # a fake street address.
                    user["id"], user["name"], "", "Digital delivery", amount,
                    amount, "",
                ),
            )
            order_id = cursor.lastrowid

            card_id, code = giftcards.issue_gift_card(
                connection,
                amount,
                issued_to_user_id=user["id"],
                purchased_order_id=order_id,
            )

            connection.execute(
                """
                INSERT INTO order_status_history(
                    order_id, old_status, new_status, changed_by, note
                )
                VALUES (?, NULL, 'Placed', ?, 'Gift card purchase')
                """,
                (order_id, user["id"]),
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        # The plaintext code exists only in this response.
        return render_template(
            "gift_card_issued.html",
            code=code,
            card_id=card_id,
            amount_paise=amount,
            order_id=order_id,
        )

    return render_template(
        "gift_cards.html",
        amounts=GIFT_CARD_AMOUNTS,
        cards=giftcards.cards_for_user(connection, user["id"]),
    )


@shop_bp.post("/gift-cards/<int:card_id>/disable")
@login_required
def disable_gift_card(card_id):
    connection = get_db()
    user = current_user()

    card = connection.execute(
        "SELECT * FROM gift_cards WHERE id = ?", (card_id,)
    ).fetchone()

    # Someone else's card must look like a missing one.
    if not card or card["issued_to_user_id"] != user["id"]:
        abort(404)

    connection.execute(
        "UPDATE gift_cards SET disabled_at = CURRENT_TIMESTAMP WHERE id = ?",
        (card_id,),
    )
    connection.commit()

    flash("Gift card cancelled. The balance can no longer be spent.", "success")
    return redirect(url_for("shop.gift_cards"))
