"""Production WSGI entry point.

Run with a real WSGI server, for example:

    gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app

`app.py` stays the development entry point (it enables the debug reloader).
"""

from pageturner import create_app

app = create_app()
