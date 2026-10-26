# Requirements 47–75 — verified gap analysis

Status: **audit complete; batch 1 (deployment) and batch 2 (wiring) implemented.**
Baseline at time of audit: branch `master`, HEAD `19ac350`, worktree clean,
`python3 -m pytest tests/ -q` → **29 passed**, `compileall` clean.

Implemented and proven since then (each row below carries its own evidence):
**47, 48, 49, 56, 57, 67, 75**. Test suite is now **32 passed**
(the original 29 plus 3 staff-role tests — no existing test was weakened).

Status vocabulary:

- **Verified** — behaves as the requirement asks, proven by reading the code path.
- **Done** — implemented *and* exercised through the real HTTP surface by a
  check whose result is recorded in this file.
- **Partial** — the storage, helper or single module already exists, but the
  feature is not reachable / not wired to its consumers.
- **Missing** — no code does this today.
- **Blocked** — cannot be finished without a decision (see *Open decisions*).

---

## Corrected audit context

An earlier pass contained claims that were **wrong** and are retracted here so
nobody rebuilds something that already works:

- ~~"blueprints are imported by nothing, so their routes never register"~~ —
  **false**. All blueprints register; the app serves 77 routes.
- ~~"templates with no backing route"~~, ~~"`/books` returns 404"~~,
  ~~"`/api/health` 404"~~ — **false**. That audit globbed
  `pageturner/templates/`, which does not exist. Templates live at `./templates`.
  Re-run against the real directory: **no broken `url_for`**; the only
  "unmatched" template is `base.html`, which is expected (it is `{% extends %}`ed).
- `admin.py.backup` / `style.css.backup` are dead files and can be deleted
  in a cleanup batch, not now.

Schema ownership is settled and must be respected: `schema.sql` is authoritative
for base tables; `db.py::init_db()` executes it then applies **additive,
idempotent** migrations (`add_column_if_missing`, `CREATE TABLE IF NOT EXISTS`).
Every new table below follows that same pattern, so restarts never drop data.

---

## The matrix

### Part B — Money and loyalty

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 47 | Flash sale with a timer | **Done** | `helpers.effective_book_price()` is now called from the cart, the checkout total, the product page and the order line, so the sale price reaches `order_items.price_paise` and the invoice without any later re-read of `books`. The product page renders the countdown from `sale_ends_in_seconds`. |
| 48 | Gift option | **Done** | Checkout accepts a gift-wrap checkbox (₹50) and a message capped at 150 characters; both persist to `orders.gift_wrap_paise` / `orders.gift_message`, are added to the total, and print on the invoice. Jinja `autoescape` renders the message inert. |
| 49 | Loyalty points | **Done** | Points are earned on order placement (`spent // 10000`), redeemable at checkout against the pre-tax subtotal, and reversed when an order is cancelled or refunded — the ledger `SUM` is the only balance, so no drift is possible. |

