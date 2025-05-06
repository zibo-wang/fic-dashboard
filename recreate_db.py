import os
import sqlite3

from database import init_db


def recreate_database():
    # Remove existing database file if it exists
    db_path = "incidents.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"Removed existing database at {db_path}")

    # Initialize new database
    init_db()
    print("Created new database with updated schema")


if __name__ == "__main__":
    recreate_database()
