"""Imports an existing PageTurner Books database into the integer-paise schema.

The source database is always opened read-only and is never modified or
deleted. Only the target database is rewritten.
"""
import argparse
import secrets
import shutil
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path

from pageturner import create_app
from pageturner.db import get_db


def to_paise(value):
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def has_table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def columns(connection, table):
    return {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def datetime_now():
    return datetime.now().isoformat(timespec="seconds")


def migrate(source_path, target_path):
    source_path = Path(source_path).resolve()
    target_path = Path(target_path).resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")
    if source_path == target_path:
        raise ValueError("Source and target must be different files. The source database is never overwritten.")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": secrets.token_hex(32),
        "DATABASE": str(target_path),
        "ADMIN_EMAIL": "",
        "ADMIN_PASSWORD": "",
    })

    with app.app_context():
        target = get_db()
        target.execute("PRAGMA foreign_keys = OFF")
        for table in ("returns", "notifications", "contact_messages", "api_requests", "admin_log", "user_sessions", "password_reset_tokens", "login_attempts", "coupon_uses", "order_status_history", "stock_log", "reservations", "cart_items", "wishlist", "reviews", "order_items", "orders", "addresses", "coupons", "book_tags", "book_categories", "tags", "books", "users"):
            target.execute(f'DELETE FROM "{table}"')
        try:
            if has_table(source, "users"):
                source_user_columns = columns(source, "users")
                for row in source.execute("SELECT * FROM users"):
                    role = row["role"] if "role" in source_user_columns and row["role"] in ("customer", "staff", "admin") else "customer"
                    target.execute("INSERT INTO users(id,name,email,password_hash,role) VALUES (?,?,?,?,?)", (row["id"],row["name"],row["email"],row["password_hash"],role))
            if has_table(source, "books"):
                source_book_columns = columns(source, "books")
                for row in source.execute("SELECT * FROM books"):
                    price = row["price_paise"] if "price_paise" in source_book_columns else to_paise(row["price"])
                    image = row["image"] or "" if "image" in source_book_columns else ""
                    image = image.replace("\\", "/")
                    if image.startswith("/static/"):
                        image = image[len("/static/"):]
                    if image and not image.startswith(("images/", "uploads/")):
                        image = "images/" + Path(image).name
                    target.execute("INSERT INTO books(id,title,author,category,price_paise,description,stock,color,image) VALUES (?,?,?,?,?,?,?,?,?)", (row["id"],row["title"],row["author"],row["category"],price,row["description"] or "",row["stock"],row["color"] or "#e8f1ef",image))
                    category_names = {value.strip() for value in row["category"].split(",") if value.strip()}
                    for category in category_names:
                        target.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (category,))
                        category_id = target.execute("SELECT id FROM categories WHERE name=? COLLATE NOCASE", (category,)).fetchone()["id"]
                        target.execute("INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)", (row["id"],category_id))
            if has_table(source, "addresses"):
                for row in source.execute("SELECT * FROM addresses"):
                    target.execute("INSERT INTO addresses(id,user_id,label,line1,city,pincode,is_default) VALUES (?,?,?,?,?,?,?)", tuple(row[key] for key in ("id","user_id","label","line1","city","pincode","is_default")))
            if has_table(source, "coupons"):
                source_coupon_columns = columns(source, "coupons")
                for row in source.execute("SELECT * FROM coupons"):
                    target.execute("INSERT OR IGNORE INTO coupons(id,code,percent,expiry_date,active,min_order_paise,max_discount_paise,usage_limit) VALUES (?,?,?,?,?,?,?,?)", (row["id"],row["code"],row["percent"],row["expiry_date"],row["active"],row["min_order_paise"] if "min_order_paise" in source_coupon_columns else 0,row["max_discount_paise"] if "max_discount_paise" in source_coupon_columns else None,row["usage_limit"] if "usage_limit" in source_coupon_columns else None))
            if has_table(source, "orders"):
                source_order_columns = columns(source, "orders")
                for row in source.execute("SELECT * FROM orders"):
                    total = row["total_paise"] if "total_paise" in source_order_columns else to_paise(row["total"])
                    discount = row["discount_paise"] if "discount_paise" in source_order_columns else to_paise(row["discount"] if "discount" in source_order_columns else 0)
                    status = row["status"] if "status" in source_order_columns and row["status"] in ("Placed","Packed","Shipped","Delivered","Cancelled") else "Placed"
                    date = row["date"] if "date" in source_order_columns else row["created_at"]
                    target.execute("INSERT INTO orders(id,user_id,name,phone,address,subtotal_paise,discount_paise,shipping_paise,tax_paise,total_paise,date,status,coupon_code,pincode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row["id"],row["user_id"] if "user_id" in source_order_columns else None,row["name"] if "name" in source_order_columns else row["customer_name"],row["phone"],row["address"],to_paise(row["subtotal"]) if "subtotal" in source_order_columns else total+discount,discount,to_paise(row["shipping"]) if "shipping" in source_order_columns else 0,to_paise(row["tax"]) if "tax" in source_order_columns else 0,total,date,status,row["coupon_code"] if "coupon_code" in source_order_columns else None,row["pincode"] if "pincode" in source_order_columns else ""))
                    target.execute("INSERT INTO order_status_history(order_id,old_status,new_status,note) VALUES (?,NULL,?,?)", (row["id"],status,"Imported from the previous project"))
            if has_table(source, "order_items"):
                source_item_columns = columns(source, "order_items")
                for row in source.execute("SELECT * FROM order_items"):
                    price = row["price_paise"] if "price_paise" in source_item_columns else to_paise(row["price"])
                    target.execute("INSERT INTO order_items(id,order_id,book_id,quantity,price_paise,returned_quantity) VALUES (?,?,?,?,?,?)", (row["id"],row["order_id"],row["book_id"],row["quantity"],price,row["returned_quantity"] if "returned_quantity" in source_item_columns else 0))
                    target.execute("INSERT INTO stock_log(book_id,change_amount,reason,order_id) VALUES (?,?,?,?)", (row["book_id"],-row["quantity"],"historical purchase",row["order_id"]))
            if has_table(source, "reviews"):
                source_review_columns = columns(source, "reviews")
                for row in source.execute("SELECT * FROM reviews"):
                    target.execute("INSERT OR IGNORE INTO reviews(id,book_id,user_id,name,rating,comment,created_at) VALUES (?,?,?,?,?,?,?)", (row["id"],row["book_id"],row["user_id"] if "user_id" in source_review_columns else None,row["name"],row["rating"],row["comment"],row["created_at"] if "created_at" in source_review_columns else datetime_now()))
            if has_table(source, "wishlist"):
                for row in source.execute("SELECT * FROM wishlist"):
                    target.execute("INSERT OR IGNORE INTO wishlist(id,user_id,book_id) VALUES (?,?,?)", (row["id"],row["user_id"],row["book_id"]))
            # Copy all old coupon and order IDs before re-enabling foreign-key checks.
            target.execute("UPDATE schema_version SET version=1")
            target.commit()
            violations = target.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Foreign-key checks failed: {len(violations)} issue(s). The target import was not accepted.")
        except Exception:
            target.rollback()
            raise
    source.close()
    # Preserve the old book covers by copying assets, never moving or deleting them.
    old_static = source_path.parent / "static"
    new_static = Path(__file__).resolve().parent / "static"
    for folder_name in ("images", "uploads"):
        old_folder = old_static / folder_name
        new_folder = new_static / folder_name
        if old_folder.is_dir():
            new_folder.mkdir(parents=True, exist_ok=True)
            for old_file in old_folder.iterdir():
                if old_file.is_file() and not (new_folder / old_file.name).exists():
                    shutil.copy2(old_file, new_folder / old_file.name)
    return target_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Safely import a legacy PageTurner database into the new schema.")
    parser.add_argument("--source", required=True, help="Existing database.db file; it will not be modified")
    parser.add_argument("--target", default="instance/database.db", help="Destination database in this project")
    args = parser.parse_args()
    print(f"Imported database to: {migrate(args.source, args.target)}")
    print("The source database was not changed. Check the imported row counts before using the new application.")