### Part C — Community

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 50 | Review helpful votes | **Done** | `review_votes(review_id, user_id)` carries `UNIQUE(review_id, user_id)`, so one account cannot vote twice — the database, not the route, is the guarantor. `reviews.vote_helpful()` uses `INSERT OR IGNORE` and reports whether a vote was really added, `remove_helpful_vote()` is idempotent, and `vote_counts_for()` returns the tally plus whether the signed-in visitor already voted. A second submit therefore leaves the count at 1 and toggling off then on returns to 1, never 2. Verified by 4 tests. |
| 51 | Report a review (moderation) | **Done** | Decision taken: a hidden review counts to **neither** the average nor the count. The seven hand-written rating SQL fragments that used to live in `shop_catalog`/`shop` were replaced by two constants in `pageturner/reviews.py` (`AVERAGE_RATING_SQL`, `REVIEW_COUNT_SQL`) which carry `AND r.hidden = 0`; every rating number on the site now interpolates them, so the rule cannot be bypassed by adding a new query. Customers report through `POST /reviews/<review_id>/report` with a reason from `REPORT_REASONS`; `GET /admin/reviews` is the moderation queue (open/hidden/kept/all), and hide/keep record `resolved_by`. Verified by 6 tests: with the filter removed the average returns to 3.0 over 2 reviews and the storefront renders "Customer reviews (2)", both failing as expected. |
| 52 | Request a book | **Done** | `book_requests` is keyed on `normalized_title` (lower-cased, whitespace-collapsed, punctuation-stripped) with `UNIQUE(normalized_title)`, so "The  Hobbit!" and "the hobbit" are one demand signal. `book_requests.record_request()` upserts `request_count = request_count + 1`; the customer form lives at `/request-book` and the admin view `GET /admin/book-requests` orders by `request_count DESC`, with `POST /admin/book-requests/<id>/fulfil` to close one out. Verified by 4 tests; removing the `ON CONFLICT` branch makes the second request raise a UNIQUE violation, which is the fail-then-pass proof that grouping is real. |
| 53 | Share my wishlist | **Done** | `wishlist_shares(user_id UNIQUE, token_hash UNIQUE, revoked_at)`. The plaintext token is shown once from the owner's own session and never stored — only its SHA-256 digest is persisted, so a database dump cannot be replayed. `GET /wishlist/shared/<token>` renders a read-only public page; an unknown **or revoked** token returns 404 rather than an empty list, so revocation is indistinguishable from a link that never existed. `POST /wishlist/share` re-issues, `POST /wishlist/share/revoke` kills the link. Verified by 5 tests; dropping the `revoked_at IS NULL` predicate makes the revoked link return 200, which is the fail-then-pass proof. |
| 75 | Recommendations for me | **Done** | `recommended_books()` builds on the existing co-purchase query, drops books the shopper already owns, and falls back to bestsellers for a visitor with no orders. Rendered as a block on the home page only for the first page with no search/filter. Verified: a co-bought title is recommended and an owned title is excluded. |

### Part D — Store owner tools

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 54 | Price history | **Done** | `book_price_history(book_id, old_price_paise, new_price_paise, reason, actor_user_id, changed_at)`. Every price write goes through one choke point, `pricing.set_book_price()`, used by add-book, edit-book, bulk repricing and import. History is recorded only when the price actually changes (no row for a no-op save), and a one-time baseline row (`reason='initial price'`, `old_price_paise IS NULL`) is backfilled idempotently on boot and after demo seeding so the trail has a known starting point. `GET /admin/books/<id>/price-history` returns JSON newest-first, admin-only (403 for a customer). Verified by 4 tests. |
| 55 | Bulk price change | **Done** | `POST /admin/books/prices/bulk` takes `book_id[]` + `percent` (-100..500, sign picks increase/discount) and reprices the selected books through `set_book_price()` with reason `bulk +15%` / `bulk -25%`, so each book gets its own history row and audit entry. Rounding is exact integer half-up via `pricing.apply_percentage()` (no floats): 5% of 1999 → 2099, 33% off 100 → 67, 50% of 1 → 2. Out-of-range percent, non-numeric percent, and an empty selection all change nothing. Verified by 5 tests. |
| 56 | Dashboard charts (CSS only) | **Done** | The dashboard now builds a zero-filled 7-day series and renders it as div bars with a `--bar-height` percentage per day (no chart library, no JS). Verified: the admin page contains exactly 7 bars for a week with a single order (`0,0,0,0,0,0,100.0`). |
| 57 | Staff role with limited rights | **Done** | `/admin/users` (admin-only) lists members and lets an admin set `role=staff`, writing an `admin_log` row with action `user_role_changed`. Staff keep the 5 order/return routes (`/admin/orders` → 200) and are still refused `/admin/books` and `/admin/users` (403). Covered by three tests added in batch 2 (`test_27`–`test_29`), including the denial side. |

### Part E — User control

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 58 | Download my data | **Done** | `GET /profile/export` streams a JSON document (`Content-Disposition: attachment`) containing the signed-in user's profile, orders with line items, saved addresses, reviews, wishlist and loyalty ledger. Scoped strictly by `session['user_id']`; anonymous users are redirected to login and never reach the query. Verified by `tests/test_data_export.py` (own-data-only, attachment header, valid JSON). |
| 59 | Delete my account | **Done** | The `NOT NULL UNIQUE` email conflict is resolved by writing a unique non-deliverable placeholder (`deleted+<id>@anonymised.invalid`) instead of NULL, and `users.anonymized_at` records the closure. `GET /account/delete` is the confirmation screen; `POST /account/delete` requires the account password **and** typing `DELETE`, then: rewrites name/email/password hash (unguessable), purges sessions, cart, wishlist, addresses, notifications, votes, points and reset tokens, scrubs the name snapshot on old reviews, and overwrites order name/phone/address/pincode while **keeping the orders and their amounts for accounting**. `current_user()` treats any session belonging to an anonymized account as unauthenticated, so an already-open browser is logged out on its next request, and `anonymize_user()` returns `None` on a second attempt (idempotent). Verified by 4 tests; skipping the order scrub leaves "Delete Me" on the order and fails. |
| 60 | Dark mode toggle | **Done** | `data-theme` attribute on `<html>` drives a CSS-variable palette in `static/style.css`; `static/script.js` persists the choice in `localStorage` and an inline pre-paint script in `base.html` applies it before first paint to avoid a white flash. Toggle is present in the header for every page and honours `prefers-color-scheme` when no choice is stored. Verified by `tests/test_dark_mode.py`. |

