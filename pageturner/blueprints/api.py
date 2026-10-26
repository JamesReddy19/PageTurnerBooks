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


def book_json(book):
    if not book:
        return None
    return {"id": book["id"], "title": book["title"], "author": book["author"], "category": book["category"], "price_paise": book["price_paise"], "price": f"{book['price_paise'] // 100}.{book['price_paise'] % 100:02d}", "stock": book["stock"], "image": book["image"]}


@api_bp.get("/books")
def books():
    query = request.args.get("q", "").strip()[:100]
    connection = get_db()
    if query:
        like = f"%{query}%"
        rows = connection.execute("SELECT b.*,(CASE WHEN b.title LIKE ? THEN 3 WHEN b.author LIKE ? THEN 2 ELSE 1 END) AS relevance FROM books b WHERE b.title LIKE ? OR b.author LIKE ? OR EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id=b.id AND t.name LIKE ?) ORDER BY relevance DESC,b.title COLLATE NOCASE LIMIT 100", (like,like,like,like,like)).fetchall()
    else:
        rows = connection.execute("SELECT * FROM books ORDER BY title COLLATE NOCASE LIMIT 100").fetchall()
    return jsonify({"books": [book_json(row) for row in rows], "count": len(rows)})


@api_bp.get("/books/<int:book_id>")
def book(book_id):
    row = get_db().execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        return jsonify({"error": "Book not found"}), 404
    return jsonify(book_json(row))


@api_bp.errorhandler(404)
def api_not_found(_error):
    return jsonify({"error": "API resource not found"}), 404
