import sqlite3
import os
from datetime import datetime

from flask import Flask, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash


# Create the Flask application.
app = Flask(__name__)

app.secret_key = "pageturner-secret-key"

admin_password = os.environ.get("ADMIN_PASSWORD")

if not admin_password:
    raise ValueError("ADMIN_PASSWORD is not set")

admin_password_hash = generate_password_hash(admin_password)


# Check whether the admin is logged in.
def admin_required():

    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    return None


# Create a reusable function to connect to the SQLite database.
def get_db_connection():

    conn = sqlite3.connect("database.db")

    conn.row_factory = sqlite3.Row

    return conn


# Handle admin login and verify the entered password.
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        password = request.form["password"]

        if check_password_hash(admin_password_hash, password):

            session["admin"] = True

            return redirect(url_for("admin"))

        flash("Wrong password!")

    return render_template("admin_login.html")


# Display the admin dashboard.
@app.route("/admin")
def admin():

    check = admin_required()

    if check:
        return check

    conn = get_db_connection()

    total_orders = conn.execute(
        "SELECT COUNT(*) AS total FROM orders"
    ).fetchone()["total"]

    total_revenue = conn.execute(
        "SELECT SUM(total) AS revenue FROM orders"
    ).fetchone()["revenue"]

    total_inventory = conn.execute(
        "SELECT SUM(stock) AS inventory FROM books"
    ).fetchone()["inventory"]

    books_sold = conn.execute(
        "SELECT SUM(quantity) AS sold FROM order_items"
    ).fetchone()["sold"]

    best_selling = conn.execute(
        """
        SELECT books.title, SUM(order_items.quantity) AS total_sold
        FROM order_items
        JOIN books
            ON order_items.book_id = books.id
        GROUP BY books.id
        ORDER BY total_sold DESC
        LIMIT 5
        """
    ).fetchall()

    category_orders = conn.execute(
        """
        SELECT books.category, COUNT(DISTINCT order_items.order_id) AS order_count
        FROM order_items
        JOIN books
            ON order_items.book_id = books.id
        GROUP BY books.category
        ORDER BY order_count DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        total_orders=total_orders,
        total_revenue=total_revenue or 0,
        total_inventory=total_inventory or 0,
        books_sold=books_sold or 0,
        best_selling=best_selling,
        category_orders=category_orders
    )


# Add a new book to the database from the admin panel.
@app.route("/admin/add", methods=["POST"])
def admin_add():

    check = admin_required()

    if check:
        return check

    title = request.form["title"]
    author = request.form["author"]
    category = request.form["category"]
    price = request.form["price"]
    description = request.form["description"]
    stock = request.form["stock"]
    color = request.form["color"]
    image = request.form["image"]

    conn = get_db_connection()

    conn.execute(
        """
        INSERT INTO books
        (title, author, category, price, description, stock, color, image)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            author,
            category,
            price,
            description,
            stock,
            color,
            image
        )
    )

    conn.commit()
    conn.close()

    flash("Book added!")

    return redirect(url_for("admin"))


# Display all books in the admin book-management page.
@app.route("/admin/books")
def manage_books():

    check = admin_required()

    if check:
        return check

    conn = get_db_connection()

    books = conn.execute(
        "SELECT * FROM books ORDER BY title"
    ).fetchall()

    conn.close()

    return render_template(
        "manage_books.html",
        books=books
    )


# Edit the title, author, price, and stock of an existing book.
@app.route("/admin/edit/<int:id>", methods=["POST"])
def admin_edit(id):

    check = admin_required()

    if check:
        return check

    title = request.form["title"]
    author = request.form["author"]
    price = request.form["price"]
    stock = request.form["stock"]

    conn = get_db_connection()

    conn.execute(
        """
        UPDATE books
        SET title = ?, author = ?, price = ?, stock = ?
        WHERE id = ?
        """,
        (title, author, price, stock, id)
    )

    conn.commit()
    conn.close()

    flash("Book updated!")

    return redirect(url_for("admin"))


