"""Requirement 63 — automatic pricing rules, stored as data.

A rule is a row in ``pricing_rules``; nothing about it is hard-coded, so staff
can add, switch off or delete a promotion from the admin page without a deploy
(and without touching ``books.price_paise``, which requirement 54 keeps as the
audited list price).

Three rule shapes are supported, which is what the shop actually needs:

``category_percent``       percent off every book in a category
``book_percent``           percent off one specific book
``min_quantity_percent``   percent off the whole cart once it holds N copies

Percentages stack additively but are capped, and no line can ever be discounted
below zero.  The result is money in integer paise like everywhere else.
"""


def active_rules(connection):
    return connection.execute(
        """
        SELECT * FROM pricing_rules
        WHERE active = 1
        ORDER BY id
        """
    ).fetchall()


def _book_categories(connection, book_id):
    rows = connection.execute(
        "SELECT category FROM book_categories WHERE book_id = ?",
        (book_id,),
    ).fetchall()

    categories = {row["category"] for row in rows}

    book = connection.execute(
        "SELECT category FROM books WHERE id = ?", (book_id,)
    ).fetchone()

    if book and book["category"]:
        categories.add(book["category"])

    return categories


def evaluate(connection, lines):
    """Apply the active rules to ``lines``.

    ``lines`` is an iterable of ``{"book_id", "quantity", "line_total"}``
    dicts — the same shape the checkout already builds for coupons.

    Returns ``(total_discount_paise, per_book_discounts)`` where
    ``per_book_discounts`` maps ``book_id -> paise``.  Splitting the discount per
    book is what lets the order store it per line later if needed, and it makes
    "no line goes negative" easy to guarantee.
    """
    lines = list(lines)

    if not lines:
        return 0, {}

    rules = active_rules(connection)

    if not rules:
        return 0, {}

    total_quantity = sum(line["quantity"] for line in lines)

    # Rules that apply to the whole cart need one decision, not one per line.
    cart_percent = sum(
        rule["percent_off"]
        for rule in rules
        if rule["rule_type"] == "min_quantity_percent"
        and total_quantity >= rule["min_quantity"]
    )

    per_book = {}

    for line in lines:
        percent = cart_percent
        categories = None

        for rule in rules:
            if rule["rule_type"] == "book_percent":
                if rule["book_id"] == line["book_id"]:
                    percent += rule["percent_off"]

            elif rule["rule_type"] == "category_percent":
                if categories is None:
                    categories = _book_categories(connection, line["book_id"])

                if rule["category"] in categories:
                    percent += rule["percent_off"]

        # Cap so a stack of promotions cannot exceed the line value.
        percent = min(percent, 90)

        if percent <= 0:
            continue

        discount = line["line_total"] * percent // 100
        discount = min(discount, line["line_total"])

        if discount > 0:
            per_book[line["book_id"]] = discount

    return sum(per_book.values()), per_book
