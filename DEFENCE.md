# PageTurner Books — project defence notes

Use these short answers to revise after the application is tested. They are prompts for your own explanation, not a replacement for understanding the code.

## Database and reliability

**1. Why use `?` in SQL?**
It separates SQL instructions from user-provided values. SQLite safely binds each value, reducing SQL injection risk.

**2. Why use a transaction during checkout?**
An order, its order items, stock deductions, stock logs and coupon use must succeed together. If one part fails, `rollback()` undoes the whole transaction.

**3. Why use `BEGIN IMMEDIATE`?**
It acquires SQLite's write reservation before checking stock or coupon usage, so two checkouts cannot both pass the same stock check and then oversell the same copies.

**4. Why does stock use `UPDATE ... WHERE stock >= ?`?**
The database only deducts the quantity when enough stock remains. Checking `rowcount` confirms that the conditional update succeeded.

**5. Why use JOINs?**
Related information is stored in different tables. JOINs let a page show an order with its customer and books without copying every detail into one table.

**6. Why normalize categories and tags?**
A book can belong to several categories and have several tags. Junction tables (`book_categories`, `book_tags`) model those many-to-many relationships without repeating category names in each row.

**7. Why integer paise instead of floating-point rupees?**
Binary floating-point cannot represent every decimal fraction exactly. Integer paise makes price, tax, discount and totals predictable.

**8. What does a migration do?**
It changes the database structure or imports old data in a controlled way. This project imports the old database into a separate destination and leaves the source untouched.

## Authentication and security

**9. Why hash passwords?**
A password hash is stored instead of the original password. During login, Werkzeug checks the entered password against that hash.

**10. What is CSRF protection?**
A random token is stored in the session and must be sent with each POST request. A different website normally cannot read the token and submit a valid request as the signed-in user.

**11. What is the login lockout?**
Five failed attempts for the same email and IP within ten minutes trigger a ten-minute lock. It slows repeated password guessing.

**12. Why check ownership for an order or address?**
Knowing an ID is not permission to view or edit the record. The SQL query checks both the record ID and the logged-in user's ID.

**13. What is role-based access?**
A customer can shop and view their own orders. Staff can manage order statuses and messages. An administrator has access to book management, exports and full reports.

**14. Why hash session tokens in the database?**
The browser keeps the raw random token, but the database stores only its SHA-256 hash. A database-only leak does not directly reveal the active token.

**15. Why validate image signatures?**
A filename extension can be misleading. The upload checks both the extension and the beginning of the file contents and uses a random safe filename.

## Business rules

**16. How do ten-minute stock reservations work?**
When checkout opens, the app records each reserved quantity with an expiry time. Availability subtracts other active reservations from stock. Expired rows are ignored and cleaned on checkout requests; no background worker is needed.

**17. Why are order totals stored?**
Prices, discounts, shipping rules and tax may change later. Storing the order's own subtotal, discount, shipping, tax and total preserves what the customer was charged at checkout.

**18. How are coupons protected against double use?**
Checkout uses a write transaction, checks the usage limit and customer history, then inserts a row in `coupon_uses`. A unique constraint prevents the same customer from using that coupon twice.

**19. Why restrict cancellation to `Placed`?**
After an order has moved to packing or shipping, restoring all stock automatically could create incorrect inventory. Only a still-placed order can be cancelled by the customer.

**20. How are partial returns handled?**
A return request names an order item and quantity. Approval checks that the request is still pending, updates returned quantity and stock, and adds a stock-log entry in one transaction.

**21. Why use an order status history table?**
The current status says where the order is now. The history records each transition, who made it and when, which makes a tracking timeline possible.

**22. Why rate-limit the API?**
The API stores request timestamps by IP address and limits an IP to 30 requests per minute. It returns JSON status 429 when the limit is reached.

**23. What is the difference between a normal form submission and `fetch()`?**
A normal form submits and navigates/reloads the page. `fetch()` sends the request in the background, so JavaScript can update the cart badge or show search suggestions without a full page reload.

**24. Why is the backup script safe while the app is running?**
It uses SQLite's built-in `Connection.backup()` API rather than copying a live database file byte-for-byte.

## Before the demo

- Register a customer and sign in.
- Add a book to the cart, apply a coupon if one exists, and complete checkout.
- Check that stock fell by the purchased quantity and the order appears in order history.
- Try to open another customer's order; it should not reveal the order.
- Sign in as staff and confirm book management and exports are denied.
- Sign in as admin and test book edits, stock logs, coupons, status updates, reports and CSV export.
- Run `python -m unittest discover -s tests -v` and review every failure.
- Run `python backup.py` and confirm a dated backup is created.
