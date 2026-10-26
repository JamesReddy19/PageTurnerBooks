
"""Staff and administrator pages. Staff cannot manage books or export reports."""

import os
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash

from ..db import get_db
from ..helpers import (
    log_admin_action,
    role_required,
    rupees_to_paise,
    ORDER_STATUS_TRANSITIONS,
)
from ..pricing import price_history_for_books


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED_STATUSES = ORDER_STATUS_TRANSITIONS

ALLOWED_IMAGE_TYPES = {
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".webp": b"RIFF",
}


def staff_or_admin(view):
    return role_required("staff", "admin")(view)


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        email_key = "__admin_password__"
        ip_address = request.remote_addr or "unknown"
        connection = get_db()

        attempt = connection.execute(
            "SELECT * FROM login_attempts WHERE email=? AND ip_address=?",
            (email_key, ip_address),
        ).fetchone()

        now = datetime.now()

        if (
            attempt
            and attempt["locked_until"]
            and attempt["locked_until"] > now.isoformat(timespec="seconds")
        ):
            flash(
                "Too many failed attempts. Please wait 10 minutes before trying again.",
                "error",
            )
            return render_template("admin_login.html"), 429

        # Verify the password against an existing admin account.
        admin_email = str(
            current_app.config.get("ADMIN_EMAIL", "")
        ).strip().lower()

        if admin_email:
            admin_users = connection.execute(
                """
                SELECT id, email, password_hash
                FROM users
                WHERE lower(email) = ? AND role = 'admin'
                """,
                (admin_email,),
            ).fetchall()
        else:
            # If ADMIN_EMAIL is not configured, check existing admin accounts.
            admin_users = connection.execute(
                """
                SELECT id, email, password_hash
                FROM users
                WHERE role = 'admin'
                """
            ).fetchall()

        valid_admin = next(
            (
                user
                for user in admin_users
                if user["password_hash"]
                and check_password_hash(user["password_hash"], password)
            ),
            None,
        )

        if valid_admin:
            connection.execute(
                "DELETE FROM login_attempts WHERE email=? AND ip_address=?",
                (email_key, ip_address),
            )
            connection.commit()

            session.clear()
            session["admin_password_login"] = True
            session["csrf_token"] = __import__("secrets").token_urlsafe(32)

            return redirect(url_for("admin.dashboard"))

        # Keep tracking failed attempts.
        if (
            not attempt
            or (
                now - datetime.fromisoformat(attempt["window_started"])
            ).total_seconds() > 600
        ):
            connection.execute(
                """
                INSERT INTO login_attempts
                    (email, ip_address, failed_count, window_started)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(email, ip_address)
                DO UPDATE SET
                    failed_count=1,
                    window_started=excluded.window_started,
                    locked_until=NULL
                """,
                (
                    email_key,
                    ip_address,
                    now.isoformat(timespec="seconds"),
                ),
            )
        else:
            failed_count = attempt["failed_count"] + 1

            locked_until = (
                (now + timedelta(minutes=10)).isoformat(timespec="seconds")
                if failed_count >= 5
                else None
            )

            connection.execute(
                """
                UPDATE login_attempts
                SET failed_count=?, locked_until=?
                WHERE email=? AND ip_address=?
                """,
                (failed_count, locked_until, email_key, ip_address),
            )

        connection.commit()
        flash("Admin password is incorrect.", "error")

    return render_template("admin_login.html")


@admin_bp.get("/logout")
def logout():
    session.pop("admin_password_login", None)
    flash("Admin password session ended.", "success")
    return redirect(url_for("shop.index"))


@admin_bp.get("/")
@role_required("admin")
def dashboard():
    connection = get_db()

    # Date range: today and the previous six days.
    today = datetime.now().date()
    start_date = today - timedelta(days=6)

    start = start_date.isoformat()
    end = (today + timedelta(days=1)).isoformat()

    summary = connection.execute(
        """
        SELECT
            COUNT(*) AS order_count,
            COALESCE(SUM(total_paise), 0) AS revenue
        FROM orders
        WHERE date(date) >= ?
          AND date(date) < ?
          AND status != 'Cancelled'
        """,
        (start, end),
    ).fetchone()

    inventory = connection.execute(
        """
        SELECT COALESCE(SUM(stock), 0) AS total_stock
        FROM books
        """
    ).fetchone()

    sales = connection.execute(
        """
        SELECT COALESCE(SUM(oi.quantity), 0) AS total_sold
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status != 'Cancelled'
        """
    ).fetchone()

    top_books = connection.execute(
        """
        SELECT b.title, SUM(oi.quantity) AS sold
        FROM order_items oi
        JOIN books b ON b.id = oi.book_id
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status != 'Cancelled'
        GROUP BY b.id
        ORDER BY sold DESC
        LIMIT 5
        """
    ).fetchall()

    low_stock = connection.execute(
        """
        SELECT id, title, stock
        FROM books
        WHERE stock <= 3
        ORDER BY stock, title
        LIMIT 10
        """
    ).fetchall()

    statuses = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM orders
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()

    # Fetch revenue and orders for dates that have order records.
    daily_rows = connection.execute(
        """
        SELECT
            date(date) AS day,
            COUNT(*) AS orders,
            COALESCE(SUM(total_paise), 0) AS revenue
        FROM orders
        WHERE date(date) >= ?
          AND date(date) < ?
          AND status != 'Cancelled'
        GROUP BY date(date)
        ORDER BY day
        """,
        (start, end),
    ).fetchall()

    # Create a lookup by date so missing dates can be filled with zeroes.
    daily_by_date = {
        row["day"]: row
        for row in daily_rows
    }

    daily = []

    for day_offset in range(7):
        current_date = start_date + timedelta(days=day_offset)
        date_string = current_date.isoformat()
        row = daily_by_date.get(date_string)

        daily.append(
            {
                "day": date_string,
                "orders": row["orders"] if row else 0,
                "revenue": row["revenue"] if row else 0,
            }
        )

    category_sales = connection.execute(
        """
        SELECT
            c.name,
            COALESCE(
                SUM(
                    CASE
                        WHEN o.status != 'Cancelled' THEN oi.quantity
                        ELSE 0
                    END
                ),
                0
            ) AS sold
        FROM categories c
        LEFT JOIN book_categories bc ON bc.category_id = c.id
        LEFT JOIN order_items oi ON oi.book_id = bc.book_id
        LEFT JOIN orders o ON o.id = oi.order_id
        GROUP BY c.id
        ORDER BY sold DESC
        LIMIT 5
        """
    ).fetchall()

    return render_template(
        "admin.html",
        summary=summary,
        inventory=inventory,
        sales=sales,
        top_books=top_books,
        low_stock=low_stock,
        statuses=statuses,
        daily=daily,
        category_sales=category_sales,
    )


