import sqlite3
from datetime import datetime

from flask import Flask, render_template, request, session, redirect, url_for, flash


app = Flask(__name__)

app.secret_key = "pageturner-secret-key"


def get_db_connection():

    conn = sqlite3.connect("database.db")

    conn.row_factory = sqlite3.Row

    return conn


@app.context_processor
def cart_count():

    cart = session.get("cart", {})

    count = 0

    for quantity in cart.values():
        count = count + quantity

    return {
        "cart_count": count
    }


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

    return render_template(
        "book.html",
        book=book
    )


@app.route("/cart/add/<int:id>", methods=["POST"])
def add_to_cart(id):

    cart = session.get("cart", {})

    book_id = str(id)

    if book_id in cart:

        cart[book_id] = cart[book_id] + 1

    else:

        cart[book_id] = 1

    session["cart"] = cart

    session.modified = True

    flash("Added to cart!")

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

        if book:

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


@app.route("/cart/update/<int:id>", methods=["POST"])
def update_cart(id):

    cart = session.get("cart", {})

    quantity = int(request.form["quantity"])

    book_id = str(id)

    if quantity > 0:

        cart[book_id] = quantity

    else:

        cart.pop(book_id, None)

    session["cart"] = cart

    session.modified = True

    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:id>", methods=["POST"])
def remove_from_cart(id):

    cart = session.get("cart", {})

    book_id = str(id)

    cart.pop(book_id, None)

    session["cart"] = cart

    session.modified = True

    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():

    if request.method == "POST":

        name = request.form["name"]

        phone = request.form["phone"]

        address = request.form["address"]

        cart = session.get("cart", {})

        if not cart:

            return redirect(url_for("cart"))

        conn = get_db_connection()

        total = 0

        for book_id, quantity in cart.items():

            book = conn.execute(
                "SELECT * FROM books WHERE id = ?",
                (int(book_id),)
            ).fetchone()

            if book:

                total = total + (book["price"] * quantity)

        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            """
            INSERT INTO orders
            (name, phone, address, total, date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, phone, address, total, date)
        )

        order_id = cursor.lastrowid

        for book_id, quantity in cart.items():

            book = conn.execute(
                "SELECT * FROM books WHERE id = ?",
                (int(book_id),)
            ).fetchone()

            if book:

                conn.execute(
                    """
                    INSERT INTO order_items
                    (order_id, book_id, quantity, price)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        int(book_id),
                        quantity,
                        book["price"]
                    )
                )

        conn.commit()

        conn.close()

        session["cart"] = {}

        session.modified = True

        return redirect(
            url_for("order_confirmation", id=order_id)
        )

    return render_template("checkout.html")


@app.route("/order/<int:id>")
def order_confirmation(id):

    conn = get_db_connection()

    order = conn.execute(
        "SELECT * FROM orders WHERE id = ?",
        (id,)
    ).fetchone()

    items = conn.execute(
        """
        SELECT books.title,
               order_items.quantity,
               order_items.price
        FROM order_items
        JOIN books
        ON order_items.book_id = books.id
        WHERE order_items.order_id = ?
        """,
        (id,)
    ).fetchall()

    conn.close()

    return render_template(
        "order.html",
        order=order,
        items=items
    )


if __name__ == "__main__":

    app.run(debug=True)