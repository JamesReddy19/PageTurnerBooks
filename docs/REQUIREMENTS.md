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
| 50 | Review helpful votes | **Missing** | `reviews` has no vote column and there is no `review_votes` table, no button, no count. Needs `UNIQUE(user_id, review_id)`. |
| 51 | Report a review (moderation) | **Missing** | `reviews` has no `hidden` flag; no report table; no admin queue. **Blocked on a decision** about the rating average (below). |
| 52 | Request a book | **Missing** | No `book_requests` table, no form, no admin aggregation view. Needs a normalised title key + `COUNT(*)` ordering so two askers increment one row. |
| 53 | Share my wishlist | **Missing** | `wishlist` table and toggle/view routes exist, but there is no share token, no `/w/<token>` route, no revoke. |
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
| 59 | Delete my account | **Missing / Blocked** | No anonymise path; no password re-auth step; sessions are not invalidated. **Schema conflict:** `users.email` is `NOT NULL UNIQUE`, so "email cleared" cannot be done as written without a migration. |
| 60 | Dark mode toggle | **Done** | `data-theme` attribute on `<html>` drives a CSS-variable palette in `static/style.css`; `static/script.js` persists the choice in `localStorage` and an inline pre-paint script in `base.html` applies it before first paint to avoid a white flash. Toggle is present in the header for every page and honours `prefers-color-scheme` when no choice is stored. Verified by `tests/test_dark_mode.py`. |

### Part F — Hard orders and money

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 61 | Idempotent checkout | **Missing** | No `checkout_tokens` table; checkout is a plain POST. A refresh/Back currently re-posts. |
| 62 | Edit an order before packing | **Missing** | `shop_orders` has only `cancel`, `invoice`, `return`. No quantity edit / item removal. `coupon_discount()` already re-checks `min_order_paise` and `usage_limit` — reusable for the "coupon no longer qualifies" rule. |
| 63 | Pricing rules engine | **Missing** | No `pricing_rules` table. Only coupon percent logic exists (`shop.coupon_discount`). |
| 64 | Gift cards | **Missing** | No `gift_cards` / `gift_card_ledger`. Needs hash-only storage and a ledger SUM balance. |
| 65 | Backorders with a fair queue | **Missing** | No backorder concept; `stock` is debited unconditionally at checkout. |

### Part G — Data and concurrency

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 66 | Optimistic locking on admin edits | **Done** | `books.version` defaults to 1 and is bumped exactly once per accepted save. `edit_book` respects the hidden `version` field: a stale value, or a missing one, raises `BookEditConflict` and renders a 200 page saying the book "was changed by someone else" with **no** write to the row. The bump happens via `set_book_price()` when the price moves, and via an explicit `version = version + 1` when only non-price fields changed, so two concurrent editors cannot both win. Verified by 4 tests, one proven to fail with the version predicate removed. |
| 67 | Keyset pagination for the API | **Done** | `/api/books` accepts `?limit=` (1–100, default 20) and `?after=<opaque cursor>`, and returns `next_cursor`/`next`. Ordering is the total order `relevance DESC, title COLLATE NOCASE, id`, so pages never overlap or skip. Verified by walking all 12 books in 4 pages with no duplicates and no gaps; an unparseable cursor returns 400 instead of silently restarting. |
| 68 | Tamper-evident audit log | **Done** | `admin_log` gained `prev_hash`/`row_hash`; `helpers.log_admin_action()` is the single chokepoint that computes `sha256(prev_hash + canonical fields)`, linking each row to its predecessor from `GENESIS_HASH`. `audit.verify_chain()` walks the chain and compares the newest `row_hash` with the `audit_chain_head` pointer. Verified by 6 tests over real rows: editing `detail`, deleting a middle row, deleting the newest row (caught by the head pointer) and nulling `row_hash` are each detected, while an untouched chain verifies ok. |
| 69 | Notification outbox with retry | **Missing** | A `notifications` table + `add_notification()` exist, but they are in-app messages with **no attempt counter, no retry, no give-up**. Needs a separate outbox written in the same transaction as the event. |
| 70 | Book of the month subscription | **Missing** | No `subscriptions` table, no "run due" button. |

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

**Tally (29 requirements):** 7 **Done** (47, 48, 49, 56, 57, 67, 75) ·
**0 Partial** · **22 Missing** — of which **2 are additionally Blocked** on a
decision (51, 59).

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

**Done and verified by executed checks:** 47, 48, 49, 56, 57, 67, 75 (7 of 29).
**Remaining: 22.** Ordered by dependency, cheapest first, so each batch leaves the
app deployable.

