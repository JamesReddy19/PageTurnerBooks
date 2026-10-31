# PageTurner Books


## 1. Project structure

- `app.py` — application entry point.
- `pageturner/` — application factory, database helpers and four Blueprints (`auth`, `shop`, `admin`, `api`).
- `schema.sql` and `migrations/001_initial_schema.sql` — database tables and indexes.
- `migrate.py` — imports data from the earlier PageTurner Books database into a separate database; it never edits the source file.
- `backup.py` — uses SQLite's online backup API and keeps the five newest backups.
- `manage.py` — creates staff/admin accounts without allowing public registration to choose a privileged role.
- `templates/`, `static/` — pages, styling, and small browser enhancements.
- `tests/` — automated tests using `unittest` and Flask's test client.
- `queries.sql` — twelve example queries, including `EXPLAIN QUERY PLAN`.

## 2. Windows PowerShell setup

Open PowerShell in your existing `PageTurnerBooks - Copy` folder:

```powershell
cd "C:\Users\Administrator\Desktop\PageTurnerBooks - Copy"
py -m pip install -r requirements.txt
```

If PowerShell blocks activation, use this command for the current terminal only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Set a private session key and an administrator password in the same PowerShell window. Use your own strong values:

powershell
$env:SECRET_KEY = "replace-this-with-a-long-random-secret"
$env:ADMIN_EMAIL = "your-admin-email@example.com"
$env:ADMIN_PASSWORD = "replace-this-with-a-strong-admin-password"


Before first use, import your existing data into the separate new database as described in section 3. Then run the app:

powershell
py app.py


Open `http://127.0.0.1:5000`. The first run creates `instance\database.db` and adds the 12 sample books if the database is empty. Stop the server with `Ctrl+C`.

## 3. Import your existing project data (required to preserve your current records)

Keep your original `PageTurnerBooks\database.db` and `database_backup.db` untouched. First make sure the original Flask server is stopped. From the **new** project folder, run:

```powershell
py migrate.py --source ".\database.db" --target "instance\database.db"
```

This imports existing users, books, orders, order items, reviews, wishlist entries, addresses and coupons into `instance\database.db`. Legacy rupee amounts are converted to integer paise. The source file is opened read-only and is not overwritten. The import uses a transaction and checks foreign-key consistency. Existing files in the old `static\images` and `static\uploads` folders are copied into the new project when a file with the same name is not already present. If the import reports an error, do not delete or change your original database; the original files remain your recovery point.

After import, start the new app using `python app.py` and compare key counts (books, users, orders, order items, wishlist) with your old database before relying on the new copy. The migration preserves existing book/order IDs when possible. Imported reviews from the old anonymous-review system remain visible, but have no linked customer account.

## 4. Create a staff or admin account

Keep the environment variables set, then run one of these commands. You will be prompted for a password; public registration only creates customer accounts.

```powershell
py manage.py create-user --name "Store Staff" --email "staff@example.com" --role staff
py manage.py create-user --name "Store Admin" --email "admin2@example.com" --role admin
```

The `ADMIN_EMAIL` and `ADMIN_PASSWORD` settings can also create the configured administrator account on startup. Staff can view orders and update allowed order statuses, but cannot delete books or export CSV reports. Administrators can manage the full admin area.

## 5. Tests

Stop the development server and run:

```powershell
py -m unittest discover -s tests -v
```

Tests use temporary databases and do not use your working database. Fix any failing test before treating a stage as complete.

## 6. Backups

powershell
py backup.py


Optional explicit paths:

powershell
py backup.py --database "instance\database.db" --folder "backups"


Backups use `sqlite3.Connection.backup()` so the app can be running. The script keeps the five newest files matching `pageturner-*.db` in the backup folder. It does not delete or overwrite `database_backup.db` from the original project.

## 7. Main routes

| Route | Purpose |
|---|---|
| `/` | Search, category filtering, sorting, pagination and book listing |
| `/book/<id>` | Book detail and verified-purchase reviews |
| `/cart` and `/checkout` | Cart, coupon, timed stock reservations and checkout |
| `/order-history`, `/order/<id>` | Customer orders, tracking, cancellation and invoice |
| `/addresses`, `/wishlist`, `/profile`, `/devices` | Customer tools |
| `/register`, `/login`, `/logout` | Account authentication |
| `/password-reset` | One-time reset token demo (no email service) |
| `/admin/` | Administrator dashboard |
| `/admin/books`, `/admin/orders` | Book management and order status management |
| `/admin/coupons`, `/admin/returns`, `/admin/reports` | Coupons, returns and reports |
| `/admin/messages` | Contact messages |
| `/api/books`, `/api/books/<id>` | Read-only JSON API, 30 requests/minute/IP |
| `/contact` | Contact form |
| `/health` | Simple health response |

