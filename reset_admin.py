
from getpass import getpass

from werkzeug.security import generate_password_hash
from pageturner import create_app
from pageturner.db import get_db


app = create_app()

with app.app_context():
    connection = get_db()

    admins = connection.execute(
        "SELECT id, name, email FROM users WHERE role = 'admin'"
    ).fetchall()

    if not admins:
        print("No admin account found.")
    else:
        print("Admin accounts:")
        for admin in admins:
            print(f"{admin['name']} - {admin['email']}")

        email = input("Enter the admin email to reset: ").strip()
        admin = connection.execute(
            "SELECT id FROM users "
            "WHERE email = ? AND role = 'admin'",
            (email,),
        ).fetchone()

        if not admin:
            print("Admin email not found.")
        else:
            password = getpass("Enter your new password: ")
            confirm = getpass("Confirm your new password: ")

            if len(password) < 8:
                print("Password must be at least 8 characters.")
            elif password != confirm:
                print("Passwords do not match.")
            else:
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(password), admin["id"]),
                )
                connection.commit()
                print("Admin password reset successfully.")
