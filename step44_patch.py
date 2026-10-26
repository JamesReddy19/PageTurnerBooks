from pathlib import Path
from datetime import datetime
import shutil
import sys
import re

root = Path.cwd()

files = [
    "schema.sql",
    "pageturner/db.py",
    "pageturner/blueprints/shop.py",
    "pageturner/blueprints/shop_catalog.py",
    "pageturner/blueprints/shop_checkout.py",
    "templates/index.html",
    "templates/book.html",
]

# Read all files first.
original = {}
for name in files:
    path = root / name
    if not path.is_file():
        sys.exit(f"STOP: Missing file: {name}")
    original[name] = path.read_text(encoding="utf-8")

updated = dict(original)

def once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}.")
    return text.replace(old, new, 1)

try:
    # 1. Add the release date column to fresh database schemas.
    name = "schema.sql"
    updated[name] = once(
        updated[name],
        " image TEXT NOT NULL DEFAULT '',\n",
        " image TEXT NOT NULL DEFAULT '',\n release_date TEXT,\n",
        "books schema column",
    )

    # 2. Add the column to existing databases without deleting data.
    name = "pageturner/db.py"
    updated[name] = once(
        updated[name],
        '    connection.executescript(schema_path.read_text(encoding="utf-8"))\n',
        '    connection.executescript(schema_path.read_text(encoding="utf-8"))\n'
        '    book_columns = {row["name"] for row in connection.execute("PRAGMA table_info(books)")}\n'
        '    if "release_date" not in book_columns:\n'
        '        connection.execute("ALTER TABLE books ADD COLUMN release_date TEXT")\n',
        "database migration insertion",
    )

    # 3. Block future-release books from being added to a cart.
    name = "pageturner/blueprints/shop.py"
    updated[name] = once(
        updated[name],
        '    book = connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()\n'
        '    if not book:\n'
        '        return False, "That book is no longer available."\n',
        '    book = connection.execute("SELECT stock, release_date FROM books WHERE id = ?", (book_id,)).fetchone()\n'
        '    if not book:\n'
        '        return False, "That book is no longer available."\n'
        '    if book["release_date"] and book["release_date"] > datetime.now().date().isoformat():\n'
        '        return False, "This book is coming soon and cannot be ordered yet."\n',
        "cart release-date validation",
    )

    # 4. Pass today's date and the 14-day cutoff to the templates.
    name = "pageturner/blueprints/shop_catalog.py"
    updated[name] = once(
        updated[name],
        'from flask import abort, render_template, request, session\n',
        'from datetime import date, timedelta\nfrom flask import abort, render_template, request, session\n',
        "catalog date import",
    )
    updated[name] = once(
        updated[name],
        "        recently_viewed=recently_viewed,\n",
        "        recently_viewed=recently_viewed,\n"
        "        today_date=date.today().isoformat(),\n"
        "        new_arrivals_cutoff=(date.today() - timedelta(days=14)).isoformat(),\n",
        "homepage template dates",
    )
    updated[name] = once(
        updated[name],
        '        also_bought=customers_also_bought(book_id),\n',
        '        also_bought=customers_also_bought(book_id),\n'
        '        today_date=date.today().isoformat(),\n'
        '        new_arrivals_cutoff=(date.today() - timedelta(days=14)).isoformat(),\n',
        "book detail template dates",
    )

    # 5. Enforce the release date again during checkout.
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

    # 6. Add Coming Soon / New labels to catalog cards.
    name = "templates/index.html"
    marker = '                        <div class="book-price-row">'
    if marker not in updated[name]:
        raise RuntimeError("Homepage book-price-row marker not found.")
    badge = (
        "                        {% if book['release_date'] and book['release_date'] > today_date %}\n"
        '                            <span class="release-ribbon coming-soon">Coming Soon</span>\n'
        "                        {% elif book['release_date'] and new_arrivals_cutoff <= book['release_date'] <= today_date %}\n"
        '                            <span class="release-ribbon new-arrival">New</span>\n'
        "                        {% endif %}\n\n"
    )
    updated[name] = updated[name].replace(marker, badge + marker)

    # 7. Add the labels and hide Add to Cart on future book details.
    name = "templates/book.html"
    updated[name] = once(
        updated[name],
        '<p class="price large-price">{{ money(book.price_paise) }}</p>\n',
        "{% if book.release_date and book.release_date > today_date %}\n"
        '<p class="release-ribbon coming-soon">Coming Soon</p>\n'
        "{% elif book.release_date and new_arrivals_cutoff <= book.release_date <= today_date %}\n"
        '<p class="release-ribbon new-arrival">New</p>\n'
        "{% endif %}\n"
        '<p class="price large-price">{{ money(book.price_paise) }}</p>\n',
        "book detail release label",
    )

    pattern = re.compile(
        r'(?P<indent>^[ \t]*)<form method="post" action="\{\{ url_for\(\'shop\.add_to_cart\', book_id=book\.id\) \}\}" class="inline-form">.*?</form>',
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(updated[name])
    if not match:
        raise RuntimeError("Could not find the book-detail Add to Cart form.")
    indent = match.group("indent")
    replacement = (
        indent + "{% if book.release_date and book.release_date > today_date %}\n"
        + indent + '    <p>This book is not available to order yet.</p>\n'
        + indent + "{% else %}\n"
        + indent + '    <form method="post" action="{{ url_for(\'shop.add_to_cart\', book_id=book.id) }}" class="inline-form">\n'
        + indent + '        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">\n'
        + indent + '        <label>Quantity <input type="number" name="quantity" value="1" min="1" max="99" required></label>\n'
        + indent + '        <button {% if book.stock < 1 %}disabled{% endif %}>Add to cart</button>\n'
        + indent + "    </form>\n"
        + indent + "{% endif %}"
    )
    updated[name] = updated[name][:match.start()] + replacement + updated[name][match.end():]

except Exception as error:
    print(f"STOP: {error}")
    print("No project source files were changed.")
    sys.exit(1)

# Verify that all expected changes were prepared before writing.
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = root / "backups" / f"step44_retry_{stamp}"

try:
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in files:
        target = backup_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
except Exception as error:
    print(f"STOP: Could not complete backup: {error}")
    print("No project source files were changed.")
    sys.exit(1)

written = []
try:
    for name in files:
        if updated[name] != original[name]:
            (root / name).write_text(updated[name], encoding="utf-8")
            written.append(name)
except Exception as error:
    print(f"Write error: {error}")
    print("Restore the changed files from this backup before proceeding:")
    print(backup_dir)
    sys.exit(1)

print("Patch applied successfully.")
print("Backup:", backup_dir)
print("Changed files:")
for name in written:
    print(" -", name)
