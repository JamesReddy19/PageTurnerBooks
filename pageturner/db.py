"""Database helpers. Money is stored as integer paise."""
import sqlite3
from pathlib import Path
from flask import current_app, g


def get_db():
    if "db" not in g:
        database_path = current_app.config["DATABASE"]
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(database_path, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = get_db()
    schema_path = Path(current_app.root_path).parent / "schema.sql"
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    version = connection.execute("SELECT COUNT(*) AS count FROM schema_version").fetchone()["count"]
    if version == 0:
        connection.execute("INSERT INTO schema_version(version) VALUES (1)")
    connection.commit()
    seed_categories(connection)
    seed_shipping_zones(connection)


def seed_categories(connection):
    names = ["Biography", "Fiction", "History", "Science", "Self-Help", "Technology"]
    for name in names:
        connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))

    for book in connection.execute("SELECT id, category FROM books").fetchall():
        # A book may belong to several categories, stored as comma-separated names in the legacy field.
        for name in {value.strip() for value in book["category"].split(",") if value.strip()}:
            connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
            category = connection.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            connection.execute("INSERT OR IGNORE INTO book_categories(book_id, category_id) VALUES (?, ?)", (book["id"], category["id"]))
    connection.commit()


def seed_shipping_zones(connection):
    for prefix in ("50", "51", "52", "53", "56", "60", "11", "40"):
        connection.execute("INSERT OR IGNORE INTO shipping_zones(prefix, fee_paise) VALUES (?, ?)", (prefix, 5000))
    connection.commit()