# Delete an existing book from the database.
@app.route("/admin/delete/<int:id>", methods=["POST"])
def admin_delete(id):

    check = admin_required()

    if check:
        return check

    conn = get_db_connection()

    conn.execute(
        "DELETE FROM books WHERE id = ?",
        (id,)
    )

    conn.commit()
    conn.close()

    flash("Book deleted!")

    return redirect(url_for("admin"))


# Log the admin out by removing the admin session.
@app.route("/admin/logout")
def admin_logout():

    session.pop("admin", None)

    return redirect(url_for("admin_login"))


# Calculate the total number of books in the cart for the navbar.
@app.context_processor
def cart_count():

    cart = session.get("cart", {})

    count = 0

    for quantity in cart.values():
        count = count + quantity

    return {
        "cart_count": count
    }


# Display books with category filtering, sorting, and pagination.
@app.route("/")
def home():

    category = request.args.get("category")
    sort = request.args.get("sort", "title")
    page = int(request.args.get("page", 1))

    per_page = 6
    offset = (page - 1) * per_page

    conn = get_db_connection()

    if sort == "price":
        order = "price"
    else:
        order = "title"

    if category and category != "All":

        books = conn.execute(
            f"""
            SELECT * FROM books
            WHERE category = ?
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (category, per_page, offset)
        ).fetchall()

        count = conn.execute(
            "SELECT COUNT(*) FROM books WHERE category = ?",
            (category,)
        ).fetchone()[0]

    else:

        books = conn.execute(
            f"""
            SELECT * FROM books
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (per_page, offset)
        ).fetchall()

        count = conn.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]

    conn.close()

    total_pages = (count + per_page - 1) // per_page

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
        selected_category=category,
        sort=sort,
        page=page,
        total_pages=total_pages
    )


# Display the selected book along with its reviews and average rating.
@app.route("/book/<int:id>")
def book_detail(id):

    conn = get_db_connection()

    book = conn.execute(
        "SELECT * FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    reviews = conn.execute(
        """
        SELECT * FROM reviews
        WHERE book_id = ?
        ORDER BY id DESC
        """,
        (id,)
    ).fetchall()

    average = conn.execute(
        """
        SELECT AVG(rating)
        FROM reviews
        WHERE book_id = ?
        """,
        (id,)
    ).fetchone()[0]

    conn.close()

    return render_template(
        "book.html",
        book=book,
        reviews=reviews,
        average=average
    )


# Save a customer's review and rating for a book.
@app.route("/book/<int:id>/review", methods=["POST"])
def add_review(id):

    name = request.form["name"]
    rating = int(request.form["rating"])
    comment = request.form["comment"]

    conn = get_db_connection()

    conn.execute(
        """
        INSERT INTO reviews
        (book_id, name, rating, comment)
        VALUES (?, ?, ?, ?)
        """,
        (id, name, rating, comment)
    )

    conn.commit()
    conn.close()

    flash("Review added!")

    return redirect(url_for("book_detail", id=id))


