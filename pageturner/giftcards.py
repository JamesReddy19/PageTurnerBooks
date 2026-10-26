"""Requirement 64 — gift cards stored as hashes, balanced by a ledger.

Two rules make this module safe:

1. **The code is never stored.** ``code_hash`` is the SHA-256 of the normalised
   code, so a copy of the database cannot be turned into spendable balance. The
   plaintext is shown to the buyer exactly once, at issue time.
2. **The balance is a SUM, not a column.** ``SELECT SUM(amount_paise)`` over
   ``gift_card_ledger`` is the only definition of the balance, so two concurrent
   redemptions cannot both "read the same number and subtract". Checkout runs
   inside ``BEGIN IMMEDIATE``, which serialises the writers, and the debit is
   written in the same transaction as the order — if the order rolls back, the
   gift card is untouched.
"""

import hashlib
import secrets
from datetime import datetime

# No 0/O/1/I/L: a code is read aloud and typed by hand.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUP_LENGTH = 4
CODE_GROUPS = 3


def normalise_code(code):
    """Case-, dash- and space-insensitive: ``abcd-efgh`` == ``ABCDEFGH``."""
    return "".join(
        character for character in (code or "").upper()
        if character.isalnum()
    )


def hash_code(code):
    return hashlib.sha256(normalise_code(code).encode("utf-8")).hexdigest()


def generate_code():
    groups = [
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_LENGTH))
        for _ in range(CODE_GROUPS)
    ]
    return "-".join(groups)


def issue_gift_card(
    connection,
    amount_paise,
    issued_to_user_id=None,
    purchased_order_id=None,
    expires_at=None,
    max_attempts=20,
):
    """Create a card and return ``(card_id, plaintext_code)``.

    The plaintext code exists only in this return value; callers must show it to
    the buyer immediately and never log it.
    """
    amount_paise = int(amount_paise)

    if amount_paise <= 0:
        raise ValueError("A gift card must be worth more than zero.")

    for _ in range(max_attempts):
        code = generate_code()
        digest = hash_code(code)

        existing = connection.execute(
            "SELECT id FROM gift_cards WHERE code_hash = ?", (digest,)
        ).fetchone()

        if existing:
            continue

        cursor = connection.execute(
            """
            INSERT INTO gift_cards(
                code_hash, code_last4, initial_paise,
                issued_to_user_id, purchased_order_id, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                digest,
                normalise_code(code)[-4:],
                amount_paise,
                issued_to_user_id,
                purchased_order_id,
                expires_at,
            ),
        )

        card_id = cursor.lastrowid

        # The initial value is a ledger row like any other, so "balance" has a
        # single definition everywhere in the app.
        connection.execute(
            """
            INSERT INTO gift_card_ledger(gift_card_id, amount_paise, reason)
            VALUES (?, ?, 'issued')
            """,
            (card_id, amount_paise),
        )

        return card_id, code

    raise ValueError("Could not generate a unique gift card code.")


def gift_card_balance(connection, card_id):
    row = connection.execute(
        """
        SELECT COALESCE(SUM(amount_paise), 0) AS balance
        FROM gift_card_ledger WHERE gift_card_id = ?
        """,
        (card_id,),
    ).fetchone()

    return row["balance"]


def find_gift_card(connection, code):
    """Look a card up by its hash. The plaintext code is never compared."""
    if not normalise_code(code):
        return None

    return connection.execute(
        "SELECT * FROM gift_cards WHERE code_hash = ?",
        (hash_code(code),),
    ).fetchone()


def card_is_spendable(card, now=None):
    if card is None or card["disabled_at"]:
        return False

    if card["expires_at"]:
        today = (now or datetime.now()).date().isoformat()

        if card["expires_at"][:10] < today:
            return False

    return True


def redeem_gift_card(connection, code, order_id, max_paise, now=None):
    """Spend up to ``max_paise`` from the card on ``order_id``.

    Returns ``(card_id, amount_paise)``; ``(None, 0)`` when the card does not
    exist, is disabled, has expired or has nothing left on it.  The caller has
    already opened the order transaction, so the debit commits or rolls back
    with the order.

    ``order_id`` may be ``None`` when the card has to be priced *before* the
    order row exists — which is exactly what checkout does, because the order
    total depends on the card.  In that case no ledger row is written here and
    the caller **must** call :func:`record_redemption` with the real order id
    inside the same transaction.  ``gift_card_ledger.order_id`` is a foreign
    key (``PRAGMA foreign_keys = ON``), so writing a placeholder such as 0 here
    would abort the whole checkout with an IntegrityError.
    """
    card = find_gift_card(connection, code)

    if not card_is_spendable(card, now):
        return None, 0

    balance = gift_card_balance(connection, card["id"])
    applied = min(balance, max(0, int(max_paise)))

    if applied <= 0:
        # An empty card cannot pay for anything; treat it like an unusable one
        # rather than silently charging zero and leaving a dangling card id on
        # the order.
        return None, 0

    if order_id is not None:
        record_redemption(connection, card["id"], order_id, applied)

    return card["id"], applied


def record_redemption(connection, card_id, order_id, amount_paise):
    """Write the debit for a redemption that was priced before the order row.

    Raises ``ValueError`` for a nonsensical amount so a caller cannot mint
    balance by passing a negative number.
    """
    amount_paise = int(amount_paise)

    if amount_paise <= 0:
        raise ValueError("A redemption must be for at least one paisa.")

    connection.execute(
        """
        INSERT INTO gift_card_ledger(
            gift_card_id, order_id, amount_paise, reason
        )
        VALUES (?, ?, ?, 'redeemed on order')
        """,
        (card_id, order_id, -amount_paise),
    )

    # Defence in depth for "the balance is a SUM, never a column": the cap in
    # `redeem_gift_card` is what normally prevents an overspend, but a caller
    # that prices a card and then writes the debit later (checkout does) has a
    # window between those two steps. Re-reading the SUM here means a card can
    # never end up negative even if that window is ever widened, and raising
    # rolls the whole order back rather than leaving a silently overdrawn card.
    if gift_card_balance(connection, card_id) < 0:
        raise ValueError(
            "That gift card does not have enough balance left for this order."
        )


def cards_for_user(connection, user_id):
    """Cards a customer bought, with their live balance (never the code)."""
    rows = connection.execute(
        """
        SELECT c.*,
               COALESCE((
                   SELECT SUM(l.amount_paise) FROM gift_card_ledger l
                   WHERE l.gift_card_id = c.id
               ), 0) AS balance_paise
        FROM gift_cards c
        WHERE c.issued_to_user_id = ?
        ORDER BY c.id DESC
        """,
        (user_id,),
    ).fetchall()

    return rows
