PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'customer' CHECK(role IN ('customer','staff','admin')),
 anonymized_at TEXT,
 -- Requirement 71: TOTP second factor.  A secret can exist while enrollment is
 -- still unconfirmed, so `totp_enabled` (not the secret) is the gate.
 totp_secret TEXT,
 totp_enabled INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS books (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 title TEXT NOT NULL,
 author TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT '',
 price_paise INTEGER NOT NULL CHECK(price_paise >= 0),
 description TEXT NOT NULL DEFAULT '',
 stock INTEGER NOT NULL DEFAULT 10 CHECK(stock >= 0),
 color TEXT NOT NULL DEFAULT '#e8f1ef',
 image TEXT NOT NULL DEFAULT '',
 release_date TEXT,
 version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(title, author)
);
CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS book_categories (book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE, PRIMARY KEY(book_id, category_id));
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS book_tags (book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY(book_id, tag_id));
CREATE TABLE IF NOT EXISTS addresses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, label TEXT NOT NULL, line1 TEXT NOT NULL, city TEXT NOT NULL, pincode TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)));
CREATE UNIQUE INDEX IF NOT EXISTS one_default_address_per_user ON addresses(user_id) WHERE is_default = 1;
CREATE TABLE IF NOT EXISTS coupons (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE, percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100), expiry_date TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, min_order_paise INTEGER NOT NULL DEFAULT 0, max_discount_paise INTEGER, usage_limit INTEGER, category_id INTEGER REFERENCES categories(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER REFERENCES users(id), name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
 subtotal_paise INTEGER NOT NULL DEFAULT 0, discount_paise INTEGER NOT NULL DEFAULT 0, shipping_paise INTEGER NOT NULL DEFAULT 0, tax_paise INTEGER NOT NULL DEFAULT 0, total_paise INTEGER NOT NULL DEFAULT 0,
 date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'Placed', coupon_code TEXT, pincode TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, book_id INTEGER NOT NULL REFERENCES books(id), quantity INTEGER NOT NULL CHECK(quantity > 0), price_paise INTEGER NOT NULL CHECK(price_paise >= 0), returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK(returned_quantity >= 0));
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), comment TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, hidden_at TEXT, hidden_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, book_id));
CREATE TABLE IF NOT EXISTS wishlist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, UNIQUE(user_id, book_id));
CREATE TABLE IF NOT EXISTS cart_items (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, quantity INTEGER NOT NULL CHECK(quantity > 0), PRIMARY KEY(user_id, book_id));
CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, cart_key TEXT NOT NULL, book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, quantity INTEGER NOT NULL CHECK(quantity > 0), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_log (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL REFERENCES books(id), change_amount INTEGER NOT NULL, reason TEXT NOT NULL, order_id INTEGER REFERENCES orders(id), actor_user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS order_status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, old_status TEXT, new_status TEXT NOT NULL, changed_by INTEGER REFERENCES users(id), note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS coupon_uses (id INTEGER PRIMARY KEY AUTOINCREMENT, coupon_id INTEGER NOT NULL REFERENCES coupons(id), user_id INTEGER NOT NULL REFERENCES users(id), order_id INTEGER NOT NULL REFERENCES orders(id), UNIQUE(coupon_id, user_id));
CREATE TABLE IF NOT EXISTS password_reset_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used_at TEXT);
CREATE TABLE IF NOT EXISTS login_attempts (email TEXT NOT NULL, ip_address TEXT NOT NULL, failed_count INTEGER NOT NULL DEFAULT 0, window_started TEXT NOT NULL, locked_until TEXT, PRIMARY KEY(email, ip_address));
CREATE TABLE IF NOT EXISTS user_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash TEXT NOT NULL UNIQUE, device_name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS admin_log (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', prev_hash TEXT, row_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_chain_head (id INTEGER PRIMARY KEY CHECK (id = 1), last_row_id INTEGER NOT NULL DEFAULT 0, last_hash TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS book_price_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 old_price_paise INTEGER CHECK(old_price_paise IS NULL OR old_price_paise >= 0),
 new_price_paise INTEGER NOT NULL CHECK(new_price_paise >= 0),
 reason TEXT NOT NULL DEFAULT '',
 actor_user_id INTEGER REFERENCES users(id),
 changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS api_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS contact_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, message TEXT NOT NULL, link TEXT NOT NULL DEFAULT '/', is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS returns (id INTEGER PRIMARY KEY AUTOINCREMENT, order_item_id INTEGER NOT NULL REFERENCES order_items(id), user_id INTEGER NOT NULL REFERENCES users(id), quantity INTEGER NOT NULL CHECK(quantity > 0), reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Rejected')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS shipping_zones (prefix TEXT PRIMARY KEY, fee_paise INTEGER NOT NULL DEFAULT 5000);

-- Requirement 50: one "helpful" vote per user per review, enforced here.
CREATE TABLE IF NOT EXISTS review_votes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(review_id, user_id)
);
-- Requirement 51: customer reports feed the admin moderation queue.
CREATE TABLE IF NOT EXISTS review_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
 reporter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 reason TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','hidden','kept')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 resolved_at TEXT,
 resolved_by INTEGER REFERENCES users(id),
 UNIQUE(review_id, reporter_user_id)
);
-- Requirement 52: repeated requests for the same title increment a counter.
CREATE TABLE IF NOT EXISTS book_requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 normalized_title TEXT NOT NULL UNIQUE,
 title TEXT NOT NULL,
 author TEXT NOT NULL DEFAULT '',
 request_count INTEGER NOT NULL DEFAULT 1 CHECK(request_count > 0),
 first_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Requirement 53: revocable, unguessable wishlist share links.
