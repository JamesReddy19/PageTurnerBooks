"""Read-only JSON API with a simple SQLite-backed rate limit."""
from flask import Blueprint, abort, jsonify, request
from ..db import get_db
from ..helpers import rupees_to_paise

api_bp = Blueprint("api", __name__, url_prefix="/api")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@api_bp.before_request
def rate_limit():
    connection = get_db()
    ip_address = request.remote_addr or "unknown"
    connection.execute("DELETE FROM api_requests WHERE created_at < datetime('now', '-1 minute')")
    count = connection.execute("SELECT COUNT(*) AS count FROM api_requests WHERE ip_address=? AND created_at>=datetime('now', '-1 minute')", (ip_address,)).fetchone()["count"]
    if count >= 30:
        connection.commit()
        abort(429)
    connection.execute("INSERT INTO api_requests(ip_address) VALUES (?)", (ip_address,))
    connection.commit()


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


def book_json(book):
    if not book:
        return None
    rating = book["average_rating"] if "average_rating" in book.keys() else None
    review_count = book["review_count"] if "review_count" in book.keys() else 0
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "category": book["category"],
        "price_paise": book["price_paise"],
        "price": f"{book['price_paise'] // 100}.{book['price_paise'] % 100:02d}",
        "stock": book["stock"],
        "image": book["image"],
        "average_rating": round(float(rating), 2) if rating is not None else None,
        "review_count": review_count,
    }


BOOK_SELECT = (
    "SELECT b.*, "
    "(SELECT AVG(r.rating) FROM reviews r "
    "WHERE r.book_id=b.id AND r.status='published') AS average_rating, "
    "(SELECT COUNT(*) FROM reviews r "
    "WHERE r.book_id=b.id AND r.status='published') AS review_count "
    "FROM books b"
)


@api_bp.get("/books")
def books():
    query = request.args.get("q", "").strip()[:100]
    connection = get_db()

    conditions = []
    where_params = []

    if query:
        like = f"%{query}%"
        conditions.append(
            "(b.title LIKE ? OR b.author LIKE ? OR EXISTS "
            "(SELECT 1 FROM book_tags bt "
            "JOIN tags t ON t.id=bt.tag_id "
            "WHERE bt.book_id=b.id AND t.name LIKE ?))"
        )
        where_params.extend([like, like, like])

    min_price = parse_price_filter(request.args.get("min_price"))
    max_price = parse_price_filter(request.args.get("max_price"))

    if min_price is not None:
        conditions.append("b.price_paise >= ?")
        where_params.append(min_price)

    if max_price is not None:
        conditions.append("b.price_paise <= ?")
        where_params.append(max_price)

    try:
        min_rating = int(request.args.get("min_rating", "0"))
    except (TypeError, ValueError):
        min_rating = 0

    if 1 <= min_rating <= 5:
        conditions.append(
            "(SELECT AVG(r.rating) FROM reviews r "
            "WHERE r.book_id=b.id AND r.status='published') >= ?"
        )
        where_params.append(min_rating)

    if request.args.get("in_stock", "").strip() in ("1", "true", "yes"):
        conditions.append("b.stock > 0")

    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = connection.execute(
        "SELECT COUNT(*) AS count FROM books b" + where_sql,
        where_params,
    ).fetchone()["count"]

    try:
        page = max(1, int(request.args.get("page", "1")))
    except (TypeError, ValueError):
        page = 1

    try:
        page_size = int(request.args.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE

    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(page, page_count)

    order_sql = "b.title COLLATE NOCASE"

    if query:
        relevance_sql = (
            "(CASE WHEN b.title LIKE ? THEN 3 "
            "WHEN b.author LIKE ? THEN 2 ELSE 1 END)"
        )
        like = f"%{query}%"
        rows = connection.execute(
            BOOK_SELECT
            + where_sql
            + f" ORDER BY {relevance_sql} DESC, {order_sql} LIMIT ? OFFSET ?",
            [like, like] + where_params + [page_size, (page - 1) * page_size],
        ).fetchall()
    else:
        rows = connection.execute(
            BOOK_SELECT
            + where_sql
            + f" ORDER BY {order_sql} LIMIT ? OFFSET ?",
            where_params + [page_size, (page - 1) * page_size],
        ).fetchall()

    return jsonify({
        "books": [book_json(row) for row in rows],
        "count": len(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
    })


@api_bp.get("/books/<int:book_id>")
def book(book_id):
    row = get_db().execute(
        BOOK_SELECT + " WHERE b.id=?",
        (book_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "Book not found"}), 404
    payload = book_json(row)
    payload["reviews"] = [
        {
            "id": review["id"],
            "name": review["name"],
            "rating": review["rating"],
            "comment": review["comment"],
            "created_at": review["created_at"],
        }
        for review in get_db().execute(
            "SELECT id,name,rating,comment,created_at FROM reviews "
            "WHERE book_id=? AND status='published' ORDER BY id DESC",
            (book_id,),
        ).fetchall()
    ]
    return jsonify(payload)


@api_bp.errorhandler(404)
def api_not_found(_error):
    return jsonify({"error": "API resource not found"}), 404