### Part F — Hard orders and money

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 61 | Idempotent checkout | **Done** | `checkout_tokens(token_hash UNIQUE, user_id, used_at, order_id)` stores only the SHA-256 of the token. `GET /checkout` mints the token inside the same `BEGIN IMMEDIATE` that creates the stock reservation, so a token can never outlive its reservation, and nothing is kept in the session (two tabs each get their own token and both may order). `POST /checkout` claims it with a conditional `UPDATE ... WHERE used_at IS NULL` inside the order transaction, so exactly one of two concurrent posts wins. The state machine is `claimed` / `replayed` / `unknown`: a replayed token redirects to the order it already produced — even though the cart is now empty, which is why the empty-cart branch checks for a replay first — and an unknown or blank token refuses the order with "Your checkout form expired" and places nothing. Verified by `test_replayed_checkout_places_exactly_one_order` and `test_checkout_without_a_token_is_refused`; making `claim_checkout_token()` always answer `claimed` puts a second copy of the same order in the database and lets a tokenless POST through, which is the fail-then-pass proof. |
| 62 | Edit an order before packing | **Done** | `GET/POST /order/<id>/edit` (`shop_order_edit.py`) is gated on `EDITABLE_STATUSES = ("Placed",)`; once the warehouse has packed, shipped or delivered, `GET` refuses with an explanation and `POST` changes nothing. An edit re-derives the money from the surviving lines rather than adjusting the old total: subtotal, coupon (re-validated through the existing `coupon_discount()`, so `min_order_paise` / `usage_limit` are re-checked), the req-63 pricing-rule discount, the free-shipping threshold, tax and the grand total. Stock moves with the edit through `stock_log` (+/- per line), a line may not be pushed past `MAX_QUANTITY_PER_LINE = 50`, removing the last line cancels instead of leaving an empty order, and another customer's order is a 404. Verified by 3 tests; widening `EDITABLE_STATUSES` to include `Packed`/`Shipped` marks a shipped order as editable and fails. |
| 63 | Pricing rules engine | **Done** | `pricing_rules(name UNIQUE, rule_type, category, book_id, min_quantity, percent_off, active)` with a CHECK that ties the shape to the columns, and three kinds: `category_percent`, `book_percent` and `min_quantity_percent` (whole cart once it holds N copies). The engine is `pricing_rules.evaluate()`, returning a total discount plus a per-book split so no line can go below zero; percentages stack additively but are capped at the line value. Rules are data, so `GET /admin/pricing-rules` lists them, `POST /admin/pricing-rules` adds one, `POST /admin/pricing-rules/<id>/toggle` switches one off without deleting it, and `GET /admin/pricing-preview` shows what they would do to the current catalogue. Checkout applies them after the coupon and before tax, and the discount is persisted as `orders.rules_discount_paise` (additive migration in `db.py`) next to the coupon total, so an invoice can explain both. Verified by 3 tests; dropping `WHERE active = 1` makes a disabled rule take 10000 paise off and fails. |
| 64 | Gift cards | **Done** | Decision 3 taken: everything is simulated, there is no gateway — buying a card debits the order the shop already charges. `gift_cards(code_hash UNIQUE, code_last4, initial_paise, issued_to_user_id, purchased_order_id, expires_at, disabled_at)` keeps only the SHA-256 of the code (the plaintext is shown exactly once, at issue) plus the last four digits so a customer can recognise their card. The balance is never a column: `gift_card_balance()` is `SUM(amount_paise)` over `gift_card_ledger`, and the initial value is itself a ledger row, so a spend is just a negative row and the ledger always explains the balance. `redeem_gift_card()` applies `min(balance, amount_owed)`, so a card can pay part of an order and never more than it holds; the ledger row is deferred to `record_redemption()` after the order row exists. Cards are per-customer (`cards_for_user`, no code in the listing), a disabled or expired card is refused, and one customer cannot cancel another's card. Verified by 6 tests; removing the `min(balance, ...)` cap charges 29000 paise against a 20000 balance and fails, and removing the post-write `SUM` re-check lets `record_redemption` overdraw a card instead of raising. |
| 65 | Backorders with a fair queue | **Done** | Decision 4 taken: **`books.stock` never goes negative.** `backorders(book_id, user_id, order_id, quantity, fulfilled_at, fulfilled_quantity)`. Adding to the cart and opening checkout now reserve only what exists and accept the remainder as a backorder (with a message saying how many copies are backordered) instead of rejecting the order; `GET /checkout` re-derives `backordered_quantity` per line, `place_order()` ships `min(quantity, available_now, reserved)` and queues the rest, so the customer pays the same total either way. `backorders.allocate_backorders()` fills the queue oldest-first when stock returns and debits `stock_log` for each fulfilment, and `POST /admin/backorders/allocate/<book_id>` is the restock action (staff allowed). Verified by 2 tests; letting `available_now` ignore the shelf creates no backorder row for a customer who ordered past the stock, which is the fail-then-pass proof. |

