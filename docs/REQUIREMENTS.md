# Requirements Tracker — Requirements 47–75

Status legend: `TODO` · `IN PROGRESS` · `DONE` · `BLOCKED` · `UNKNOWN`

**Verification legend**

- `unit` — covered by a new/existing test in `tests/`
- `sweep` — `tests/test_route_sweep.py` (no 5xx, no duplicate rules)
- `manual` — exercised by hand against a temp DB, result recorded here
- `sql` — row counts / integrity query checked

---

## Status of the requirement list itself

The text of Requirements **47–75 has not been provided yet.** It is not present
in this repository: `README.md` and `START_HERE.txt` contain numbered *chapters*,
not numbered requirements. Requirements **1–46** are also unavailable.

Per instructions, their contents will **not** be invented. This file holds the
process and the guardrails; the requirement rows are filled in from the real
list when it is supplied.

| Block | Count | Received |
|---|---|---|
| 1–46 (prerequisite context) | 46 | No — not supplied |
| 47–75 (this batch) | 29 | **No — not supplied** |

---

## Baseline (Batch 1, complete)

| Item | Status | Verification |
|---|---|---|
| Migrator IndentationError repaired, source opened read-only | DONE | manual — legacy DB migrated, 0 FK violations, source unchanged |
| `api_bp` registered, `/api/books` reachable | DONE | unit + sweep |
| Legacy redirect routes registered, no duplicate paths | DONE | sweep — `test_no_duplicate_url_rules` |
| Gunicorn entry point, `$PORT`, 1 worker for SQLite | DONE | manual — boot verified, `/health` 200 |
| Production refuses to start without `SECRET_KEY` | DONE | manual |
| Persistent DB + upload storage configured | DONE | manual — volumes `/data/`, `/app/static/uploads/` |
| Anonymous-route regression sweep in permanent suite | DONE | proven to fail on an injected 5xx |

Baseline test count: **29 passed** (26 original + 3 sweep guards).

---

## Requirements 47–75

_Pending receipt of the list. Each row will record: requirement text, what
already exists, the gap, status, and verification method._

| # | Requirement (verbatim) | Existing code to reuse | Gap | Status | Verified by |
|---|---|---|---|---|---|
| 47 | _awaiting text_ | — | — | UNKNOWN | — |
| … | | | | | |
| 75 | _awaiting text_ | — | — | UNKNOWN | — |

---

## Known context from the audit (likely overlap with 47–75)

These are verified facts about the current code. They are recorded here because
they will probably intersect the incoming requirements, and because building on
what exists is cheaper than rebuilding.

**Implemented but unreachable (highest-value gap):**

- `admin_bundles`, `admin_coupons`, `admin_import`, `admin_orders`,
  `admin_reports`, `admin_returns`, `admin_sales` — modules exist but are imported
  by nothing, so their routes never register.
- `shop_cart`, `shop_checkout`, `shop_orders`, `auth_addresses` — exist, but the
  logic is duplicated inside the monolithic `shop.py` / `auth.py`.
- Templates with no backing route: `manage_bundles.html`, `manage_sales.html`,
  `returns.html`, `coupons.html`, `admin_report.html` (and others to be audited).

**Schema exists, no routes:** `bundles`, `bundle_items`, `cart_bundles`,
`order_bundles`, `order_bundle_items`, `points_ledger` are created in `db.py`,
but `shop.py` / `site.py` / `api.py` never reference them.

**Missing routes:** `/books` (catalog listing) returns 404; `/api/health` 404.

**Test coverage gaps:** 0 tests for coupons, bundles, sales, returns.
77 routes total.

**Dead files:** `pageturner/blueprints/admin.py.backup`, `static/style.css.backup`.

**Open question:** both `schema.sql` (top level) and the inline schema in `db.py`
exist — which is authoritative must be confirmed before touching the schema.

---

## Working protocol (per batch)

1. Verify branch/HEAD/worktree clean; create checkpoint tag.
2. Implement 5–6 related requirements.
3. Run: focused tests → full `pytest` → `compileall` → route sweep.
4. Commit only if all pass. If a check fails, fix the regression first.
5. Record results in this file.

**Non-negotiables:** no weakened tests, no destructive schema changes, no
orphaned or duplicate routes, no push without approval, no deploy without approval.
