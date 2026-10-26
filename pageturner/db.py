
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


def backfill_price_history_baseline(connection):
    """Give every book a starting price-history row exactly once.

    Requirement 54 needs a known starting price for the trail to be
    meaningful.  This is idempotent: it only inserts for books that have no
    history at all, so it is safe to run on every boot and after seeding.
    """
    connection.execute(
        """
        INSERT INTO book_price_history
            (book_id, old_price_paise, new_price_paise, reason, actor_user_id)
        SELECT b.id, NULL, b.price_paise, 'initial price', NULL
        FROM books b
        WHERE NOT EXISTS (
            SELECT 1 FROM book_price_history h WHERE h.book_id = b.id
        )
        """
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

    # Optimistic locking for concurrent editorial edits (requirement 66).
    add_column_if_missing(
        connection, "books", "version", "INTEGER NOT NULL DEFAULT 1"
    )

    # Hash-chained audit log (requirement 68).  Columns are added to existing
    # databases here; schema.sql defines them for fresh installs.
    add_column_if_missing(connection, "admin_log", "prev_hash", "TEXT")
    add_column_if_missing(connection, "admin_log", "row_hash", "TEXT")

    # Review moderation and account anonymization (requirements 50, 51, 59).
    # Remember: hidden reviews must vanish from the average rating *and* the
    # review count, so every rating query filters on this column.
    add_column_if_missing(
        connection, "reviews", "hidden", "INTEGER NOT NULL DEFAULT 0"
    )
    add_column_if_missing(connection, "reviews", "hidden_at", "TEXT")
    add_column_if_missing(connection, "reviews", "hidden_by", "INTEGER")
    add_column_if_missing(connection, "users", "anonymized_at", "TEXT")

    # Requirement 50/51/52/53 tables.  schema.sql declares these for fresh
    # installs; repeating them here migrates databases created before batch 4,
    # which otherwise would fail at runtime with "no such table".
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL
                REFERENCES reviews(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(review_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS review_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL
                REFERENCES reviews(id) ON DELETE CASCADE,
            reporter_user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'hidden', 'kept')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            resolved_by INTEGER REFERENCES users(id),
            UNIQUE(review_id, reporter_user_id)
        );

        CREATE TABLE IF NOT EXISTS book_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_title TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            request_count INTEGER NOT NULL DEFAULT 1
                CHECK (request_count > 0),
            first_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wishlist_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE
                REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_review_votes_review
            ON review_votes(review_id);
        CREATE INDEX IF NOT EXISTS idx_review_reports_status
            ON review_reports(status, review_id);
        CREATE INDEX IF NOT EXISTS idx_book_requests_count
            ON book_requests(request_count DESC);
        """
    )

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_chain_head (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_row_id INTEGER NOT NULL DEFAULT 0,
            last_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS book_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL
                REFERENCES books(id) ON DELETE CASCADE,
            old_price_paise INTEGER
                CHECK(old_price_paise IS NULL OR old_price_paise >= 0),
            new_price_paise INTEGER NOT NULL
                CHECK(new_price_paise >= 0),
            reason TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER REFERENCES users(id),
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_price_history_book
            ON book_price_history(book_id, changed_at);
        """
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

    # One-time baseline for price history: books that have never been edited
    # through set_book_price get a single "initial price" row so the audit
    # trail starts from a known value.  Existing price rows are never touched.
    backfill_price_history_baseline(connection)

    connection.commit()

    # Seal legacy audit rows into the hash chain (idempotent: only rows whose
    # row_hash is still NULL are hashed, and an existing head is preserved).
    from .audit import seal_chain

    if seal_chain(connection):
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