### Part G — Data and concurrency

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 66 | Optimistic locking on admin edits | **Done** | `books.version` defaults to 1 and is bumped exactly once per accepted save. `edit_book` respects the hidden `version` field: a stale value, or a missing one, raises `BookEditConflict` and renders a 200 page saying the book "was changed by someone else" with **no** write to the row. The bump happens via `set_book_price()` when the price moves, and via an explicit `version = version + 1` when only non-price fields changed, so two concurrent editors cannot both win. Verified by 4 tests, one proven to fail with the version predicate removed. |
| 67 | Keyset pagination for the API | **Done** | `/api/books` accepts `?limit=` (1–100, default 20) and `?after=<opaque cursor>`, and returns `next_cursor`/`next`. Ordering is the total order `relevance DESC, title COLLATE NOCASE, id`, so pages never overlap or skip. Verified by walking all 12 books in 4 pages with no duplicates and no gaps; an unparseable cursor returns 400 instead of silently restarting. |
| 68 | Tamper-evident audit log | **Done** | `admin_log` gained `prev_hash`/`row_hash`; `helpers.log_admin_action()` is the single chokepoint that computes `sha256(prev_hash + canonical fields)`, linking each row to its predecessor from `GENESIS_HASH`. `audit.verify_chain()` walks the chain and compares the newest `row_hash` with the `audit_chain_head` pointer. Verified by 6 tests over real rows: editing `detail`, deleting a middle row, deleting the newest row (caught by the head pointer) and nulling `row_hash` are each detected, while an untouched chain verifies ok. |
| 69 | Notification outbox with retry | **Done** | A separate `notification_outbox(user_id, channel, recipient, subject, body, status, attempts, max_attempts, next_attempt_at, last_error, dedupe_key UNIQUE, sent_at, created_at)` — deliberately *not* the in-app `notifications` table, which stays for messages the customer reads on site. `outbox.enqueue()` deliberately does **not** commit: it takes the caller's connection, so the message is written inside the same transaction as the event that caused it, and `INSERT OR IGNORE` on `dedupe_key` makes a replay a no-op (returns `None`). Checkout queues `order:{id}:confirmed` next to the order row itself, so an order can never exist without its confirmation and a confirmation can never announce an order that rolled back. `outbox.due()` selects `status = 'pending'` ordered by `next_attempt_at, id`; `process_due()` hands each to `transport`, marks `sent` on success, and on failure increments `attempts`, stores `last_error` and pushes `next_attempt_at` out by `backoff_seconds()` — exponential and deterministic, so a failing provider is not hammered. Once `attempts >= max_attempts` the row is parked as `failed` with the cause retained; `retry_now()` is the explicit human retry. The built-in transport is a log sink (`pageturner.outbox` logger), which is a real inspectable target, and a deployment with a mail provider swaps it through config. Admin has `GET /admin/outbox` (filter by status + per-status counts), `POST /admin/outbox/process` and `POST /admin/outbox/<id>/retry`, all audited. Verified by 6 tests; forcing `backoff_seconds()` to 0 makes the immediate-retry test fail, removing the attempt cap gives 3 attempts against a budget of 2, dropping `OR IGNORE` raises the UNIQUE error, and removing the checkout `enqueue()` leaves the order with no confirmation. |
| 70 | Book of the month subscription | **Done** | `subscriptions(user_id, book_id, quantity, next_run_on, active, created_on, cancelled_on)` with `UNIQUE(user_id, book_id)` so a customer has one standing order per title, plus `subscription_runs(subscription_id, run_on, outcome, order_id, note)` whose `UNIQUE(subscription_id, run_on)` is the idempotency key. Decision 3 is respected: a subscription does **not** invent a payment path — `_charge_once()` builds a normal `orders` row and its `order_items`, using the existing pipeline so gift cards, pricing rules, stock and backorders all apply. The run ships `min(quantity, available_now)` from the shelf and queues the rest through `backorders`, exactly as checkout does. `run_due()` walks `active = 1 AND next_run_on <= today` and returns an `ordered/skipped/blocked` summary; a blocked run (title unreleased, or the book gone) records the reason in `subscription_runs` and **does not advance** `next_run_on`, so nothing is silently skipped and a human can see why. Pressing the button twice cannot double-charge: the UNIQUE row and an `already charged` early return make the second press a `skipped`. `next_run_on` advances by one month from the run date. Admin: `GET /admin/book-of-the-month` (feature the current title), `POST /admin/subscriptions/run`. Customers self-serve `GET/POST /subscription`. Each delivery also queues its own outbox message keyed `subscription:{id}:{run_on}`. Verified by 6 tests; disabling the `already` check raises the UNIQUE constraint on the second press, disabling the release gate turns a blocked run into an order, and removing the run's `enqueue()` leaves the delivery unannounced. |

