"""PageTurner Books entry point.

In production the app is served by gunicorn through ``wsgi:app``. Running this
file directly starts a local development server that honours the PORT variable
so it behaves like the hosting environment.
"""
import os

from pageturner import create_app

app = create_app()

if __name__ == "__main__":
    # The hosting platform supplies PORT; fall back to 5000 for local work.
    port = int(os.environ.get("PORT", "5000"))
    debug = (
        os.environ.get("FLASK_DEBUG", "0") == "1"
        or os.environ.get("APP_ENV", "").lower() == "development"
    )

    # Bind to every interface so the platform's proxy can reach the container.
    app.run(host="0.0.0.0", port=port, debug=debug)