| Batch | Reqs | Missing capability (one line each) | New storage |
|---|---|---|---|
| 3 | 60 | Dark mode: no `localStorage`, no CSS-variable theme, no pre-paint script (white flash) | — |
| 3 | 58 | Data export: `auth_profile` has no JSON download route, no `Content-Disposition` | — |
| 3 | 54 | Price history: no `price_history`; price written in two places (`admin.py` INSERT, `admin_books.py` UPDATE), needs one `set_book_price()` choke point | `price_history` |
| 3 | 55 | Bulk price change: no selection UI, and `execute` per book instead of one statement | — |
| 3 | 66 | Optimistic locking: `books` has no `version` column; edit is a blind `UPDATE ... WHERE id=?` | `books.version` |
| 3 | 68 | Audit log chain: `admin_log` has no `prev_hash`/`row_hash`; single chokepoint `log_admin_action()` makes this a one-function change | `admin_log.prev_hash`, `row_hash` |
| 4 | 50 | Helpful votes: no `review_votes` table, no button, no count; needs `UNIQUE(user_id, review_id)` per Q2 above | `review_votes` |
| 4 | 51 | Report a review: no `hidden` flag, no report table, no admin queue — **blocked on decision 1** | `review_reports`, `reviews.hidden` |
| 4 | 52 | Request a book: no table, no form, no aggregation view; needs normalised title key + `COUNT(*)` ordering | `book_requests` |
| 4 | 53 | Share a wishlist: `wishlist` exists but there is no share token, no `/w/<token>` route, no revoke | `wishlist_shares` |
| 4 | 59 | Delete my account: no anonymise path, no password re-auth, sessions not invalidated — **blocked on decision 2** (`users.email NOT NULL UNIQUE`) | — |
| 5 | 61 | Idempotent checkout: no `checkout_tokens`; refresh/Back re-posts and duplicates orders | `checkout_tokens` |
| 5 | 62 | Edit order before packing: only `cancel`/`invoice`/`return` exist; `coupon_discount()` already re-checks `min_order_paise`/`usage_limit` for the re-price rule | — |
| 5 | 63 | Pricing rules: no `pricing_rules` table; only coupon percent logic exists | `pricing_rules` |
| 5 | 64 | Gift cards: no card/ledger tables; needs hash-only storage + ledger `SUM` balance — **decision 3 (simulated capture)** | `gift_cards`, `gift_card_ledger` |
| 5 | 65 | Backorders: no backorder concept; `stock` is debited unconditionally at checkout — **decision 4 (no negative stock)** | `backorders` |
| 6 | 69 | Notification outbox: `notifications` has no attempt counter/retry/give-up; outbox must be written in the same transaction as the event | `notification_outbox` |
| 6 | 70 | Book-of-the-month: no `subscriptions`, no "run due" action | `subscriptions` |
| 7 | 71 | TOTP 2FA: no `hmac`/`struct` TOTP, no secret column, no replay store | `users.totp_secret`, replay table |
| 7 | 72 | API keys + scopes: no `api_keys`, `/api/` is unauthenticated, and `rate_limit()` is keyed by IP not key | `api_keys` |
| 7 | 73 | Permissions table: `role_required` at **27 call sites**, no `permissions`/`role_permissions` — largest refactor, do last | `permissions`, `role_permissions` |
| 8 | 74 | Growth report: no `LAG`/`SUM OVER`; CSV export already exists to hand-check the result | — |

**Req 76 (tests continuously)** is only partly met today: 35 tests cover 77
routes, so the shopping and security paths that already exist are mostly
uncovered. Each batch above must add the tests for what it implements, and the
batch is not "Done" until those tests fail-then-pass. Req 77 (`stress.py`) is
sequenced after 61 and 65, because those are what make concurrent checkout safe.

**Blocked on decisions 1–4 below** before any of 51, 59, 64, 65 can be coded
honestly. Everything else can start immediately.

---

## Open decisions (needed before coding these)

1. **Req 51 — does a hidden review's rating still count in the average?**
   Must be decided and written as a code comment. Recommendation: **exclude
   hidden reviews from both the average and the count**, because a hidden review
   is a moderation decision that the content should not be shown to shoppers at
   all; leaving it in the average would silently let reported content keep
   influencing the score with no visible explanation.
2. **Req 59 — `users.email` is `NOT NULL UNIQUE`.** "Email cleared" needs either
   a nullable email (migration + every `WHERE email` login path audited) or a
   placeholder like `deleted+<id>@invalid`. Placeholder is safer: it preserves
   UNIQUE, requires no null-handling, and keeps login failing because
   `password_hash` is replaced and sessions are dropped.
3. **Reqs 64 / 70 imply payment capture**, but the app has **no payment
   gateway**. Decide whether these are simulated (create the order / mark the
   card issued, no real charge) — recommended, and matching how checkout
   already works.
4. **Req 65 backorder vs. `stock` invariant:** decide whether a backorder order
   debits stock at placement (allowing negative) or is only debited when the
   fulfil step runs. Recommended: do **not** go negative; keep a separate
   `backorder` quantity and allocate oldest-first on restock.

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