### Part H — Advanced security

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 71 | Two-factor login (TOTP, stdlib) | **Missing** | No `hmac`/`struct` TOTP anywhere; `users` has no secret column. No replay protection store. |
| 72 | API keys with scopes | **Missing** | No `api_keys` table, no auth decorator on `/api/`. `api.rate_limit()` exists but is keyed by **IP**, not by key. A JSON 429 handler already exists (`site.py:58`). |
| 73 | Permissions table | **Missing** | `role_required` is used at **27 call sites**; no `permissions` / `role_permissions` tables. Largest refactor in the set. |

### Part I — Analytics

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 74 | Growth report with window functions | **Missing** | Reports are date-range aggregates; no `LAG` / `SUM OVER`. The CSV export already exists and can serve as the hand-check the acceptance criterion demands. |

**Tally (29 requirements):** 13 **Done** (47, 48, 49, 54, 55, 56, 57, 58, 60,
66, 67, 68, 75) · **0 Partial** · **16 Missing** — of which **2 are additionally
Blocked** on a decision (51, 59).

---

## Reuse first — do not rebuild these

| Existing asset | Location | Serves |
|---|---|---|
| `effective_book_price()` | `helpers.py:212` | 47 (already correct, just uncalled) |
| `points_ledger` + `loyalty_points_balance/earned/discount_paise` | `db.py`, `helpers.py:244-263` | 49 |
| `customers_also_bought()` | `shop.py:173` | 75 |
| `log_admin_action()` single chokepoint | `helpers.py:123` | 68, 69 |
| `notifications` + `add_notification()` | `helpers.py:139` | 69, 70 |
| `api.rate_limit()` + JSON 429 handler | `api.py:9`, `site.py:58` | 72 |
| `role_required()` | `helpers.py:92` | 57, 73 |
| `coupon_discount()` re-checks min order | `shop.py:109-160` | 62 |
| `order_items.price_paise` snapshot (already written; invoice reads it) | `shop_checkout.py`, `invoice.html` | 47, 62, 63 |
| `money()`, `rupees_to_paise()` (Decimal in, int paise out) | `helpers.py:26-41` | all money work |

