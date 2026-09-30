import sqlite3

from flask import Flask, render_template, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "pageturner-secret-key"


def get_db_connection():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def home():
    category = request.args.get("category")

    conn = get_db_connection()

    if category and category != "All":
        books = conn.execute(
            "SELECT * FROM books WHERE category = ?",
            (category,)
        ).fetchall()
    else:
        books = conn.execute(
            "SELECT * FROM books"
        ).fetchall()

    conn.close()

    categories = [
        "All",
        "Biography",
        "Fiction",
        "History",
        "Science",
        "Self-Help",
        "Technology"
    ]

    return render_template(
        "index.html",
        books=books,
        categories=categories,
        selected_category=category
    )


@app.route("/book/<int:id>")
def book_detail(id):
    conn = get_db_connection()

    book = conn.execute(
        "SELECT * FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    conn.close()

    return render_template("book.html", book=book)


@app.route("/cart/add/<int:id>", methods=["POST"])
def add_to_cart(id):
    cart = session.get("cart", {})

    book_id = str(id)

    if book_id in cart:
        cart[book_id] = cart[book_id] + 1
    else:
        cart[book_id] = 1

    session["cart"] = cart

    return redirect(url_for("book_detail", id=id))


@app.route("/cart")
def cart():
    cart = session.get("cart", {})

    conn = get_db_connection()

    items = []
    total = 0

    for book_id, quantity in cart.items():
        book = conn.execute(
            "SELECT * FROM books WHERE id = ?",
            (int(book_id),)
        ).fetchone()

        subtotal = book["price"] * quantity
        total = total + subtotal

        items.append({
            "book": book,
            "quantity": quantity,
            "subtotal": subtotal
        })

    conn.close()

    return render_template(
        "cart.html",
        items=items,
        total=total
    )


if __name__ == "__main__":
    app.run(debug=True)