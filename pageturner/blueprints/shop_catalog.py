
"""Book listing and book detail pages."""

from flask import abort, render_template, request, session
from ..db import get_db
from ..helpers import current_user
from .shop import shop_bp, customers_also_bought


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
    }

    sort_sql = sort_options.get(sort, sort_options["title"])

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
        "WHERE r.book_id=b.id) AS average_rating "
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
    )


@shop_bp.get("/book/<int:book_id>")
def book_detail(book_id):
    connection = get_db()

    book = connection.execute(
        "SELECT b.*, "
        "(SELECT AVG(r.rating) FROM reviews r "
        "WHERE r.book_id=b.id) AS average_rating "
        "FROM books b WHERE b.id=?",
        (book_id,),
    ).fetchone()

    if not book:
        abort(404)

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

    if user:
        user_review = connection.execute(
            "SELECT id FROM reviews WHERE book_id=? AND user_id=?",
            (book_id, user["id"]),
        ).fetchone()

    return render_template(
        "book.html",
        book=book,
        reviews=reviews,
        tags=tags,
        user_review=user_review,
        also_bought=customers_also_bought(book_id),
    )
