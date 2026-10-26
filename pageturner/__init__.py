
"""Application factory for PageTurner Books."""

import os
import secrets

from flask import Flask, abort, request, session

from .config import Config
from .db import close_db, get_db, init_db
from .helpers import current_user, money, effective_book_price, sale_ends_in_seconds
from .seed import seed_books_if_empty, create_environment_admin


def create_app(test_config=None):
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        instance_relative_config=True,
    )

    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    # A predictable SECRET_KEY would let anyone forge signed session cookies.
    # Tests and local development get a random per-process key instead, while
    # production must supply one through the environment.
    if not app.config.get("SECRET_KEY"):
        is_development = bool(
            app.config.get("TESTING")
            or app.debug
            or str(app.config.get("ENVIRONMENT", "")).lower() == "development"
        )

        if not is_development:
            raise RuntimeError(
                "SECRET_KEY is not set. Provide a long random SECRET_KEY "
                "environment variable before running in production."
            )

        app.config["SECRET_KEY"] = secrets.token_hex(32)
        app.logger.warning(
            "SECRET_KEY was not provided; using a temporary development key. "
            "Sessions will be invalidated on restart."
        )

    # Make sure the instance directory and upload folder exist. Both may live
    # on a mounted persistent disk, so create them defensively.
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Close database connections after each request.
    app.teardown_appcontext(close_db)

    # Initialize the database and prepare the initial data.
    with app.app_context():
        init_db()
        seed_books_if_empty()
        create_environment_admin()

    # Create or retrieve the CSRF token.
    def csrf_value():
        token = session.get("csrf_token")

        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token

        return token

    # Make common values available to all templates.
    @app.context_processor
    def common_template_values():
        user = current_user()

        cart = session.get("cart", {})
        cart_count = sum(int(quantity) for quantity in cart.values())
        wishlist_count = 0

        if user:
            connection = get_db()

            database_cart_count = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS count "
                "FROM cart_items WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["count"]

            cart_count += database_cart_count

            wishlist_count = connection.execute(
                "SELECT COUNT(*) AS count "
                "FROM wishlist WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["count"]

            unread = connection.execute(
                "SELECT COUNT(*) AS count "
                "FROM notifications "
                "WHERE user_id = ? AND is_read = 0",
                (user["id"],),
            ).fetchone()["count"]
        else:
            unread = 0

        return {
            "current_user": user,
            "cart_count": cart_count,
            "wishlist_count": wishlist_count,
            "unread_notifications": unread,
            "money": money,
            "effective_price": effective_book_price,
            "sale_ends_in_seconds": sale_ends_in_seconds,
            "csrf_token": csrf_value(),
        }

    # Import and register the application blueprints.

    from .blueprints.api import api_bp
    from .blueprints.auth import auth_bp
    from .blueprints.shop import shop_bp
    from .blueprints.admin import admin_bp
    from .site import register_site_routes
    from .blueprints.promise_checkout import promise_checkout_bp
    from .legacy import register_legacy_routes

    app.register_blueprint(auth_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(promise_checkout_bp)
    app.register_blueprint(api_bp)

    # Register public pages, notifications, and error handlers.
    register_site_routes(app)

    # Keep the original project URLs working. These endpoints only redirect
    # (307 for POST) to the canonical routes and do not duplicate them.
    register_legacy_routes(app)

    # Validate CSRF tokens on state-changing requests.
    @app.before_request
    def check_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None

        submitted_token = request.form.get("csrf_token")

        if not submitted_token:
            submitted_token = request.headers.get("X-CSRF-Token")

        saved_token = session.get("csrf_token")

        if (
            not submitted_token
            or not saved_token
            or not secrets.compare_digest(submitted_token, saved_token)
        ):
            abort(400, description="Invalid or missing CSRF token.")

        return None

    return app