@admin_bp.get("/books")
@role_required("admin")
def manage_books():
    books = get_db().execute(
        """
        SELECT
            b.*,
            (
                SELECT GROUP_CONCAT(t.name, ', ')
                FROM book_tags bt
                JOIN tags t ON t.id = bt.tag_id
                WHERE bt.book_id = b.id
            ) AS tags_text
        FROM books b
        ORDER BY b.id DESC
        """
    ).fetchall()

    categories = get_db().execute(
        "SELECT * FROM categories ORDER BY name"
    ).fetchall()

    tags = get_db().execute(
        "SELECT * FROM tags ORDER BY name"
    ).fetchall()

    # Recent price changes per book, newest first (req 54/55 audit trail).
    from ..pricing import price_history_for_books

    price_history = price_history_for_books(
        get_db(), [book["id"] for book in books], per_book=3
    )

    return render_template(
        "manage_books.html",
        books=books,
        categories=categories,
        tags=tags,
        price_history=price_history,
    )


def read_book_form():
    title = request.form.get("title", "").strip()[:200]
    author = request.form.get("author", "").strip()[:150]
    category = request.form.get("category", "").strip()[:80]
    description = request.form.get("description", "").strip()[:5000]
    color = request.form.get("color", "#e8f1ef").strip()[:20]

    try:
        price_paise = rupees_to_paise(request.form.get("price", ""))
        stock = int(request.form.get("stock", "0"))
    except ValueError as error:
        raise ValueError(str(error))

    if (
        not title
        or not author
        or not category
        or stock < 0
        or stock > 100000
    ):
        raise ValueError(
            "Enter title, author, category, and a valid stock amount."
        )

    return title, author, category, price_paise, description, stock, color


def save_uploaded_image():
    uploaded = request.files.get("image_file")

    if not uploaded or not uploaded.filename:
        return ""

    safe_name = secure_filename(uploaded.filename)
    extension = Path(safe_name).suffix.lower()

    if extension not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Upload a JPG, PNG or WEBP image.")

    header = uploaded.stream.read(12)
    uploaded.stream.seek(0)

    signature = ALLOWED_IMAGE_TYPES[extension]

    if not header.startswith(signature) or (
        extension == ".webp" and header[8:12] != b"WEBP"
    ):
        raise ValueError(
            "The file contents do not match the image extension."
        )

    filename = f"{__import__('secrets').token_hex(12)}{extension}"

    uploaded.save(
        os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    )

    return "uploads/" + filename


def save_categories_and_tags(book_id, category, tags_text):
    connection = get_db()

    category_names = {
        value.strip()
        for value in category.split(",")
        if value.strip()
    }

    if not category_names:
        category_names = {category.strip()}

    category_ids = set()

    for name in category_names:
        connection.execute(
            "INSERT OR IGNORE INTO categories(name) VALUES (?)",
            (name,),
        )

        category_id = connection.execute(
            "SELECT id FROM categories WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()["id"]

        category_ids.add(category_id)

        connection.execute(
            """
            INSERT OR IGNORE INTO book_categories(book_id, category_id)
            VALUES (?, ?)
            """,
            (book_id, category_id),
        )

    existing_categories = connection.execute(
        "SELECT category_id FROM book_categories WHERE book_id=?",
        (book_id,),
    ).fetchall()

    for row in existing_categories:
        if row["category_id"] not in category_ids:
            connection.execute(
                """
                DELETE FROM book_categories
                WHERE book_id=? AND category_id=?
                """,
                (book_id, row["category_id"]),
            )

    connection.execute(
        "DELETE FROM book_tags WHERE book_id=?",
        (book_id,),
    )

    tags = {
        value.strip().lower()
        for value in tags_text.split(",")
        if value.strip()
    }

    for tag in tags:
        connection.execute(
            "INSERT OR IGNORE INTO tags(name) VALUES (?)",
            (tag,),
        )

        tag_id = connection.execute(
            "SELECT id FROM tags WHERE name=? COLLATE NOCASE",
            (tag,),
        ).fetchone()["id"]

        connection.execute(
            """
            INSERT OR IGNORE INTO book_tags(book_id, tag_id)
            VALUES (?, ?)
            """,
            (book_id, tag_id),
        )


# Import route modules after the shared Blueprint and helpers exist.
from . import (  # noqa: E402,F401
    admin_books,
    admin_orders,
    admin_coupons,
    admin_returns,
    admin_reports,
    admin_import,
    admin_bundles,
    admin_sales,
    admin_users,
)
