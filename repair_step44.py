from pathlib import Path

path = Path("step44_patch.py")
text = path.read_text(encoding="utf-8")

# Fix the book-detail date matching rule.
anchor = text.index('"homepage template dates",')
start = text.index("    updated[name] = once(\n", anchor)
end = text.index("    # 5. Enforce the release date again during checkout.", start)

book_dates = r'''    updated[name] = once(
        updated[name],
        '        also_bought=customers_also_bought(book_id),\n',
        '        also_bought=customers_also_bought(book_id),\n'
        '        today_date=date.today().isoformat(),\n'
        '        new_arrivals_cutoff=(date.today() - timedelta(days=14)).isoformat(),\n',
        "book detail template dates",
    )

'''

text = text[:start] + book_dates + text[end:]

# Fix the checkout replacement's newline escaping.
start = text.index("    # 5. Enforce the release date again during checkout.")
end = text.index("    # 6. Add Coming Soon / New labels to catalog cards.", start)

checkout = r'''    # 5. Enforce the release date again during checkout.
    name = "pageturner/blueprints/shop_checkout.py"
    checkout_line = '                raise ValueError("Your checkout reservation expired. Please open checkout again.")\n'
    checkout_replacement = (
        '                if book["release_date"] and book["release_date"] > datetime.now().date().isoformat():\n'
        '                    raise ValueError(f"{book[\'title\']} is coming soon and cannot be ordered yet.")\n'
        + checkout_line
    )
    updated[name] = once(
        updated[name],
        checkout_line,
        checkout_replacement,
        "checkout release-date validation",
    )

'''

text = text[:start] + checkout + text[end:]
path.write_text(text, encoding="utf-8")
print("Patch script repaired.")