# Add a selected book to the cart while checking its available stock.
@app.route("/cart/add/<int:id>", methods=["POST"])
def add_to_cart(id):

    cart = session.get("cart", {})

    book_id = str(id)

    conn = get_db_connection()

    book = conn.execute(
        "SELECT stock FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    conn.close()

    if not book:
        flash("Book not found!")
        return redirect(url_for("home"))

    if book["stock"] == 0:
        flash("Out of stock!")
        return redirect(url_for("book_detail", id=id))

    current_quantity = cart.get(book_id, 0)

    if current_quantity >= book["stock"]:
        flash("No more copies available!")
    else:
        cart[book_id] = current_quantity + 1

        session["cart"] = cart

        session.modified = True

        flash("Added to cart!")

    return redirect(url_for("book_detail", id=id))


# Display the cart items and calculate the total price.
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


# Update the quantity of a book already present in the cart.
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


# Remove a book completely from the cart.
@app.route("/cart/remove/<int:id>", methods=["POST"])
def remove_from_cart(id):

    cart = session.get("cart", {})

    book_id = str(id)

    cart.pop(book_id, None)

    session["cart"] = cart

    session.modified = True

    return redirect(url_for("cart"))


# Find previous orders using the customer's phone number.
@app.route("/order-history", methods=["GET", "POST"])
def order_history():

    orders = []
    searched = False

    if request.method == "POST":

        phone = request.form["phone"]

        conn = get_db_connection()

        orders = conn.execute(
            """
            SELECT
                orders.id,
                orders.date,
                orders.total,
                books.title,
                order_items.quantity
            FROM orders
            JOIN order_items
                ON orders.id = order_items.order_id
            JOIN books
                ON order_items.book_id = books.id
            WHERE orders.phone = ?
            GROUP BY
                orders.id,
                orders.date,
                orders.total,
                books.title,
                order_items.quantity
            ORDER BY orders.id DESC
            """,
            (phone,)
        ).fetchall()

        conn.close()

        searched = True

    return render_template(
        "order_history.html",
        orders=orders,
        searched=searched
    )


# Display all customer orders for the admin.
@app.route("/orders")
def orders():

    check = admin_required()

    if check:
        return check

    conn = get_db_connection()

    orders = conn.execute(
        "SELECT * FROM orders ORDER BY id DESC"
    ).fetchall()

    conn.close()

    return render_template(
        "orders.html",
        orders=orders
    )


# Process checkout, create the order, reduce stock, and empty the cart.
@app.route("/checkout", methods=["GET", "POST"])
def checkout():

    if request.method == "POST":

        name = request.form["name"]
        phone = request.form["phone"]
        address = request.form["address"]

        cart = session.get("cart", {})

        if not cart:
            flash("Your cart is empty!")
            return redirect(url_for("cart"))

        conn = get_db_connection()

        try:

            # Start one database transaction for the whole checkout.
            conn.execute("BEGIN IMMEDIATE")

            total = 0
            books = []

            # Get the current book information and calculate the total.
            for book_id, quantity in cart.items():

                book = conn.execute(
                    "SELECT * FROM books WHERE id = ?",
                    (int(book_id),)
                ).fetchone()

                if not book or quantity <= 0:

                    conn.rollback()

                    flash("Sorry, this order could not be placed.")

                    return redirect(url_for("cart"))

                total = total + book["price"] * quantity

                books.append(
                    (book, int(book_id), quantity)
                )

            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Create the order inside the same transaction.
            cursor = conn.execute(
                """
                INSERT INTO orders (name, phone, address, total, date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, phone, address, total, date)
            )

            order_id = cursor.lastrowid

            # Reduce stock and create the order items.
            for book, book_id, quantity in books:

                cursor = conn.execute(
                    """
                    UPDATE books
                    SET stock = stock - ?
                    WHERE id = ? AND stock >= ?
                    """,
                    (quantity, book_id, quantity)
                )

                # rowcount tells us how many database rows were changed.
                if cursor.rowcount == 0:

                    conn.rollback()

                    flash("Sorry, just sold out.")

                    return redirect(url_for("cart"))

                conn.execute(
                    """
                    INSERT INTO order_items
                    (order_id, book_id, quantity, price)
                    VALUES (?, ?, ?, ?)
                    """,
                    (order_id, book_id, quantity, book["price"])
                )

            # Save the complete checkout only after everything succeeds.
            conn.commit()

        except Exception:

            # Undo all database changes if any error occurs.
            conn.rollback()

            flash("Order could not be placed. Please try again.")

            return redirect(url_for("cart"))

        finally:

            # Always close the database connection.
            conn.close()

        session["cart"] = {}

        session.modified = True

        return redirect(url_for("order_confirmation", id=order_id))

    return render_template("checkout.html")


# Display the completed order and the books included in that order.
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


# Start the Flask development server when this file is run directly.
if __name__ == "__main__":

    app.run(debug=True)