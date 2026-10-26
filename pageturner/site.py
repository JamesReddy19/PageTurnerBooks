"""Public contact, notification and error pages."""
from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from .db import get_db
from .helpers import current_user


def register_site_routes(app):
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/contact")
    def contact():
        return render_template("contact.html")

    @app.post("/contact")
    def send_contact():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not name or "@" not in email or not subject or not message or len(message) > 5000:
            flash("Please complete every field with valid details.", "error")
        else:
            get_db().execute("INSERT INTO contact_messages(name,email,subject,message) VALUES (?,?,?,?)", (name, email, subject, message))
            get_db().commit()
            flash("Your message was sent. Thank you.", "success")
            return redirect(url_for("contact"))
        return render_template("contact.html"), 400

    @app.get("/notifications")
    def notifications():
        if not current_user():
            return redirect(url_for("auth.login"))
        rows = get_db().execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC", (session["user_id"],)).fetchall()
        return render_template("notifications.html", notifications=rows)

    @app.post("/notifications/read/<int:notification_id>")
    def mark_notification_read(notification_id):
        if not current_user():
            abort(403)
        get_db().execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", (notification_id, session["user_id"]))
        get_db().commit()
        return redirect(url_for("notifications"))

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=getattr(error, "description", "Bad request")), 400

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="You do not have permission to view this page."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="The page you requested could not be found."), 404

    @app.errorhandler(429)
    def too_many(_error):
        return jsonify({"error": "Too many requests. Please try again in a minute."}), 429

    @app.errorhandler(500)
    def server_error(_error):
        get_db().rollback()
        return render_template("error.html", code=500, message="Something went wrong. Please try again."), 500
