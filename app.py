import sqlite3
import os
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    session,
    redirect,
    url_for,
    flash,
    jsonify
)

from werkzeug.security import generate_password_hash, check_password_hash


# Create the Flask application.
app = Flask(__name__)

app.secret_key = "pageturner-secret-key"


# Get the admin password from the environment variable.
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


# Create indexes that improve commonly used searches.
def create_indexes():

    conn = get_db_connection()

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_books_category
        ON books(category)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_user_id
        ON orders(user_id)
        """
    )

    conn.commit()
    conn.close()


# Create the indexes when the application starts.
create_indexes()


# Handle admin login.
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        password = request.form.get("password", "").strip()

        if not password:

            flash("Password is required.")

            return redirect(url_for("admin_login"))

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
        SELECT books.title,
               SUM(order_items.quantity) AS total_sold
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
        SELECT books.category,
               COUNT(DISTINCT order_items.order_id) AS order_count
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


# Add a new book from the admin panel.
@app.route("/admin/add", methods=["POST"])
def admin_add():

    check = admin_required()

    if check:
        return check

    title = request.form["title"]
    author = request.form["author"]
    category = request.form["category"]
    price_text = request.form["price"]
    description = request.form["description"]
    stock_text = request.form["stock"]
    color = request.form["color"]
    image = request.form["image"]

    if not title or not author or not category or not price_text or not stock_text:
        flash("Title, author, category, price and stock are required.")
        return redirect(url_for("admin"))

    try:
        price = float(price_text)
    except ValueError:
        flash("Price must be a number.")
        return redirect(url_for("admin"))

    try:
        stock = int(stock_text)
    except ValueError:
        flash("Stock must be a whole number.")
        return redirect(url_for("admin"))

    if price < 0:
        flash("Price cannot be negative.")
        return redirect(url_for("admin"))

    if stock < 0:
        flash("Stock cannot be negative.")
        return redirect(url_for("admin"))

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


# Edit an existing book.
@app.route("/admin/edit/<int:id>", methods=["POST"])
def admin_edit(id):

    check = admin_required()

    if check:
        return check

    title = request.form.get("title", "").strip()
    author = request.form.get("author", "").strip()
    price_text = request.form.get("price", "").strip()
    stock_text = request.form.get("stock", "").strip()

    if not title or not author or not price_text or not stock_text:

        flash("Title, author, price and stock are required.")

        return redirect(url_for("manage_books"))

    try:

        price = float(price_text)

    except ValueError:

        flash("Price must be a number.")

        return redirect(url_for("manage_books"))

    try:

        stock = int(stock_text)

    except ValueError:

        flash("Stock must be a whole number.")

        return redirect(url_for("manage_books"))

    if price < 0:

        flash("Price cannot be negative.")

        return redirect(url_for("manage_books"))

    if stock < 0:

        flash("Stock cannot be negative.")

        return redirect(url_for("manage_books"))

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

    return redirect(url_for("manage_books"))


# Delete an existing book.
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

    return redirect(url_for("manage_books"))


# Log the admin out.
@app.route("/admin/logout")
def admin_logout():

    session.pop("admin", None)

    return redirect(url_for("admin_login"))


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


# Update an order status.
@app.route("/admin/order-status/<int:id>", methods=["POST"])
def update_order_status(id):

    check = admin_required()

    if check:
        return check

    status = request.form.get("status", "").strip()

    allowed_statuses = [
        "Placed",
        "Packed",
        "Delivered"
    ]

    if status not in allowed_statuses:

        flash("Invalid order status.")

        return redirect(url_for("orders"))

    conn = get_db_connection()

    conn.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ?
        """,
        (status, id)
    )

    conn.commit()
    conn.close()

    flash("Order status updated.")

    return redirect(url_for("orders"))