## 8. Security and business rules included

- Parameterized SQL values, password hashes, CSRF checks on every POST, role checks, owner checks and safe redirect validation.
- Five failed logins within ten minutes trigger a ten-minute lockout for that email/IP pair.
- Checkout uses `BEGIN IMMEDIATE`, checks stock again, writes all order items, decrements stock conditionally, records stock logs and commits as one unit.
- Stock reservations expire after ten minutes; expired reservations are ignored and removed on checkout.
- Order status changes use a single transition dictionary. Cancellation only applies to `Placed` orders and restores stock in a transaction.
- Coupon usage is recorded per customer/order; money fields are integer paise; shipping, tax and discounts are stored on the order.
- Reviews require a delivered purchase and are limited to one review per customer/book.
- Return requests are quantity-based and only approved returns restore stock.
- Image uploads are limited to 1 MB, use safe random filenames and validate file signatures as well as extensions.
- The API has a SQLite-backed request limit; admin changes are recorded in `admin_log`.

## 9. SQL notes

All money columns end in `_paise`. Convert rupees to paise by multiplying the decimal amount by 100, then use integer calculations. Use `?` placeholders for values. `queries.sql` contains the twelve required examples; use SQLite's command line or a SQLite viewer to run them. Query 9 needs a book ID, and queries 11–12 need the parameters shown in their comments.

## 10. Payments (simulated, not a real gateway)

`pageturner/payments.py` records a payment for every order, but **no payment provider is contacted**. Nothing is charged and no card data is collected or stored.

- Checkout offers cash on delivery, card (simulated) and UPI (simulated).
- Each order gets one row in the `payments` table with an amount, a `SIM-…` reference and `is_simulated = 1`.
- Card/UPI are stored as `Simulated - paid`; cash on delivery is stored as `Pending collection`.
- The checkout page, the order page and the confirmation email all state that the payment is simulated.
- Only the methods in `ALLOWED_METHODS` are accepted; an unknown value is rejected instead of stored.

To use a real gateway you would replace `charge()` with the provider SDK call, verify the provider webhook signature server-side, and only then mark the order as paid. That work is **not** done here and should not be assumed from the presence of the `payments` table.

## 11. Email delivery

`pageturner/mail.py` sends an order confirmation after checkout:

- If `MAIL_SERVER` is set, messages are sent through that SMTP server (`MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`). This is real delivery and needs real credentials, which this project does not ship.
- If `MAIL_SERVER` is empty (the default), messages are appended to `instance/mail_outbox.log` so the flow is testable offline. **This is not email delivery.** Set `MAIL_OUTBOX` to put that file somewhere else.

The outbox is a runtime artifact and is listed in `.gitignore`. It contains whatever addresses are used while testing, so never commit it. Automated tests point `MAIL_OUTBOX` at a temporary file and assert that the project's own outbox is untouched.

Delivery problems are logged and never roll back an order. Password-reset tokens are still displayed on the reset page, because no mail service is configured by default.

## 12. Production deployment

1. Install dependencies: `pip install -r requirements.txt` (installs Flask, plus gunicorn on Linux/macOS).
2. Configure the environment (see `.env.example`). At minimum set a strong `SECRET_KEY`; set `COOKIE_SECURE=1` when serving over HTTPS; set `ADMIN_EMAIL`/`ADMIN_PASSWORD` only for the first boot, then remove them.
3. Start a real WSGI server — do not use `app.py`, which enables the debug reloader:

   ```bash
   gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
   ```

4. Put a TLS-terminating reverse proxy in front of it, keep `instance/` on persistent storage, and schedule `python backup.py` for backups.
5. Health check: `GET /health`. Logs go to stdout/stderr from gunicorn.

If `SECRET_KEY` is not set the app logs a warning at startup because the fallback key is public and unsuitable for a deployed site.

## 13. Known project boundaries

- Payments are simulated (section 10); no provider, webhook or real charge exists.
- Email is only real when SMTP is configured; otherwise it goes to a local outbox file (section 11).
- No deployment has been performed from this repository; the gunicorn/`wsgi.py` instructions are provided but not executed.
- Notifications are created for order-status changes, return decisions and wishlist stock changes. There is no background worker; expired reservations are cleaned during checkout requests.
- Shipping-zone prefixes are starter example data in `pageturner/db.py`; update them to match any rules your project evaluator provides.
