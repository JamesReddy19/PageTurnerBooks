# Deploying PageTurner Books

The app is a Flask + SQLite application served by **gunicorn** through
`wsgi:app`. `app.py` also binds `$PORT` (default 5000) for local runs.

## Required environment variables

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | **Required in production.** Long random value (e.g. `python -c "import secrets;print(secrets.token_hex(32))"`). The app refuses to start without it outside development. |
| `PAGETURNER_DATABASE` | SQLite file path. Use persistent storage, e.g. `/data/instance/database.db`. |
| `PAGETURNER_UPLOADS` | Cover-image folder. Defaults to `<project>/static/uploads`; point it at a mounted disk (e.g. `/app/static/uploads`). |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Creates the administrator at startup and re-applies that password on every boot (the env var is the source of truth). Keep the password only in the platform's secret store. |
| `APP_ENV` | `production` (default) or `development`. |
| `COOKIE_SECURE` | `1` when served over HTTPS (recommended). |
| `PORT` | Supplied by the host; gunicorn binds it. |

## Persistent storage

SQLite and uploaded covers must live outside the container's ephemeral layer.
Attach disks at:

* `/data/` → `PAGETURNER_DATABASE=/data/instance/database.db`
* `/app/static/uploads/` → `PAGETURNER_UPLOADS=/app/static/uploads`

The upload folder stays inside `static/` so existing `url_for('static', ...)`
image URLs keep working. If no disk is attached, both paths fall back to the
container filesystem and **data is lost on redeploy**.

## Worker configuration (SQLite)

`gunicorn.conf.py` uses **1 worker process with 8 threads** (`gthread`).
SQLite permits a single writer, so multiple worker *processes* sharing one file
cause `database is locked` errors. Each request still gets its own connection
from `flask.g`. Raise `GUNICORN_WORKERS` only after moving to Postgres.

## Startup safety

* `init_db()` only runs `CREATE TABLE/INDEX ... IF NOT EXISTS` and additive
  `ALTER TABLE ... ADD COLUMN`; no table is ever dropped.
* Sample books are seeded **only** when the `books` table is empty, so a live
  catalogue is never overwritten.
* Migrations are **never** run automatically on boot. Import an existing
  database deliberately, once, before first start:

  ```bash
  python migrate.py --source /old/database.db --target /data/instance/database.db
  ```

## Importing a legacy database

```bash
python migrate.py --source /path/to/old/database.db --target instance/database.db
```

The source file is opened **read-only** and is never modified. The target is
rewritten inside a transaction, so a failure rolls back. Verified on a synthetic
legacy database: floats become integer paise (`19.99` → `1999`), rows and ids
are preserved, `PRAGMA foreign_key_check` is clean, and the old cover images are
copied (never moved) into `static/images/`.

## Worker safety note

Keep `GUNICORN_WORKERS=1`. Two worker processes writing the same SQLite file is
the documented cause of `database is locked`; the 8 `gthread` threads cover the
concurrency this app needs.

## Local development

```bash
pip install -r requirements.txt
export APP_ENV=development        # random per-process SECRET_KEY
python app.py                     # http://127.0.0.1:5000/health
```

```bash
python -m pytest tests -q         # 32 tests
```
