# database.py
import datetime

import duckdb
import pytz  # For timezone-aware datetimes

DB_FILE = "sre_dashboard.duckdb"


def get_db_connection():
    return duckdb.connect(database=DB_FILE, read_only=False)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create sequences first
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS incident_id_seq START 1;")
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS engineer_id_seq START 1;")

    # Incidents table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY DEFAULT nextval('incident_id_seq'),
        job_api_id VARCHAR,             -- ID from the source API (no longer unique)
        job_name VARCHAR,
        status VARCHAR,                 -- CRITICAL, ERROR, WARNING
        priority VARCHAR,               -- P1, P2, P3, P4
        log_url VARCHAR,
        first_detected_at TIMESTAMPTZ,
        responded_at TIMESTAMPTZ,
        resolved_at TIMESTAMPTZ,
        responding_engineer_id INTEGER, -- FK to engineers table
        inc_number VARCHAR,
        inc_link VARCHAR,
        notes TEXT,                     -- For any additional notes
        last_api_update TIMESTAMPTZ     -- When the API last reported this status
    );
    """)

    # Engineers table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS engineers (
        id INTEGER PRIMARY KEY DEFAULT nextval('engineer_id_seq'),
        name VARCHAR UNIQUE,
        on_call_level VARCHAR          -- L1, L2
    );
    """)

    # Add some default engineers if table is empty
    cursor.execute("SELECT COUNT(*) FROM engineers")
    if cursor.fetchone()[0] == 0:
        default_engineers = [
            ("Alice (L1)", "L1"),
            ("Bob (L2)", "L2"),
            ("Charlie (L1)", "L1"),
        ]
        cursor.executemany(
            "INSERT INTO engineers (name, on_call_level) VALUES (?, ?)",
            default_engineers,
        )

    conn.commit()
    conn.close()


# --- Helper to get current UTC time ---
def now_utc():
    return datetime.datetime.now(pytz.utc)


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
    # Example: Add an engineer
    # conn = get_db_connection()
    # conn.execute("INSERT INTO engineers (name, on_call_level) VALUES (?, ?)", ('David (L2)', 'L2'))
    # conn.commit()
    # conn.close()
