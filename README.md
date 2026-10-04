# PageTurner Books

PageTurner Books is a simple online bookstore project.

Users can view books, search for books, filter books by category, sort books, add books to a cart, update the quantity, remove books and place an order.

Users can also add reviews and ratings and find their previous orders using their phone number.

An admin can add, edit and delete books and view simple sales reports.

The project stores the data in an SQLite database.

## Technologies Used

Python : Used for the main application logic.

Flask : Used to create the web application and routes.

SQLite : Used to store books, orders, order items and reviews.

HTML : Used to create the web pages.

CSS : Used to design the bookstore pages.

JavaScript : Used for book search and phone number validation.

## Features

- View books on the home page
- Search books by title or author
- Filter books by category
- Sort books by title or price
- View books using pagination
- View book details
- View book images
- Add books to the cart
- Update book quantity
- Remove books from the cart
- Check book stock
- Show Out of Stock when stock is zero
- Prevent adding more books than available stock
- View cart total
- Checkout using name, phone number and address
- Check phone number using JavaScript
- Save orders in the database
- Show order confirmation
- Add book reviews and ratings
- Show average book rating
- Find previous orders using phone number
- Admin login
- Add books from the admin panel
- Edit books from the admin panel
- Delete books from the admin panel
- View admin reports

## How to Run

1. Open the PageTurnerBooks folder in VS Code.
2. Open the terminal.
3. Run the following command:

python app.py

4. Open the local address shown in the terminal in a web browser.

## What the Website Can Do

View books with images and details.

Search by book title or author.

Filter books by category.

Sort books by title or price.

Move between pages of books.

Add and remove books from the cart.

Change book quantity.

Check available stock.

Show the cart total.

Enter customer details at checkout.

Check the phone number.

Save orders in the database.

Show the order confirmation.

Add reviews and ratings.

Find previous orders using a phone number.

Manage books through the admin panel.

View sales reports.

## Main Pages

Shop – View, search, filter and sort books.

Book Details – View information, stock and reviews for a book.

Cart – Manage selected books and view the total.

Checkout – Enter customer details and place an order.

Order Confirmation – View the completed order.

Order History – Find previous orders using a phone number.

Admin Login – Login to the admin area.

Admin Panel – Add, edit and delete books.

Admin Report – View order, revenue, book sales and category reports.

## Database

The project uses four tables:

books – Stores book information.

orders – Stores customer order information.

order_items – Stores the books included in each order.

reviews – Stores reviews and ratings for books.

## Routes

`/` – Shows books, search, category filter, sorting and pagination.

`/book/<id>` – Shows book details and reviews.

`/cart/add/<id>` – Adds a book to the cart.

`/cart` – Shows the cart and total.

`/cart/update/<id>` – Updates the book quantity.

`/cart/remove/<id>` – Removes a book from the cart.

`/checkout` – Takes customer details and places the order.

`/order/<id>` – Shows the order confirmation.

`/book/<id>/review` – Adds a review for a book.

`/order-history` – Finds previous orders using a phone number.

`/admin/login` – Shows the admin login page.

`/admin` – Shows the admin panel.

`/admin/add` – Adds a new book.

`/admin/edit/<id>` – Updates a book.

`/admin/delete/<id>` – Deletes a book.

`/admin/report` – Shows admin reports.

`/admin/logout` – Logs out the admin.

## SQL Used

The project uses SQL to:

- Read books
- Search and filter books
- Sort and paginate books
- Add orders
- Update stock
- Add reviews
- Find order history
- Manage books
- Generate admin reports

The `queries.sql` file contains 8 SQL queries required for the project.

## Final Project Structure

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
│   ├── order.html
│   ├── admin.html
│   ├── admin_login.html
│   ├── admin_report.html
│   └── order_history.html
└── static/
    ├── style.css
    ├── script.js
    └── images/