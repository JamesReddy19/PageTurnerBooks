
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


def add_column_if_missing(connection, table, column, definition):
    """Add a column only when it does not already exist."""
    columns = {
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }

    if column not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    connection = get_db()

    # Keep the project's existing schema initialization.
    schema_path = Path(current_app.root_path).parent / "schema.sql"
    connection.executescript(
        schema_path.read_text(encoding="utf-8")
    )

    # Additive migrations: existing rows and tables are preserved.
    add_column_if_missing(
        connection, "books", "release_date", "TEXT"
    )
    add_column_if_missing(
        connection, "books", "sale_price_paise", "INTEGER"
    )
    add_column_if_missing(
        connection, "books", "sale_start", "TEXT"
    )
    add_column_if_missing(
        connection, "books", "sale_end", "TEXT"
    )

    add_column_if_missing(
        connection, "orders", "gift_wrap_paise",
        "INTEGER NOT NULL DEFAULT 0"
    )
    add_column_if_missing(
        connection, "orders", "gift_message", "TEXT"
    )
    add_column_if_missing(
        connection, "orders", "loyalty_points_earned",
        "INTEGER NOT NULL DEFAULT 0"
    )
    add_column_if_missing(
        connection, "orders", "loyalty_points_redeemed",
        "INTEGER NOT NULL DEFAULT 0"
    )

    # Combo bundle definitions.
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            price_paise INTEGER NOT NULL CHECK (price_paise >= 0),
            active INTEGER NOT NULL DEFAULT 1
                CHECK (active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bundle_items (
            bundle_id INTEGER NOT NULL
                REFERENCES bundles(id) ON DELETE CASCADE,
            book_id INTEGER NOT NULL
                REFERENCES books(id) ON DELETE RESTRICT,
            quantity INTEGER NOT NULL DEFAULT 1
                CHECK (quantity BETWEEN 1 AND 99),
            PRIMARY KEY (bundle_id, book_id)
        );

        CREATE TABLE IF NOT EXISTS cart_bundles (
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            bundle_id INTEGER NOT NULL
                REFERENCES bundles(id) ON DELETE CASCADE,
            quantity INTEGER NOT NULL DEFAULT 1
                CHECK (quantity BETWEEN 1 AND 99),
            PRIMARY KEY (user_id, bundle_id)
        );

        CREATE TABLE IF NOT EXISTS order_bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL
                REFERENCES orders(id) ON DELETE CASCADE,
            bundle_id INTEGER
                REFERENCES bundles(id) ON DELETE SET NULL,
            bundle_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            price_paise INTEGER NOT NULL CHECK (price_paise >= 0)
        );

        CREATE TABLE IF NOT EXISTS order_bundle_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_bundle_id INTEGER NOT NULL
                REFERENCES order_bundles(id) ON DELETE CASCADE,
            book_id INTEGER
                REFERENCES books(id) ON DELETE SET NULL,
            book_title TEXT NOT NULL,
            quantity_per_bundle INTEGER NOT NULL
                CHECK (quantity_per_bundle > 0)
        );

        -- A ledger records every loyalty credit and debit.
        -- The current balance is calculated with SUM(points_change).
        CREATE TABLE IF NOT EXISTS points_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            order_id INTEGER
                REFERENCES orders(id) ON DELETE SET NULL,
            points_change INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_points_ledger_user
            ON points_ledger(user_id);

        CREATE INDEX IF NOT EXISTS idx_points_ledger_order
            ON points_ledger(order_id);

        CREATE INDEX IF NOT EXISTS idx_bundle_items_book
            ON bundle_items(book_id);
        """
    )

    version_row = connection.execute(
        "SELECT COUNT(*) AS count FROM schema_version"
    ).fetchone()

    if version_row["count"] == 0:
        connection.execute(
            "INSERT INTO schema_version(version) VALUES (1)"
        )

    connection.commit()

    seed_categories(connection)
    seed_shipping_zones(connection)


def seed_categories(connection):
    names = [
        "Biography",
        "Fiction",
        "History",
        "Science",
        "Self-Help",
        "Technology",
    ]

    for name in names:
        connection.execute(
            "INSERT OR IGNORE INTO categories(name) VALUES (?)",
            (name,),
        )

    books = connection.execute(
        "SELECT id, category FROM books"
    ).fetchall()

    for book in books:
        # A legacy book may contain several comma-separated categories.
        for name in {
            value.strip()
            for value in (book["category"] or "").split(",")
            if value.strip()
        }:
            connection.execute(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                (name,),
            )

            category = connection.execute(
                """
                SELECT id FROM categories
                WHERE name = ? COLLATE NOCASE
                """,
                (name,),
            ).fetchone()

            connection.execute(
                """
                INSERT OR IGNORE INTO book_categories
                    (book_id, category_id)
                VALUES (?, ?)
                """,
                (book["id"], category["id"]),
            )

    connection.commit()


def seed_shipping_zones(connection):
    for prefix in ("50", "51", "52", "53", "56", "60", "11", "40"):
        connection.execute(
            """
            INSERT OR IGNORE INTO shipping_zones(prefix, fee_paise)
            VALUES (?, ?)
            """,
            (prefix, 5000),
        )

    connection.commit()
