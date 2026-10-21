"""Small command-line helpers for managing PageTurner Books users."""
import argparse
from getpass import getpass
from werkzeug.security import generate_password_hash
from pageturner import create_app
from pageturner.db import get_db


def create_user(email, name, role):
    password = getpass("Password (at least 8 characters): ")
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    app = create_app()
    with app.app_context():
        connection = get_db()
        connection.execute("INSERT INTO users(name,email,password_hash,role) VALUES (?,?,?,?)", (name.strip(),email.strip().lower(),generate_password_hash(password),role))
        connection.commit()
    print(f"Created {role} account for {email}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage PageTurner Books users")
    subparsers = parser.add_subparsers(dest="command", required=True)
    user_parser = subparsers.add_parser("create-user", help="Create a staff or admin account")
    user_parser.add_argument("--name", required=True)
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--role", choices=["staff", "admin"], required=True)
    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args.email, args.name, args.role)
