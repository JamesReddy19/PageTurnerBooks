"""Initial sample books and optional environment administrator."""
from flask import current_app
from werkzeug.security import generate_password_hash
from .db import get_db, seed_categories

def seed_books_if_empty():
    connection = get_db()
    count = connection.execute("SELECT COUNT(*) AS count FROM books").fetchone()["count"]
    if count:
        return
    books = [
        ("Steve Jobs", "Walter Isaacson", "Biography", 59900, "The life and work of Apple co-founder Steve Jobs.", 10, "#e7e9f7"),
        ("Wings of Fire", "A. P. J. Abdul Kalam", "Biography", 39900, "An inspiring autobiography about learning and perseverance.", 10, "#f7e7cf"),
        ("The Pearl", "John Steinbeck", "Fiction", 29900, "A classic story about hope, wealth and human nature.", 10, "#e3f1e7"),
        ("The Blue Umbrella", "Ruskin Bond", "Fiction", 19900, "A gentle story set in the hills of India.", 10, "#dfeef9"),
        ("Wild History", "Mahesh Rangarajan", "History", 54900, "A look at people, landscapes and the past.", 10, "#f4e6dd"),
        ("Hitler", "Ian Kershaw", "History", 69900, "A historical biography of Adolf Hitler.", 10, "#e8e8e8"),
        ("Cosmos", "Carl Sagan", "Science", 49900, "A journey through the universe and scientific discovery.", 10, "#e5e8fa"),
        ("Chaos", "James Gleick", "Science", 59900, "An introduction to chaos theory and complex systems.", 10, "#e1f2f1"),
        ("Atomic Habits", "James Clear", "Self-Help", 49900, "Small habits that can lead to meaningful change.", 10, "#f7ead7"),
        ("Blink", "Malcolm Gladwell", "Self-Help", 44900, "How rapid judgments shape our decisions.", 10, "#f1e5f2"),
        ("Zero to One", "Peter Thiel", "Technology", 39900, "Notes on startups and creating something new.", 10, "#e4eef0"),
        ("Life 3.0", "Max Tegmark", "Technology", 64900, "Exploring artificial intelligence and the future of life.", 10, "#e9e7fa"),
    ]
    connection.executemany("INSERT INTO books(title,author,category,price_paise,description,stock,color) VALUES (?,?,?,?,?,?,?)", books)
    connection.commit()
    seed_categories(connection)


def create_environment_admin():
    email = str(current_app.config.get("ADMIN_EMAIL", "")).strip().lower()
    password = current_app.config.get("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    connection = get_db()
    found = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    password_hash = generate_password_hash(password)
    if found:
        connection.execute("UPDATE users SET role='admin', password_hash=? WHERE id=?", (password_hash, found["id"]))
    else:
        connection.execute("INSERT INTO users(name,email,password_hash,role) VALUES (?,?,?,'admin')", ("Store Administrator", email, password_hash))
    connection.commit()