Money hygiene is currently **good**: input via `Decimal(...).quantize(ROUND_HALF_UP)`,
integer paise throughout, tax rounded `(base*pct + 50)//100`, formatting via
`divmod`. No floats in any money path. **Any new money code must keep using
`money()`** — note `manage_sales.html` does `sale_price_paise / 100` for display,
the one place that risks importing a float.

---

## Suggested batches (dependency-ordered)

1. ~~**Wire what exists (cheapest, highest value):** 47, 48, 49, 56, 57, 67, 75.
   Mostly connecting existing columns/helpers to routes and templates.~~
   **Shipped** — see the "Done" rows above.
2. **New single-table features:** 50, 51, 52, 53, 54, 55, 58, 59, 60, 66, 68.
3. **Transactional money:** 61, 62, 63, 64, 65.
4. **Background/asynchronous-ish:** 69, 70.
5. **Security:** 71, 72, 73 (73 last — it touches 27 call sites).
6. **Analytics:** 74 (independent).
7. **Tests (76) continuously; `stress.py` (77) after 61/65.**

---

## Batch log

### Batch 1 — deployment readiness (no UI change)

| Fix | File |
|---|---|
| `IndentationError` at `migrate.py:26` healed; `migrate()` body no longer truncated. Migration behaviour unchanged (`PRAGMA foreign_keys = OFF`, guards in place). | `migrate.py` |
| `api_bp` registered in the app factory (was missing, so `/api/*` did not exist). | `pageturner/__init__.py` |
| Legacy routes registered through `pageturner/legacy.py` with the original URL rules, no duplicate endpoints. | `pageturner/__init__.py`, `pageturner/legacy.py` |
| Port comes from the platform (`PORT`), 5000 only as the local fallback; `0.0.0.0` bind. | `app.py`, `wsgi.py`, `gunicorn.conf.py` |
| WSGI entry point `wsgi:app` + `gunicorn` dependency. | `wsgi.py`, `requirements.txt` |
| `SECRET_KEY` has **no** predictable fallback: `create_app()` refuses to start outside `APP_ENV=development`/`TESTING`. | `pageturner/config.py`, `pageturner/__init__.py` |
| SQLite persistence, uploads and admin bootstrap documented as environment-driven; no reseeding on boot. | `pageturner/config.py`, `DEPLOYMENT.md` |
| Single gunicorn worker (`gthread`) — SQLite allows one writer; multi-process workers would hit `database is locked`. | `gunicorn.conf.py` |

### Batch 2 — requirements 47, 48, 49, 56, 57, 67, 75

Checks actually executed (not asserted):

```
python -m pytest tests/ -q          → 32 passed  (original 29 + 3 staff tests)
python3 -m compileall -q .          → clean
scratch walk of /api/books          → 12/12 books, 4 pages, no dupes/gaps,
                                      invalid cursor → 400
scratch cart→checkout→invoice       → sale price snapshot, gift wrap ₹50 +
                                      message, points earned & reversed
scratch admin dashboard             → exactly 7 bars
scratch /admin/users role change    → staff set, admin_log written;
                                      staff 200 on /admin/orders, 403 on
                                      /admin/books and /admin/users
```

### Batch 2b — checkout ledger hotfix (found while reviewing batch 2)

The batch-2 `stock_log` insert had been placed **inside** the `if points_earned:`
branch and **outside** the per-book loop, so a purchase logged only the last line
item of a multi-book order, and logged nothing at all when the order was too
small to earn loyalty points. The ledger then disagreed with `books.stock` — a
problem for reqs 61, 65 and 66, which all treat the ledger as the audit trail.

| Fix | File |
|---|---|
| The `stock_log` row now sits in the per-book loop, immediately after the `order_items` insert and the stock debit, inside the same transaction. | `pageturner/blueprints/shop_checkout.py` |
| Three regression tests: every line logged, a zero-point order still logged, and purchase-then-cancel nets to zero with stock restored. | `tests/test_checkout_ledger.py` |

Proof the tests pin the bug rather than the fix: with the buggy ordering
temporarily restored, `test_every_purchased_line_is_logged` and
`test_purchase_is_logged_even_when_no_points_are_earned` **fail**; with the fix
in place all three pass.

```
python -m pytest -q        → 35 passed
python3 -m compileall -q . → clean
```

### Deployment configuration still manual

