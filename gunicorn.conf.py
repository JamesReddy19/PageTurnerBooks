"""Gunicorn settings tuned for this single-file SQLite application.

SQLite allows only one writer at a time. Several *worker processes* hitting the
same database file is the classic cause of "database is locked" errors under
load, so this configuration deliberately uses ONE worker process that serves
requests with several threads. Each request still gets its own sqlite3
connection from ``flask.g``, so connections are never shared between threads.

If you ever move to Postgres, raise ``GUNICORN_WORKERS`` (e.g. 4).
"""
import os

# Honour the port supplied by the hosting platform; 5000 locally.
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"

timeout = 60
graceful_timeout = 30
keepalive = 5

# Log to stdout/stderr so the platform can capture them.
accesslog = "-"
errorlog = "-"
loglevel = "info"