CREATE TABLE IF NOT EXISTS wishlist_shares (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
CREATE INDEX IF NOT EXISTS idx_order_items_book ON order_items(book_id);
CREATE INDEX IF NOT EXISTS idx_reservations_expiry ON reservations(expires_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_api_requests_ip_time ON api_requests(ip_address, created_at);
CREATE INDEX IF NOT EXISTS idx_price_history_book ON book_price_history(book_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_reviews_book_visible ON reviews(book_id, hidden);
CREATE INDEX IF NOT EXISTS idx_review_votes_review ON review_votes(review_id);
CREATE INDEX IF NOT EXISTS idx_review_reports_status ON review_reports(status, review_id);
CREATE INDEX IF NOT EXISTS idx_book_requests_count ON book_requests(request_count DESC);

-- ---------------------------------------------------------------------------
-- Batch 5 (requirements 61-65)
-- ---------------------------------------------------------------------------

-- Requirement 61: a refresh, a Back button or a double click re-sends the same
-- POST. The checkout form carries a single-use token; the token row records the
-- order it produced, so a replay can be answered with that order instead of a
-- second one.
CREATE TABLE IF NOT EXISTS checkout_tokens (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token_hash TEXT NOT NULL UNIQUE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 used_at TEXT,
 order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL
);

-- Requirement 63: data-driven pricing rules, editable without a code change.
CREATE TABLE IF NOT EXISTS pricing_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE,
 rule_type TEXT NOT NULL
  CHECK(rule_type IN ('category_percent','book_percent','min_quantity_percent')),
 category TEXT,
 book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
 min_quantity INTEGER NOT NULL DEFAULT 1 CHECK(min_quantity >= 1),
 percent_off INTEGER NOT NULL CHECK(percent_off BETWEEN 1 AND 90),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(
  (rule_type = 'category_percent' AND category IS NOT NULL AND book_id IS NULL)
  OR (rule_type = 'book_percent' AND book_id IS NOT NULL)
  OR (rule_type = 'min_quantity_percent')
 )
);

-- Requirement 64: gift cards. Only the SHA-256 of the code is stored, so a
-- database leak cannot be turned into free money; the balance is the ledger SUM,
-- never a mutable counter.
CREATE TABLE IF NOT EXISTS gift_cards (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code_hash TEXT NOT NULL UNIQUE,
 code_last4 TEXT NOT NULL,
 initial_paise INTEGER NOT NULL CHECK(initial_paise > 0),
 issued_to_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 purchased_order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
 expires_at TEXT,
 disabled_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gift_card_ledger (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 gift_card_id INTEGER NOT NULL REFERENCES gift_cards(id) ON DELETE CASCADE,
 order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
 amount_paise INTEGER NOT NULL,
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Requirement 65: a backorder is a quantity a customer paid for but that the
-- warehouse cannot ship yet. `books.stock` never goes negative; the queue is
-- allocated oldest-first when stock returns.
CREATE TABLE IF NOT EXISTS backorders (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
 quantity INTEGER NOT NULL CHECK(quantity > 0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 fulfilled_at TEXT,
 fulfilled_quantity INTEGER NOT NULL DEFAULT 0
);

-- Requirement 69: a transactional notification outbox. The business event and
-- its delivery row are written on the same connection, inside the same
-- transaction, so a rolled-back order can never email a customer about an order
-- that does not exist. Attempts, backoff and give-up live on this row; the
-- dispatcher in `pageturner.outbox` owns the retry policy.
CREATE TABLE IF NOT EXISTS notification_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
 channel TEXT NOT NULL DEFAULT 'email' CHECK(channel IN ('email')),
 recipient TEXT,
 subject TEXT NOT NULL,
 body TEXT NOT NULL,
 dedupe_key TEXT UNIQUE,
 status TEXT NOT NULL DEFAULT 'pending'
  CHECK(status IN ('pending', 'sent', 'failed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts > 0),
 next_attempt_at TEXT NOT NULL,
 last_error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 sent_at TEXT
);

-- Requirement 70: the monthly featured book. One pick per calendar month.
CREATE TABLE IF NOT EXISTS book_of_the_month (
 month TEXT PRIMARY KEY,
 book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscriptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active', 'paused', 'cancelled')),
 quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity BETWEEN 1 AND 5),
 next_run_on TEXT NOT NULL,
 last_run_on TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 cancelled_at TEXT
);

-- Idempotency for the "run due" button: one charge per subscription per month,
-- enforced by the database rather than by trusting the caller.
CREATE TABLE IF NOT EXISTS subscription_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 run_on TEXT NOT NULL,
 order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
 outcome TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(subscription_id, run_on)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_live_user
 ON subscriptions(user_id) WHERE status IN ('active', 'paused');
CREATE INDEX IF NOT EXISTS idx_subscriptions_due
 ON subscriptions(status, next_run_on);
CREATE INDEX IF NOT EXISTS idx_outbox_due
 ON notification_outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_outbox_user
 ON notification_outbox(user_id);
CREATE INDEX IF NOT EXISTS idx_subscription_runs_month
 ON subscription_runs(run_on);

CREATE INDEX IF NOT EXISTS idx_checkout_tokens_user
 ON checkout_tokens(user_id, used_at);
CREATE INDEX IF NOT EXISTS idx_gift_card_ledger_card
 ON gift_card_ledger(gift_card_id);
CREATE INDEX IF NOT EXISTS idx_backorders_book
 ON backorders(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_backorders_open
 ON backorders(fulfilled_at, book_id);
