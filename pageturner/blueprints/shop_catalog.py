
"""Book listing and book detail pages."""

from datetime import date, timedelta
from flask import abort, render_template, request, session
from ..db import get_db
from ..helpers import current_user, rupees_to_paise
from .shop import shop_bp, customers_also_bought


def parse_price_filter(value):
    """Convert a rupee price filter into paise, or None when unset/invalid."""
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        paise = rupees_to_paise(text)
    except ValueError:
        return None

    return paise if paise > 0 else None


@shop_bp.get("/")
def index():
    connection = get_db()
    user = current_user()

    query = request.args.get("q", "").strip()[:100]

    category_values = [
        value
        for value in request.args.getlist("category")
        if value.isdigit()
    ]

    category = (
        category_values[0]
        if category_values
        else request.args.get("category", "").strip()
    )

    category_ids = list(dict.fromkeys(category_values))
    sort = request.args.get("sort", "title")

    sort_options = {
        "title": "b.title COLLATE NOCASE",
        "price": "b.price_paise",
        "newest": "b.created_at DESC, b.id DESC",
        "relevance": "relevance DESC, b.title COLLATE NOCASE",
        "rating": "average_rating DESC, b.title COLLATE NOCASE",
        "popularity": "sold DESC, b.title COLLATE NOCASE",
    }

    sort_sql = sort_options.get(sort, sort_options["title"])

    # Optional filters: price range, minimum rating and stock availability.
    min_price = parse_price_filter(request.args.get("min_price"))
    max_price = parse_price_filter(request.args.get("max_price"))

    try:
        min_rating = int(request.args.get("min_rating", "0"))
    except (ValueError, TypeError):
        min_rating = 0

    min_rating = min_rating if 1 <= min_rating <= 5 else 0
    in_stock = request.args.get("in_stock", "").strip() in ("1", "true", "yes")

    try:
        page = max(1, int(request.args.get("page", "1")))
    except (ValueError, TypeError):
        page = 1

    page_size = 6
    conditions = []
    where_params = []
    relevance_sql = "0 AS relevance"
    relevance_params = []

    if query:
        conditions.append(
            "(b.title LIKE ? OR b.author LIKE ? OR EXISTS "
            "(SELECT 1 FROM book_tags bt "
            "JOIN tags t ON t.id=bt.tag_id "
            "WHERE bt.book_id=b.id AND t.name LIKE ?))"
        )

        like = f"%{query}%"
        where_params.extend([like, like, like])

        relevance_sql = (
            "(CASE WHEN b.title LIKE ? THEN 3 "
            "WHEN b.author LIKE ? THEN 2 ELSE 1 END) AS relevance"
        )
        relevance_params = [like, like]

    if category_ids:
        marks = ",".join("?" for _ in category_ids)

        conditions.append(
            f"b.id IN (SELECT book_id FROM book_categories "
            f"WHERE category_id IN ({marks}) "
            "GROUP BY book_id "
            "HAVING COUNT(DISTINCT category_id) = ?)"
        )

        where_params.extend(int(value) for value in category_ids)
        where_params.append(len(category_ids))

    elif category:
        conditions.append(
            "(b.category = ? OR EXISTS "
            "(SELECT 1 FROM book_categories bc "
            "JOIN categories c ON c.id=bc.category_id "
            "WHERE bc.book_id=b.id AND c.name=?))"
        )
        where_params.extend([category, category])

    if min_price is not None:
        conditions.append("b.price_paise >= ?")
        where_params.append(min_price)

    if max_price is not None:
        conditions.append("b.price_paise <= ?")
        where_params.append(max_price)

    if min_rating:
        conditions.append(
            "(SELECT AVG(r.rating) FROM reviews r "
            "WHERE r.book_id=b.id AND r.status='published') >= ?"
        )
        where_params.append(min_rating)

    if in_stock:
        conditions.append("b.stock > 0")

    where_sql = (
        " WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    count = connection.execute(
        "SELECT COUNT(*) AS count FROM books b" + where_sql,
        where_params,
    ).fetchone()["count"]

    page_count = max(1, (count + page_size - 1) // page_size)
    page = min(page, page_count)

    query_params = (
        relevance_params
        + where_params
        + [page_size, (page - 1) * page_size]
    )

    books = connection.execute(
        f"SELECT b.*, {relevance_sql}, "
        "(SELECT AVG(r.rating) FROM reviews r "
        "WHERE r.book_id=b.id AND r.status='published') AS average_rating, "
        "(SELECT COUNT(*) FROM order_items oi "
        "WHERE oi.book_id=b.id) AS sold "
        f"FROM books b{where_sql} "
        f"ORDER BY {sort_sql} LIMIT ? OFFSET ?",
        query_params,
    ).fetchall()

    categories = connection.execute(
        "SELECT c.id, c.name, COUNT(bc.book_id) AS book_count "
        "FROM categories c "
        "LEFT JOIN book_categories bc ON bc.category_id=c.id "
        "GROUP BY c.id ORDER BY c.name"
    ).fetchall()

    wishlist_ids = set()

    if user:
        wishlist_rows = connection.execute(
            "SELECT book_id FROM wishlist WHERE user_id=?",
            (user["id"],),
        ).fetchall()

        wishlist_ids = {row["book_id"] for row in wishlist_rows}

    # Step 42: retrieve the five most recently viewed existing books.
    recent_ids = session.get("recently_viewed", [])
    recently_viewed = []

    if recent_ids:
        placeholders = ",".join("?" for _ in recent_ids)

        recent_rows = connection.execute(
            "SELECT b.*, "
            "(SELECT AVG(r.rating) FROM reviews r "
            "WHERE r.book_id=b.id AND r.status='published') "
            "AS average_rating "
            f"FROM books b WHERE b.id IN ({placeholders})",
            recent_ids,
        ).fetchall()

        books_by_id = {row["id"]: row for row in recent_rows}

        # Preserve the viewing order and skip deleted books.
        recently_viewed = [
            books_by_id[book_id]
            for book_id in recent_ids
            if book_id in books_by_id
        ]

    return render_template(
        "index.html",
        books=books,
        categories=categories,
        query=query,
        category=category,
        category_ids=category_ids,
        sort=sort,
        page=page,
        page_count=page_count,
        count=count,
        current_user=user,
        wishlist_ids=wishlist_ids,
        recently_viewed=recently_viewed,
        today_date=date.today().isoformat(),
        new_arrivals_cutoff=(date.today() - timedelta(days=14)).isoformat(),
    )


@shop_bp.get("/book/<int:book_id>")
def book_detail(book_id):
    connection = get_db()

    book = connection.execute(
        "SELECT b.*, "
        "(SELECT AVG(r.rating) FROM reviews r "
        "WHERE r.book_id=b.id AND r.status='published') "
        "AS average_rating "
        "FROM books b WHERE b.id=?",
        (book_id,),
    ).fetchone()

    if not book:
        abort(404)

    # Step 42: record this view, moving duplicates to the front.
    recent_ids = session.get("recently_viewed", [])

    recent_ids = [
        viewed_id
        for viewed_id in recent_ids
        if viewed_id != book_id
    ]

    recent_ids.insert(0, book_id)
    session["recently_viewed"] = recent_ids[:5]

    reviews = connection.execute(
        "SELECT * FROM reviews WHERE book_id=? ORDER BY id DESC",
        (book_id,),
    ).fetchall()

    tags = connection.execute(
        "SELECT t.name FROM tags t "
        "JOIN book_tags bt ON bt.tag_id=t.id "
        "WHERE bt.book_id=? ORDER BY t.name",
        (book_id,),
    ).fetchall()

    user = current_user()
    user_review = None
    is_wishlisted = False

    if user:
        user_review = connection.execute(
            "SELECT id FROM reviews WHERE book_id=? AND user_id=?",
            (book_id, user["id"]),
        ).fetchone()

        wishlist_entry = connection.execute(
            "SELECT 1 FROM wishlist WHERE user_id=? AND book_id=?",
            (user["id"], book_id),
        ).fetchone()

        is_wishlisted = wishlist_entry is not None

    return render_template(
        "book.html",
        book=book,
        reviews=reviews,
        tags=tags,
        user_review=user_review,
        is_wishlisted=is_wishlisted,
        also_bought=customers_also_bought(book_id),
        today_date=date.today().isoformat(),
        new_arrivals_cutoff=(date.today() - timedelta(days=14)).isoformat(),
    )


# Step 43: Show all books by an author.
@shop_bp.get("/author/<string:author_name>")
def author_page(author_name):
    connection = get_db()

    # One query gets the books, ratings, and total book count.
    books = connection.execute(
        "SELECT b.*, "
        "(SELECT AVG(r.rating) FROM reviews r "
        "WHERE r.book_id = b.id) AS average_rating, "
        "COUNT(*) OVER() AS author_book_count "
        "FROM books b "
        "WHERE b.author = ? COLLATE NOCASE "
        "ORDER BY b.title COLLATE NOCASE",
        (author_name,),
    ).fetchall()

    # No books means the author does not exist.
    if not books:
        abort(404)

    return render_template(
        "author.html",
        author_name=books[0]["author"],
        books=books,
        author_book_count=books[0]["author_book_count"],
    )

# Step 45: Compare exactly two books.
@shop_bp.get("/compare")
def compare_books():
    connection = get_db()
    selected_ids = request.args.getlist("book_ids")

    # Exactly two distinct books must be selected.
    if len(selected_ids) != 2 or selected_ids[0] == selected_ids[1]:
        return render_template(
            "compare.html",
            books=[],
            error="Please select exactly two different books to compare."
        ), 400

    # Reject invalid book IDs.
    try:
        book_ids = [int(book_id) for book_id in selected_ids]
    except (ValueError, TypeError):
        return render_template(
            "compare.html",
            books=[],
            error="Invalid book selection. Please select two books."
        ), 400

    placeholders = ",".join("?" for _ in book_ids)

    books = connection.execute(
        f"""
        SELECT b.*,
               (SELECT AVG(r.rating)
                FROM reviews r
                WHERE r.book_id = b.id
                  AND r.status='published') AS average_rating
        FROM books b
        WHERE b.id IN ({placeholders})
        """,
        book_ids
    ).fetchall()

    if len(books) != 2:
        return render_template(
            "compare.html",
            books=[],
            error="One or both selected books could not be found."
        ), 404

    # Keep the books in the same order in which they were selected.
    books_by_id = {book["id"]: book for book in books}
    books = [books_by_id[book_id] for book_id in book_ids]

    return render_template(
        "compare.html",
        books=books,
        error=None
    )
