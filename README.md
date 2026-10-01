# PageTurner Books

PageTurner Books is a simple online bookstore project.

Users can view books, search for books, filter books by category, add books to a cart, update the quantity, remove books, and place an order.

The project saves order details in an SQLite database.

 Technologies Used

Python : Used for the main application logic.

Flask : Used to create the web application and routes.

SQLite : Used to store books, orders and order items.

HTML : Used to create the web pages.

CSS : Used to design the bookstore pages.

JavaScript : Used for book search and phone number validation.


Features

- View books on the home page
- Search books by title or author
- Filter books by category
- View book details
- View book images
- Add books to the cart
- Update book quantity
- Remove books from the cart
- View cart total
- Checkout using name, phone number and address
- Check phone number using JavaScript
- Save orders in the database
- Show order confirmation

How to Run

1. Open the PageTurnerBooks folder in VS Code.
2. Open the terminal.
3. Run the following command:

python app.py

Open the local address shown in the terminal in a web browser.

What the Website Can Do

Vhow books with images and details
Search by book title or author
Filter books by category
Add and remove books from cart
Change book quantity
Show cart total
Take customer details at checkout
Check the phone number
Save orders in the database
Show the order confirmationn



Main Pages
Shop – View, search and filter books
Book Details – View information about a book
Cart – Manage selected books
Checkout – Enter customer details
Order Confirmation – View the completed order

Database

We use three tables:

books – Book information
orders – Customer order information
order_items – Books inside an order.

SQL queries :

The queries.sql file contains 5 queries for:

-Books below 500
-Technology books by price
-Books count by category
-Most expensive book
-Orders with customer name and total

Routes

 `/` – Shows books, search and category filter.
 `/book/<id>` – Shows book details.
 `/cart/add/<id>` – Adds a book to cart.
 `/cart` – Shows cart and total.
 `/cart/update/<id>` – Updates quantity.
 `/cart/remove/<id>` – Removes a book.
 `/checkout` – Takes customer details and saves the order.
 `/order/<id>` – Shows order confirmation.

SQL Used

- Home – Reads books from `books`.
- Book Details – Reads one book from `books`.
- Cart – Reads selected books from `books`.
- Checkout – Reads books and adds data to `orders` and `order_items`.
- Order Confirmation – Reads order data and uses JOIN with `books`.

Final Project Structurre looks like :

PageTurnerBooks/
├── app.py
├── database.db
├── schema.sql
├── seed.sql
├── queries.sql
├── README.md
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── book.html
│   ├── cart.html
│   ├── checkout.html
│   └── order.html
└── static/
    ├── style.css
    ├── script.js
    └── images/