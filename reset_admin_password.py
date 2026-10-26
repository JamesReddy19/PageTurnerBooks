
from getpass import getpass
from werkzeug.security import generate_password_hash, check_password_hash

from pageturner import create_app
from pageturner.db import get_db

ADMIN_EMAIL = "admin45@mail.com"

app = create_app()

with app.app_context():
    db = get_db()

    user = db.execute(
        "SELECT id, email, role FROM users WHERE email = ? AND role = ?",
        (ADMIN_EMAIL, "admin"),
    ).fetchone()

    if not user:
        print("Admin account not found. No changes made.")
    else:
        password = getpass("Enter a NEW password (minimum 8 characters): ")
        confirm = getpass("Confirm the new password: ")

        if len(password) < 8:
            print("Password must be at least 8 characters. No changes made.")
        elif password != confirm:
            print("Passwords do not match. No changes made.")
        else:
            new_hash = generate_password_hash(password)

            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, user["id"]),
            )
            db.commit()

            saved_hash = db.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()["password_hash"]

            if check_password_hash(saved_hash, password):
                print("Password reset and verification successful.")
                print("Login email:", user["email"])
            else:
                print("Verification failed. Please do not try logging in yet.")