The repository cannot create host resources, so these stay as operator steps in
`DEPLOYMENT.md`: the persistent disk mount (`/data`), `SECRET_KEY`,
`ADMIN_EMAIL`/`ADMIN_PASSWORD`, and running `python migrate.py` **once** during
release — never on boot, so a restart can never reseed or wipe a live database.

---

## Gap list — what remains (47–76)

**Done and verified by executed checks:** 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,
57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 75 (23 of 29). **Remaining: 6**
(69, 70, 71, 72, 73, 74). Ordered by dependency, cheapest first, so each batch
leaves the app deployable.

Batches 3, 4 and 5 are complete; their rows are retained below only as a record
of what shipped in each batch. All four open decisions are now answered and
implemented, so nothing in 47–76 is blocked on a product call any more.

| Batch | Reqs | Missing capability (one line each) | New storage |
|---|---|---|---|
| ~~3~~ | ~~60~~ | **Shipped.** Dark mode: no `localStorage`, no CSS-variable theme, no pre-paint script (white flash) | — |
| ~~3~~ | ~~58~~ | **Shipped.** Data export: `auth_profile` has no JSON download route, no `Content-Disposition` | — |
| ~~3~~ | ~~54~~ | **Shipped.** Price history: no `price_history`; price written in two places (`admin.py` INSERT, `admin_books.py` UPDATE), needs one `set_book_price()` choke point | `price_history` |
| ~~3~~ | ~~55~~ | **Shipped.** Bulk price change: no selection UI, and `execute` per book instead of one statement | — |
| ~~3~~ | ~~66~~ | **Shipped.** Optimistic locking: `books` has no `version` column; edit is a blind `UPDATE ... WHERE id=?` | `books.version` |
| ~~3~~ | ~~68~~ | **Shipped.** Audit log chain: `admin_log` has no `prev_hash`/`row_hash`; single chokepoint `log_admin_action()` makes this a one-function change | `admin_log.prev_hash`, `row_hash` |
| ~~4~~ | ~~50~~ | **Shipped.** Helpful votes: `UNIQUE(review_id, user_id)` in the schema makes a double submit a no-op; toggle is idempotent | `review_votes` |
| ~~4~~ | ~~51~~ | **Shipped.** Review reports + moderation queue; the average AND the count now come from one filtered constant (decision 1) | `review_reports`, `reviews.hidden` |
| ~~4~~ | ~~52~~ | **Shipped.** Book requests grouped by normalised title with an upserted `request_count`; admin list sorted by demand | `book_requests` |
| ~~4~~ | ~~53~~ | **Shipped.** Wishlist sharing with a hashed, revocable token; a revoked link 404s like an unknown one | `wishlist_shares` |
| ~~4~~ | ~~59~~ | **Shipped.** Password-confirmed anonymisation; unique `.invalid` email placeholder resolves the UNIQUE conflict, orders preserved, sessions blocked | — |
| ~~5~~ | ~~61~~ | **Shipped.** Idempotent checkout: hashed single-use token minted with the reservation, claimed inside the order transaction; replay lands on the existing order | `checkout_tokens` |
| ~~5~~ | ~~62~~ | **Shipped.** Edit before packing: `Placed`-only gate, full re-price of the surviving lines, stock moved through `stock_log` | — |
| ~~5~~ | ~~63~~ | **Shipped.** Data-driven pricing rules with an admin CRUD/toggle page, per-book split, additive-but-capped stacking | `pricing_rules` |
| ~~5~~ | ~~64~~ | **Shipped.** Gift cards: hash-only codes, ledger-`SUM` balance, partial redemption, per-customer ownership — decision 3 (simulated capture) | `gift_cards`, `gift_card_ledger` |
| ~~5~~ | ~~65~~ | **Shipped.** Backorders: stock never negative, remainder queued and allocated oldest-first on restock — decision 4 | `backorders` |
| ~~6~~ | ~~69~~ | **Shipped.** Outbox: separate `notification_outbox` with attempts/backoff/park, written in the same transaction as the event it announces | `notification_outbox` |
| ~~6~~ | ~~70~~ | **Shipped.** Book of the month: `subscriptions` + `subscription_runs`, admin "run due", rides the normal order pipeline (decision 3) | `subscriptions`, `subscription_runs` |
| 7 | 71 | TOTP 2FA: no `hmac`/`struct` TOTP, no secret column, no replay store | `users.totp_secret`, replay table |
| 7 | 72 | API keys + scopes: no `api_keys`, `/api/` is unauthenticated, and `rate_limit()` is keyed by IP not key | `api_keys` |
| 7 | 73 | Permissions table: `role_required` at **27 call sites**, no `permissions`/`role_permissions` — largest refactor, do last | `permissions`, `role_permissions` |
| 8 | 74 | Growth report: no `LAG`/`SUM OVER`; CSV export already exists to hand-check the result | — |

