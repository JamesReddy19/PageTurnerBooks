
from pageturner import create_app
from pageturner.db import get_db

app = create_app()

with app.app_context():
    db = get_db()

    print("DATABASE:", app.config["DATABASE"])

    admins = db.execute(
        "SELECT id, name, email, role FROM users WHERE role = ?",
        ("admin",),
    ).fetchall()

    print("ADMIN ACCOUNTS:")
    for admin in admins:
        print(dict(admin))

    if not admins:
        print("No admin accounts found.")