# Register a new customer.
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:

            flash("Name, email and password are required.")

            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        conn = get_db_connection()

        try:

            conn.execute(
                """
                INSERT INTO users
                (name, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (name, email, password_hash)
            )

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            flash("Email already registered.")

            return redirect(url_for("register"))

        conn.close()

        flash("Registration successful. Please login.")

        return redirect(url_for("login"))

    return render_template("register.html")


# Login a customer.
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:

            flash("Email and password are required.")

            return redirect(url_for("login"))

        conn = get_db_connection()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ):

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]

            flash("Login successful.")

            return redirect(url_for("home"))

        flash("Invalid email or password.")

    return render_template("login.html")


# Log the customer out.
@app.route("/logout")
def logout():

    session.pop("user_id", None)
    session.pop("user_name", None)

    flash("You have been logged out.")

    return redirect(url_for("home"))


# Calculate the cart count for the navbar.
@app.context_processor
def cart_count():

    cart = session.get("cart", {})

    count = 0

    for quantity in cart.values():

        count = count + quantity

    return {
        "cart_count": count
    }


# Display books with search, category filtering, sorting and pagination.
@app.route("/")
def home():

    query = request.args.get("q", "").strip()

    category = request.args.get("category")

    sort = request.args.get("sort", "title")

    try:

        page = int(request.args.get("page", 1))

    except ValueError:

        page = 1

    if page < 1:

        page = 1

    per_page = 6

    offset = (page - 1) * per_page

    conn = get_db_connection()

    if sort == "price":

        order = "price"

    else:

        order = "title"

    search_text = "%" + query + "%"

    if category and category != "All":

        books = conn.execute(
            f"""
            SELECT *
            FROM books
            WHERE category = ?
            AND (title LIKE ? OR author LIKE ?)
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (
                category,
                search_text,
                search_text,
                per_page,
                offset
            )
        ).fetchall()

        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE category = ?
            AND (title LIKE ? OR author LIKE ?)
            """,
            (
                category,
                search_text,
                search_text
            )
        ).fetchone()[0]

    else:

        books = conn.execute(
            f"""
            SELECT *
            FROM books
            WHERE title LIKE ? OR author LIKE ?
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (
                search_text,
                search_text,
                per_page,
                offset
            )
        ).fetchall()

        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE title LIKE ? OR author LIKE ?
            """,
            (
                search_text,
                search_text
            )
        ).fetchone()[0]

    wishlist_ids = []

    if session.get("user_id"):

        wishlist_rows = conn.execute(
            """
            SELECT book_id
            FROM wishlist
            WHERE user_id = ?
            """,
            (session.get("user_id"),)
        ).fetchall()

        for row in wishlist_rows:

            wishlist_ids.append(row["book_id"])

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
        total_pages=total_pages,
        wishlist_ids=wishlist_ids,
        query=query
    )


# Display one book and its reviews.
@app.route("/book/<int:id>")
def book_detail(id):

    conn = get_db_connection()

    book = conn.execute(
        "SELECT * FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    if not book:

        conn.close()

        return render_template("404.html"), 404

    reviews = conn.execute(
        """
        SELECT *
        FROM reviews
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


