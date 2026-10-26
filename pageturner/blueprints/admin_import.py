"""Validate and import book CSV rows in a single transaction."""
import csv
import io
import sqlite3
from flask import flash, redirect, request, url_for
from ..db import get_db
from ..helpers import log_admin_action, rupees_to_paise, permission_required
from ..pricing import set_book_price
from .admin import admin_bp

@admin_bp.post("/import/books.csv")
@permission_required("catalog.manage")
def import_books_csv():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename.lower().endswith(".csv"):
        flash("Choose a CSV file.", "error")
        return redirect(url_for("admin.manage_books"))
    try:
        text = uploaded.stream.read(2 * 1024 * 1024 + 1).decode("utf-8-sig")
        if len(text.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("CSV file is too large.")
        reader = csv.DictReader(io.StringIO(text))
        needed = {"title","author","category","price","stock"}
        if not reader.fieldnames or not needed.issubset(set(reader.fieldnames)):
            raise ValueError("CSV needs title, author, category, price and stock columns.")
        valid = []
        errors = []
        for line, row in enumerate(reader, start=2):
            try:
                title, author, category = row["title"].strip(), row["author"].strip(), row["category"].strip()
                price = rupees_to_paise(row["price"])
                stock = int(row["stock"])
                if not title or not author or not category or stock < 0:
                    raise ValueError("missing required field or invalid stock")
                valid.append((title,author,category,price,row.get("description", ""),stock,"#e8f1ef"))
            except (ValueError, TypeError) as error:
                errors.append(f"Row {line}: {error}")
        connection = get_db()
        connection.execute("BEGIN IMMEDIATE")
        inserted = 0
        for row in valid:
            before = connection.total_changes
            connection.execute("INSERT OR IGNORE INTO books(title,author,category,price_paise,description,stock,color) VALUES (?,?,?,?,?,?,?)", row)
            if connection.total_changes > before:
                inserted += 1
                book_id = connection.execute("SELECT id FROM books WHERE title=? AND author=?", (row[0],row[1])).fetchone()["id"]
                # Price history baseline for the new row (req 54 choke point).
                set_book_price(
                    connection, book_id, row[3], None, reason="csv import"
                )
                connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (row[2],))
                cat_id = connection.execute("SELECT id FROM categories WHERE name=?", (row[2],)).fetchone()["id"]
                connection.execute("INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)", (book_id,cat_id))
        log_admin_action("books_csv_imported", f"inserted={inserted}, invalid={len(errors)}")
        connection.commit()
        flash(f"Imported {inserted} new books. {len(errors)} row(s) had validation errors. Duplicate titles/authors were skipped.", "success")
        for error in errors[:5]:
            flash(error, "error")
    except (UnicodeDecodeError, ValueError, csv.Error) as error:
        get_db().rollback()
        flash(f"CSV import failed: {error}", "error")
    return redirect(url_for("admin.manage_books"))
