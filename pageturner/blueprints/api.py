"""JSON API with scoped API keys and SQLite-backed rate limiting.

Anonymous callers can read the public catalogue and share one 30/minute bucket
per IP (requirement 25).  A caller presenting an API key (requirement 72) gets a
bucket of its own and, for the endpoints that expose account or stock data, must
hold the matching scope.  Keys are looked up and verified in ``apikeys``.
"""
import base64
import json

from flask import Blueprint, abort, g, jsonify, request

from .. import apikeys
from ..db import get_db

api_bp = Blueprint("api", __name__, url_prefix="/api")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def presented_token():
    """The API token on this request, from either accepted header."""
    header = request.headers.get("Authorization", "")

    if header.lower().startswith("bearer "):
        return header[7:].strip()

    return request.headers.get("X-API-Key", "").strip()


@api_bp.before_request
def authenticate_and_throttle():
    """Resolve the API key, then count the request against its bucket."""
    connection = get_db()
    token = presented_token()

    g.api_key = apikeys.lookup(connection, token) if token else None

    # A token was presented but is not valid.  Answering 401 rather than
    # treating the caller as anonymous avoids silently applying the small
    # anonymous allowance to someone who believes they are authenticated.
    if token and g.api_key is None:
        return jsonify({"error": "Invalid or revoked API key"}), 401

    if not apikeys.reserve_quota(
        connection, request.remote_addr or "unknown", g.api_key
    ):
        return (
            jsonify({"error": "Rate limit exceeded", "limit_per_minute": (
                apikeys.KEY_QUOTA if g.api_key else apikeys.ANONYMOUS_QUOTA
            )}),
            429,
        )

    if g.api_key is not None:
        apikeys.touch(connection, g.api_key)


def require_scope(scope):
    """Abort with 403 unless the presented key carries ``scope``.

    Returning the response instead of raising keeps an explicit ``return`` at
    each call site, so a forgotten check cannot silently fall through.
    """
    granted = apikeys.scopes_of(getattr(g, "api_key", None))

    if scope not in granted:
        return jsonify({
            "error": "This endpoint requires the '%s' scope" % scope,
            "granted_scopes": sorted(granted),
        }), 403

    return None


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


def can_read_inventory():
    return "inventory:read" in apikeys.scopes_of(getattr(g, "api_key", None))


def book_json(book, include_stock=True):
    """Serialise a book row.

    ``stock`` is exact inventory information, so it is only exposed to a key
    holding ``inventory:read``.  Anonymous readers still get a coarse
    ``in_stock`` flag, which is what a storefront needs to show a buy button.
    """
    if not book:
        return None

    payload = {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "category": book["category"],
        "price_paise": book["price_paise"],
        "price": f"{book['price_paise'] // 100}.{book['price_paise'] % 100:02d}",
        "in_stock": book["stock"] > 0,
        "image": book["image"],
    }

    if include_stock:
        payload["stock"] = book["stock"]

    return payload


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

    include_stock = can_read_inventory()
    payload = {"books": [book_json(row, include_stock) for row in rows], "count": len(rows), "limit": limit, "next_cursor": next_cursor}
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
    return jsonify(book_json(row, can_read_inventory()))


@api_bp.get("/me")
def me():
    """Describe the key making the request.

    This is how a client checks its scopes before using them; it requires a key
    because there is nothing to describe without one.

    The owner's details are personal data, so they are only returned to a key
    that actually holds ``account:read``.  Any other key still gets its own
    metadata, which is what it needs to decide whether it is configured right.
    """
    if g.api_key is None:
        return jsonify({
            "error": "An API key is required",
            "hint": "Send Authorization: Bearer <token> or X-API-Key: <token>",
        }), 401

    owner = None
    if "account:read" in apikeys.scopes_of(g.api_key):
        owner = get_db().execute(
            "SELECT id, name, email, role FROM users WHERE id = ?",
            (g.api_key["user_id"],),
        ).fetchone()

    return jsonify({
        "key": {
            "id": g.api_key["id"],
            "name": g.api_key["name"],
            "prefix": g.api_key["prefix"],
            "scopes": sorted(apikeys.scopes_of(g.api_key)),
            "created_at": g.api_key["created_at"],
            "last_used_at": g.api_key["last_used_at"],
        },
        "account": (
            {
                "id": owner["id"],
                "name": owner["name"],
                "email": owner["email"],
                "role": owner["role"],
            }
            if owner
            else None
        ),
        "available_scopes": apikeys.SCOPES,
        "account_requires_scope": "account:read",
        "quota_per_minute": apikeys.KEY_QUOTA,
    })


@api_bp.get("/inventory")
def inventory():
    """Exact stock levels for the catalogue.  Requires ``inventory:read``."""
    denied = require_scope("inventory:read")
    if denied:
        return denied

    rows = get_db().execute(
        "SELECT * FROM books ORDER BY id"
    ).fetchall()

    return jsonify({
        "books": [book_json(row, include_stock=True) for row in rows],
        "count": len(rows),
    })


@api_bp.errorhandler(404)
def api_not_found(_error):
    return jsonify({"error": "API resource not found"}), 404
