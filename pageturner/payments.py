"""Simulated payment handling.

This project does not integrate a real payment gateway. Nothing here contacts
Razorpay, Stripe, PayU or any other provider, no card data is stored, and no
money moves. A payment row is written so the order page can state clearly what
happened ("simulated").

A real integration would replace `charge()` with a provider SDK call, verify a
webhook signature, and only then mark the order as paid.
"""

import secrets
from datetime import datetime

SIMULATED = True

METHOD_COD = "cod"
METHOD_CARD = "card_simulated"
METHOD_UPI = "upi_simulated"

# Only these values are accepted from the checkout form.
ALLOWED_METHODS = (METHOD_COD, METHOD_CARD, METHOD_UPI)

METHOD_LABELS = {
    METHOD_COD: "Cash on delivery",
    METHOD_CARD: "Card (simulated)",
    METHOD_UPI: "UPI (simulated)",
}

STATUS_PLACED = "Pending collection"
STATUS_SIMULATED_PAID = "Simulated - paid"


def method_label(method):
    return METHOD_LABELS.get(method, "Unknown method")


def charge(method, amount_paise):
    """Return a simulated payment result.

    `amount_paise` is validated as a non-negative integer so the same contract
    holds if a real gateway is wired in later.
    """
    if method not in ALLOWED_METHODS:
        raise ValueError("Choose a valid payment method.")

    if not isinstance(amount_paise, int) or amount_paise < 0:
        raise ValueError("Invalid payment amount.")

    if method == METHOD_COD:
        status = STATUS_PLACED
    else:
        status = STATUS_SIMULATED_PAID

    reference = "SIM-" + secrets.token_hex(5).upper()

    return {
        "method": method,
        "status": status,
        "reference": reference,
        "amount_paise": amount_paise,
        "is_simulated": 1 if SIMULATED else 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def record_payment(connection, order_id, result):
    """Persist a payment result inside the caller's transaction."""
    connection.execute(
        """
        INSERT INTO payments(
            order_id, method, status, amount_paise,
            reference, is_simulated, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            result["method"],
            result["status"],
            result["amount_paise"],
            result["reference"],
            result["is_simulated"],
            result["created_at"],
        ),
    )


def payment_for_order(connection, order_id):
    return connection.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
