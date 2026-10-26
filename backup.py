"""Create a consistent SQLite backup and keep the five newest backups."""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from pageturner.config import BASE_DIR


def create_backup(database_path=None, backup_folder=None):
    database_path = Path(database_path or BASE_DIR / "instance" / "database.db")
    backup_folder = Path(backup_folder or BASE_DIR / "backups")
    backup_folder.mkdir(parents=True, exist_ok=True)
    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")
    name = f"pageturner-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    destination = backup_folder / name
    with sqlite3.connect(database_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    backups = sorted(backup_folder.glob("pageturner-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_backup in backups[5:]:
        old_backup.unlink()
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Back up the PageTurner SQLite database.")
    parser.add_argument("--database", help="Path to the live SQLite database")
    parser.add_argument("--folder", help="Folder where dated backups are stored")
    args = parser.parse_args()
    print(f"Backup created: {create_backup(args.database, args.folder)}")