# Add a review for a book.
@app.route("/book/<int:id>/review", methods=["POST"])
def add_review(id):

    name = request.form.get("name", "").strip()
    rating_text = request.form.get("rating", "").strip()
    comment = request.form.get("comment", "").strip()

    if not name or not rating_text or not comment:

        flash("Name, rating and comment are required.")

        return redirect(url_for("book_detail", id=id))

    try:

        rating = int(rating_text)

    except ValueError:

        flash("Rating must be a number from 1 to 5.")

        return redirect(url_for("book_detail", id=id))

    if rating < 1 or rating > 5:

        flash("Rating must be between 1 and 5.")

        return redirect(url_for("book_detail", id=id))

    conn = get_db_connection()

    book = conn.execute(
        "SELECT id FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    if not book:

        conn.close()

        return render_template("404.html"), 404

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


# Add a selected book to the cart.
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

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":

            return {
                "status": "error",
                "message": "Book not found!"
            }, 404

        flash("Book not found!")

        return redirect(url_for("home"))

    if book["stock"] == 0:

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":

            return {
                "status": "error",
                "message": "Out of stock!"
            }, 400

        flash("Out of stock!")

        return redirect(url_for("book_detail", id=id))

    current_quantity = cart.get(book_id, 0)

    if current_quantity >= book["stock"]:

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":

            return {
                "status": "error",
                "message": "No more copies available!",
                "cart_count": sum(cart.values())
            }, 400

        flash("No more copies available!")

    else:

        cart[book_id] = current_quantity + 1

        session["cart"] = cart

        session.modified = True

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":

            return {
                "status": "added",
                "message": "Added to cart!",
                "cart_count": sum(cart.values())
            }

        flash("Added to cart!")

    return redirect(url_for("book_detail", id=id))


# Display the cart.
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

    discount = 0

    coupon_code = session.get("coupon_code")

    if coupon_code:

        coupon = conn.execute(
            """
            SELECT *
            FROM coupons
            WHERE code = ?
            """,
            (coupon_code,)
        ).fetchone()

        today = datetime.now().strftime("%Y-%m-%d")

        if coupon and coupon["active"] and coupon["expiry_date"] >= today:

            discount = total * coupon["percent"] / 100

        else:

            session.pop("coupon_code", None)

            coupon_code = None

    final_total = total - discount

    conn.close()

    return render_template(
        "cart.html",
        items=items,
        total=total,
        discount=discount,
        final_total=final_total,
        coupon_code=coupon_code
    )


# Update a cart quantity.
@app.route("/cart/update/<int:id>", methods=["POST"])
def update_cart(id):

    cart = session.get("cart", {})

    quantity_text = request.form.get("quantity", "").strip()

    if not quantity_text:

        flash("Quantity is required.")

        return redirect(url_for("cart"))

    try:

        quantity = int(quantity_text)

    except ValueError:

        flash("Quantity must be a number.")

        return redirect(url_for("cart"))

    if quantity < 0:

        flash("Quantity cannot be negative.")

        return redirect(url_for("cart"))

    if quantity == 0:

        cart.pop(str(id), None)

    else:

        conn = get_db_connection()

        book = conn.execute(
            "SELECT stock FROM books WHERE id = ?",
            (id,)
        ).fetchone()

        conn.close()

        if not book:

            flash("Book not found.")

            return redirect(url_for("cart"))

        if quantity > book["stock"]:

            flash(
                f"Only {book['stock']} copies are available."
            )

            return redirect(url_for("cart"))

        cart[str(id)] = quantity

    session["cart"] = cart

    session.modified = True

    return redirect(url_for("cart"))


# Remove a book from the cart.
@app.route("/cart/remove/<int:id>", methods=["POST"])
def remove_from_cart(id):

    cart = session.get("cart", {})

    book_id = str(id)

    cart.pop(book_id, None)

    session["cart"] = cart

    session.modified = True

    return redirect(url_for("cart"))


# Apply a coupon.
@app.route("/apply-coupon", methods=["POST"])
def apply_coupon():

    code = request.form.get("code", "").strip().upper()

    if not code:

        flash("Please enter a coupon code.")

        return redirect(url_for("cart"))

    conn = get_db_connection()

    coupon = conn.execute(
        """
        SELECT *
        FROM coupons
        WHERE code = ?
        """,
        (code,)
    ).fetchone()

    conn.close()

    if not coupon:

        flash("Invalid coupon code.")

        return redirect(url_for("cart"))

    today = datetime.now().strftime("%Y-%m-%d")

    if not coupon["active"]:

        flash("This coupon is no longer active.")

        return redirect(url_for("cart"))

    if coupon["expiry_date"] < today:

        flash("This coupon has expired.")

        return redirect(url_for("cart"))

    session["coupon_code"] = code

    flash(
        f"Coupon applied. You saved {coupon['percent']}%."
    )

    return redirect(url_for("cart"))


# Remove the current coupon.
@app.route("/remove-coupon", methods=["POST"])
def remove_coupon():

    session.pop("coupon_code", None)

    flash("Coupon removed.")

    return redirect(url_for("cart"))


# Find previous orders using the customer's phone number.
@app.route("/order-history", methods=["GET", "POST"])
def order_history():

    orders = []

    searched = False

    if request.method == "POST":

        phone = request.form.get("phone", "").strip()

        if not phone:

            flash("Phone number is required.")

            return redirect(url_for("order_history"))

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


# Process checkout.
@app.route("/checkout", methods=["GET", "POST"])
def checkout():

    if not session.get("user_id"):

        flash("Please login before checkout.")

        return redirect(url_for("login"))

    if request.method == "GET":

        return render_template("checkout.html")

    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()

    if not name or not phone or not address:

        flash("Name, phone and address are required.")

        return redirect(url_for("checkout"))

    if not phone.isdigit() or len(phone) != 10:

        flash("Phone number must be exactly 10 digits.")

        return redirect(url_for("checkout"))

    if phone[0] not in ["6", "7", "8", "9"]:

        flash("Phone number must start with 6, 7, 8 or 9.")

        return redirect(url_for("checkout"))

    cart = session.get("cart", {})

    if not cart:

        flash("Your cart is empty.")

        return redirect(url_for("cart"))

    user_id = session.get("user_id")

    conn = get_db_connection()

    try:

        conn.execute("BEGIN IMMEDIATE")

        total = 0

        discount = 0

        books = []

        for book_id, quantity in cart.items():

            if not isinstance(quantity, int) or quantity <= 0:

                raise ValueError("Invalid cart quantity.")

            book = conn.execute(
                """
                SELECT *
                FROM books
                WHERE id = ?
                """,
                (int(book_id),)
            ).fetchone()

            if not book:

                raise ValueError("Book not found.")

            if quantity > book["stock"]:

                raise ValueError(
                    f"Only {book['stock']} copies of "
                    f"{book['title']} are available."
                )

            total = total + (book["price"] * quantity)

            books.append(
                (book, int(book_id), quantity)
            )

        coupon_code = session.get("coupon_code")

        if coupon_code:

            coupon = conn.execute(
                """
                SELECT *
                FROM coupons
                WHERE code = ?
                """,
                (coupon_code,)
            ).fetchone()

            today = datetime.now().strftime("%Y-%m-%d")

            if coupon and coupon["active"] and coupon["expiry_date"] >= today:

                discount = total * coupon["percent"] / 100

            else:

                session.pop("coupon_code", None)

                coupon_code = None

        final_total = total - discount

        date = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cursor = conn.execute(
            """
            INSERT INTO orders
            (user_id, name, phone, address, total, date, discount)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name,
                phone,
                address,
                final_total,
                date,
                discount
            )
        )

        order_id = cursor.lastrowid

        for book, book_id, quantity in books:

            cursor = conn.execute(
                """
                UPDATE books
                SET stock = stock - ?
                WHERE id = ? AND stock >= ?
                """,
                (
                    quantity,
                    book_id,
                    quantity
                )
            )

            if cursor.rowcount == 0:

                raise ValueError(
                    "Sorry, just sold out."
                )

            conn.execute(
                """
                INSERT INTO order_items
                (order_id, book_id, quantity, price)
                VALUES (?, ?, ?, ?)
                """,
                (
                    order_id,
                    book_id,
                    quantity,
                    book["price"]
                )
            )

        conn.commit()

    except Exception as error:

        conn.rollback()

        flash(str(error))

        return redirect(url_for("cart"))

    finally:

        conn.close()

    session["cart"] = {}

    session.pop("coupon_code", None)

    session.modified = True

    return redirect(
        url_for("order_confirmation", id=order_id)
    )


# Display the completed order.
@app.route("/order/<int:id>")
def order_confirmation(id):

    conn = get_db_connection()

    order = conn.execute(
        "SELECT * FROM orders WHERE id = ?",
        (id,)
    ).fetchone()

    if not order:

        conn.close()

        return render_template("404.html"), 404

    items = conn.execute(
        """
        SELECT
            books.title,
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


# Add or remove a book from the wishlist.
@app.route("/wishlist/add/<int:id>", methods=["POST"])
def add_to_wishlist(id):

    if not session.get("user_id"):

        return {
            "status": "login_required"
        }, 401

    user_id = session.get("user_id")

    conn = get_db_connection()

    book = conn.execute(
        "SELECT id FROM books WHERE id = ?",
        (id,)
    ).fetchone()

    if not book:

        conn.close()

        return {
            "status": "error",
            "message": "Book not found."
        }, 404

    existing = conn.execute(
        """
        SELECT *
        FROM wishlist
        WHERE user_id = ? AND book_id = ?
        """,
        (user_id, id)
    ).fetchone()

    if existing:

        conn.execute(
            """
            DELETE FROM wishlist
            WHERE user_id = ? AND book_id = ?
            """,
            (user_id, id)
        )

        conn.commit()
        conn.close()

        return {
            "status": "removed"
        }

    conn.execute(
        """
        INSERT INTO wishlist
        (user_id, book_id)
        VALUES (?, ?)
        """,
        (user_id, id)
    )

    conn.commit()
    conn.close()

    return {
        "status": "added"
    }


# Display the user's wishlist.
@app.route("/wishlist")
def wishlist():

    if not session.get("user_id"):

        flash("Please login to view your wishlist.")

        return redirect(url_for("login"))

    user_id = session.get("user_id")

    conn = get_db_connection()

    books = conn.execute(
        """
        SELECT books.*
        FROM wishlist
        JOIN books
            ON wishlist.book_id = books.id
        WHERE wishlist.user_id = ?
        ORDER BY wishlist.id DESC
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    return render_template(
        "wishlist.html",
        books=books
    )


# Remove a book from the wishlist.
@app.route("/wishlist/remove/<int:id>", methods=["POST"])
def remove_from_wishlist(id):

    if not session.get("user_id"):

        flash("Please login to manage your wishlist.")

        return redirect(url_for("login"))

    user_id = session.get("user_id")

    conn = get_db_connection()

    conn.execute(
        """
        DELETE FROM wishlist
        WHERE user_id = ? AND book_id = ?
        """,
        (user_id, id)
    )

    conn.commit()
    conn.close()

    flash("Book removed from your wishlist.")

    return redirect(url_for("wishlist"))


# Return books as JSON for JavaScript fetch().
@app.route("/api/books")
def api_books():

    query = request.args.get("q", "").strip()

    category = request.args.get("category")

    sort = request.args.get("sort", "title")

    conn = get_db_connection()

    if sort == "price":

        order = "price"

    else:

        order = "title"

    search_text = "%" + query + "%"

    if category and category != "All":

        books = conn.execute(
            f"""
            SELECT *
            FROM books
            WHERE category = ?
            AND (title LIKE ? OR author LIKE ?)
            ORDER BY {order}
            """,
            (
                category,
                search_text,
                search_text
            )
        ).fetchall()

    else:

        books = conn.execute(
            f"""
            SELECT *
            FROM books
            WHERE title LIKE ? OR author LIKE ?
            ORDER BY {order}
            """,
            (
                search_text,
                search_text
            )
        ).fetchall()

    conn.close()

    book_list = []

    for book in books:

        book_list.append({
            "id": book["id"],
            "title": book["title"],
            "author": book["author"],
            "category": book["category"],
            "price": book["price"],
            "stock": book["stock"],
            "image": book["image"]
        })

    return jsonify(book_list)


# Display a custom 404 page.
@app.errorhandler(404)
def page_not_found(error):

    return render_template("404.html"), 404


# Display a custom 500 page.
@app.errorhandler(500)
def server_error(error):

    return render_template("500.html"), 500


# Start the Flask development server.
if __name__ == "__main__":

    app.run(debug=True)