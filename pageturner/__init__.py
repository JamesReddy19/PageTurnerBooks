"""Application factory for PageTurner Books."""
import hashlib
import os
import secrets
from pathlib import Path
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from .config import Config
from .db import close_db, get_db, init_db
from .helpers import current_user, money
from .seed import seed_books_if_empty, create_environment_admin


def create_app(test_config=None):
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
        instance_relative_config=True,
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash(app.config["ADMIN_PASSWORD"]) if app.config.get("ADMIN_PASSWORD") else ""
    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret-key"
        else:
            raise RuntimeError("Set the SECRET_KEY environment variable before starting PageTurner Books.")
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
        seed_books_if_empty()
        create_environment_admin()

    @app.before_request
    def check_session_and_csrf():
        # A revoked login token stops working on the next request.
        token = session.get("session_token")
        if token and session.get("user_id"):
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            saved = get_db().execute(
                "SELECT id, revoked_at FROM user_sessions WHERE token_hash = ? AND user_id = ?",
                (token_hash, session["user_id"]),
            ).fetchone()
            if saved is None or saved["revoked_at"]:
                session.clear()
                flash("This session has been signed out. Please log in again.", "error")
                if request.method == "POST":
                    abort(403)
                return redirect(url_for("auth.login"))
            get_db().execute("UPDATE user_sessions SET last_seen = CURRENT_TIMESTAMP WHERE id = ?", (saved["id"],))
            get_db().commit()

        if request.method == "POST":
            expected = session.get("csrf_token", "")
            supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                abort(400, description="The form expired or its security token is missing. Refresh the page and try again.")

    @app.context_processor
    def common_template_values():
        user = current_user()
        cart = session.get("cart", {})
        cart_count = sum(int(quantity) for quantity in cart.values())
        if user:
            cart_count = get_db().execute(
                "SELECT COALESCE(SUM(quantity), 0) AS count FROM cart_items WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["count"] + cart_count
            unread = get_db().execute("SELECT COUNT(*) AS count FROM notifications WHERE user_id = ? AND is_read = 0", (user["id"],)).fetchone()["count"]
        else:
            unread = 0
        return {"current_user": user, "cart_count": cart_count, "unread_notifications": unread, "money": money, "csrf_token": csrf_value()}

    from .site import register_site_routes
    register_site_routes(app)

    from .blueprints.auth import auth_bp
    from .blueprints.shop import shop_bp
    from .blueprints.admin import admin_bp
    from .blueprints.api import api_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    from .legacy import register_legacy_routes
    register_legacy_routes(app)
    return app


def csrf_value():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