**Req 76 (tests continuously)** is at 107 tests over the 90-odd routes. Each
batch above must add the tests for what it implements, and the batch is not
"Done" until those tests fail-then-pass. Batch 4 did exactly that for 51, 52, 53
and 59, and batch 5 repeated it for 61, 62, 63, 64 and 65 by temporarily
reverting each mechanism and observing the failure before restoring it. Req 77
(`stress.py`) is sequenced after 61 and 65, because those are what make
concurrent checkout safe — both are now in place.

**Nothing left is blocked on a product decision.** Decisions 3 and 4 are
answered and implemented in batch 5, so 69–74 are all pure engineering; 73
(permissions table) is the largest and goes last, and 74 is deliberately last
because it is read-only reporting with no schema change.

---

## Decisions (all four answered; kept as the record of *why*)

1. **Req 51 — does a hidden review's rating still count in the average?**
   **Answered: no.** A hidden review is a moderation decision that the content
   should not be shown to shoppers at all, so it counts to neither the average
   nor the count; leaving it in would silently let reported content keep
   influencing the score with no visible explanation. Implemented in batch 4 as
   the `AND r.hidden = 0` constants in `pageturner/reviews.py`.
2. **Req 59 — `users.email` is `NOT NULL UNIQUE`.** **Answered: placeholder.**
   `deleted+<id>@anonymised.invalid` preserves UNIQUE, needs no null-handling,
   and keeps login failing because `password_hash` is replaced and sessions are
   dropped. Implemented in batch 4.
3. **Reqs 64 / 70 imply payment capture**, but the app has **no payment
   gateway**. **Answered: simulated.** Buying a gift card debits the order the
   shop already charges and credits a ledger row; no external call is made, and
   `orders.gift_card_paise` records what the card covered. Implemented in batch
   5 for req 64; req 70 (book-of-the-month) must charge a subscription through
   the same existing order pipeline, never a new gateway.
4. **Req 65 backorder vs. `stock` invariant:** **Answered: `books.stock` never
   goes negative.** The remainder of an order is a `backorders` row, and
   `allocate_backorders()` fills the queue oldest-first when stock returns.
   Implemented in batch 5.

---

## Answers to the two QUESTIONS

**Q1 — Why copy the sale price into the order line instead of reading it from
the book later?** Because an order and its invoice are a record of what was
actually agreed at a moment in time. A sale window expires and the normal price
can be edited afterwards, so re-reading `books` would retroactively change
historical totals and make the invoice disagree with what the customer paid.
The snapshot (`order_items.price_paise`, which already exists here) makes the
money arithmetic reproducible and auditable, and is what lets the cart, the
order and the invoice agree permanently rather than momentarily.
Note the two are complementary: `effective_book_price()` decides *today's*
price at add-to-cart/checkout time, and the snapshot freezes it at order time.

**Q2 — Why is a UNIQUE pair better than checking in Python for helpful votes?**
A Python check is a time-of-check/time-of-use race: two concurrent requests can
both read "no vote exists" and both insert. `UNIQUE(user_id, review_id)` makes
the database the single atomic authority, so a double-click, a retry or two
workers cannot create two votes — the second `INSERT` simply fails and is
handled as "already voted". It also matches this project's own rule: *do not
rely on a Python check where the database can enforce the rule.*

---

## Working protocol (per batch)

1. Verify branch/HEAD/worktree clean; create a checkpoint tag.
2. Implement 5–6 related requirements.
3. Run: focused tests → full `pytest` → `compileall` → route sweep.
4. Commit only if all pass. If a check fails, fix the regression first.
5. Record results in this file.

**Non-negotiables:** no weakened tests, no destructive schema changes, no
orphaned or duplicate routes, no push without approval, no deploy without
approval, no float for money, no SQL built by joining user input, no trusting an
ID from the URL without an ownership check, no secret/token stored in a form
that is usable if the table is read.
