"""Read-only JSON API with a simple SQLite-backed rate limit."""
import base64
import json

from flask import Blueprint, abort, jsonify, request
from ..db import get_db

api_bp = Blueprint("api", __name__, url_prefix="/api")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


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


def encode_cursor(payload):
    """Encode a keyset position as a URL-safe, opaque cursor."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value):
    """
    Decode a cursor produced by :func:`encode_cursor`.

    Returns ``None`` when the value is not a cursor we produced, so the caller
    can answer 400 instead of silently restarting from the first page.
    """
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return {"relevance": int(payload["r"]), "title": str(payload["t"]), "id": int(payload["i"]), "query": str(payload.get("q", ""))}
    except (KeyError, TypeError, ValueError):
        return None


def page_size():
    """Read ``?limit=`` from a 1..MAX_PAGE_SIZE range, defaulting politely."""
    try:
        requested = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return max(1, min(requested, MAX_PAGE_SIZE))


def book_json(book):
    if not book:
        return None
    return {"id": book["id"], "title": book["title"], "author": book["author"], "category": book["category"], "price_paise": book["price_paise"], "price": f"{book['price_paise'] // 100}.{book['price_paise'] % 100:02d}", "stock": book["stock"], "image": book["image"]}


@api_bp.get("/books")
def books():
    """
    List books, one keyset page at a time.

    Ordering is ``relevance DESC, title COLLATE NOCASE, id`` — a total order, so
    the cursor is a true position rather than an offset. A matching
    ``next_cursor`` is returned only while more rows remain; pass it back as
    ``?after=`` for the next page.
    """
    query = request.args.get("q", "").strip()[:100]
    limit = page_size()

    cursor = None
    raw_cursor = request.args.get("after", "").strip()
    if raw_cursor:
        cursor = decode_cursor(raw_cursor)
        # A cursor is only meaningful for the search that produced it.
        if cursor is None or cursor["query"] != query:
            return jsonify({"error": "Invalid cursor"}), 400

    if query:
        like = f"%{query}%"
        relevance = "(CASE WHEN b.title LIKE ? THEN 3 WHEN b.author LIKE ? THEN 2 ELSE 1 END)"
        relevance_params = [like, like]
        where = "b.title LIKE ? OR b.author LIKE ? OR EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id=b.id AND t.name LIKE ?)"
        where_params = [like, like, like]
    else:
        relevance = "1"
        relevance_params = []
        where = "1=1"
        where_params = []

    sql = [f"SELECT * FROM (SELECT b.*, {relevance} AS relevance FROM books b WHERE {where}) AS p"]
    params = relevance_params + where_params

    if cursor:
        sql.append(
            "WHERE relevance < ? OR (relevance = ? AND (title COLLATE NOCASE > ? COLLATE NOCASE "
            "OR (title COLLATE NOCASE = ? COLLATE NOCASE AND id > ?)))"
        )
        params += [cursor["relevance"], cursor["relevance"], cursor["title"], cursor["title"], cursor["id"]]

    sql.append("ORDER BY relevance DESC, title COLLATE NOCASE ASC, id ASC LIMIT ?")
    # Ask for one extra row: its existence is how we know a next page exists.
    params.append(limit + 1)

    rows = get_db().execute(" ".join(sql), params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"r": last["relevance"], "t": last["title"], "i": last["id"], "q": query})

    payload = {"books": [book_json(row) for row in rows], "count": len(rows), "limit": limit, "next_cursor": next_cursor}
    if next_cursor:
        payload["next"] = f"{request.path}?limit={limit}&after={next_cursor}"
        if query:
            payload["next"] += f"&q={query}"
    return jsonify(payload)


@api_bp.get("/books/<int:book_id>")
def book(book_id):
    row = get_db().execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        return jsonify({"error": "Book not found"}), 404
    return jsonify(book_json(row))


@api_bp.errorhandler(404)
def api_not_found(_error):
    return jsonify({"error": "API resource not found"}), 404
