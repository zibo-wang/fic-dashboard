# database.py
import datetime
import hashlib

import duckdb
import pytz  # For timezone-aware datetimes

DB_FILE = "sre_dashboard.duckdb"


def get_db_connection():
    return duckdb.connect(database=DB_FILE, read_only=False)


def hash_password(password):
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create sequences first
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS incident_id_seq START 1;")
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS engineer_id_seq START 1;")
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS user_id_seq START 1;")

    # Users table for authentication
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY DEFAULT nextval('user_id_seq'),
        username VARCHAR UNIQUE NOT NULL,
        password_hash VARCHAR NOT NULL,
        full_name VARCHAR,
        role VARCHAR DEFAULT 'engineer',  -- 'admin', 'engineer'
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMPTZ
    );
    """)

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
        on_call_level VARCHAR,          -- L1, L2
        user_id INTEGER,                -- FK to users table
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)

    # Add default admin user if no users exist
    user_count_result = cursor.execute("SELECT COUNT(*) FROM users").fetchone()
    if user_count_result and user_count_result[0] == 0:
        default_users = [
            (
                "admin",
                hash_password("admin123"),
                "System Administrator",
                "admin",
            ),
            (
                "alice.johnson",
                hash_password("engineer123"),
                "Alice Johnson",
                "engineer",
            ),
            (
                "bob.smith",
                hash_password("engineer123"),
                "Bob Smith",
                "engineer",
            ),
            (
                "charlie.davis",
                hash_password("engineer123"),
                "Charlie Davis",
                "engineer",
            ),
        ]
        cursor.executemany(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
            default_users,
        )

    # Add some default engineers if table is empty
    engineer_count_result = cursor.execute(
        "SELECT COUNT(*) FROM engineers"
    ).fetchone()
    if engineer_count_result and engineer_count_result[0] == 0:
        # Get user IDs for engineers
        alice_result = cursor.execute(
            "SELECT id FROM users WHERE username = 'alice.johnson'"
        ).fetchone()
        bob_result = cursor.execute(
            "SELECT id FROM users WHERE username = 'bob.smith'"
        ).fetchone()
        charlie_result = cursor.execute(
            "SELECT id FROM users WHERE username = 'charlie.davis'"
        ).fetchone()

        if alice_result and bob_result and charlie_result:
            alice_id = alice_result[0]
            bob_id = bob_result[0]
            charlie_id = charlie_result[0]

            default_engineers = [
                ("Alice Johnson", "L1", alice_id),
                ("Bob Smith", "L2", bob_id),
                ("Charlie Davis", "L1", charlie_id),
            ]
            cursor.executemany(
                "INSERT INTO engineers (name, on_call_level, user_id) VALUES (?, ?, ?)",
                default_engineers,
            )

    conn.commit()
    conn.close()


# --- Helper to get current UTC time ---
def now_utc():
    return datetime.datetime.now(pytz.utc)


# --- Authentication Functions ---
def authenticate_user(username, password):
    """Authenticate a user with username and password.

    Args:
        username (str): The username
        password (str): The plain text password

    Returns:
        dict or None: User information if authentication successful, None otherwise
    """
    conn = get_db_connection()
    try:
        password_hash = hash_password(password)
        result = conn.execute(
            """
            SELECT id, username, full_name, role, is_active, last_login
            FROM users
            WHERE username = ? AND password_hash = ? AND is_active = TRUE
            """,
            (username, password_hash),
        ).fetchone()

        if result:
            user_id, username, full_name, role, is_active, last_login = result
            # Update last login time
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (now_utc(), user_id),
            )
            conn.commit()

            return {
                "id": user_id,
                "username": username,
                "full_name": full_name,
                "role": role,
                "is_active": is_active,
                "last_login": last_login,
            }
        return None
    except Exception as e:
        print(f"Authentication error: {e}")
        return None
    finally:
        conn.close()


def get_user_by_id(user_id):
    """Get user information by user ID.

    Args:
        user_id (int): The user ID

    Returns:
        dict or None: User information if found, None otherwise
    """
    conn = get_db_connection()
    try:
        result = conn.execute(
            """
            SELECT id, username, full_name, role, is_active, last_login
            FROM users
            WHERE id = ? AND is_active = TRUE
            """,
            (user_id,),
        ).fetchone()

        if result:
            user_id, username, full_name, role, is_active, last_login = result
            return {
                "id": user_id,
                "username": username,
                "full_name": full_name,
                "role": role,
                "is_active": is_active,
                "last_login": last_login,
            }
        return None
    except Exception as e:
        print(f"Error getting user: {e}")
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
    # Example: Add an engineer
    # conn = get_db_connection()
    # conn.execute("INSERT INTO engineers (name, on_call_level) VALUES (?, ?)", ('David (L2)', 'L2'))
    # conn.commit()
    # conn.close()
