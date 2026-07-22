from pathlib import Path
import sqlite3
from datetime import datetime
import sys

# Находим корень Py, чтобы импортировать config.py
OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


SCHEMA_VERSION = "0.1.0"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_db():
    paths.ensure_directories()

    db_path = paths.OSBB_DB_FILE
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS schema_info (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schema_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        comment TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS apartments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_number TEXT NOT NULL UNIQUE,
        entrance TEXT,
        entrance_source TEXT,
        total_area REAL,
        object_type TEXT,
        status TEXT DEFAULT 'active',
        source TEXT,
        notes TEXT,
        created_at TEXT,
        created_by TEXT,
        updated_at TEXT,
        updated_by TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_id INTEGER,
        full_name TEXT,
        phone_raw TEXT,
        ownership_type TEXT,
        person_role TEXT,
        source TEXT,
        notes TEXT,
        created_at TEXT,
        created_by TEXT,
        updated_at TEXT,
        updated_by TEXT,
        FOREIGN KEY(apartment_id) REFERENCES apartments(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_id INTEGER,
        license_plate TEXT,
        car_model TEXT,
        car_color TEXT,
        parking_time TEXT,
        status TEXT,
        source TEXT,
        notes TEXT,
        created_at TEXT,
        created_by TEXT,
        updated_at TEXT,
        updated_by TEXT,
        FOREIGN KEY(apartment_id) REFERENCES apartments(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS contact_methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_id INTEGER,
        person_id INTEGER,
        contact_type TEXT,
        contact_value TEXT,
        telegram_user_id TEXT,
        telegram_username TEXT,
        contact_owner TEXT,
        is_primary INTEGER DEFAULT 0,
        source TEXT,
        notes TEXT,
        created_at TEXT,
        created_by TEXT,
        updated_at TEXT,
        updated_by TEXT,
        FOREIGN KEY(apartment_id) REFERENCES apartments(id),
        FOREIGN KEY(person_id) REFERENCES persons(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_id INTEGER,
        event_time TEXT,
        event_group TEXT,
        event_type TEXT,
        amount REAL,
        status TEXT,
        source TEXT,
        related_table TEXT,
        related_record_id TEXT,
        file_path TEXT,
        comment TEXT,
        created_at TEXT,
        created_by TEXT,
        FOREIGN KEY(apartment_id) REFERENCES apartments(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS verification_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apartment_number TEXT,
        field_name TEXT,
        paper_value TEXT,
        bot_value TEXT,
        final_value TEXT,
        status TEXT,
        verified_by TEXT,
        verified_at TEXT,
        comment TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_time TEXT,
        username TEXT,
        table_name TEXT,
        record_id TEXT,
        action TEXT,
        field_name TEXT,
        old_value TEXT,
        new_value TEXT,
        comment TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT,
        source_name TEXT,
        source_id TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS raw_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER,
        message_datetime TEXT,
        sender_name TEXT,
        sender_username TEXT,
        sender_user_id TEXT,
        message_text TEXT,
        has_photo INTEGER DEFAULT 0,
        file_path TEXT,
        imported_at TEXT,
        processed_status TEXT DEFAULT 'new',
        FOREIGN KEY(source_id) REFERENCES message_sources(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS extracted_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_message_id INTEGER,
        apartment_number TEXT,
        fact_type TEXT,
        amount REAL,
        fact_date TEXT,
        confidence REAL,
        status TEXT DEFAULT 'new',
        comment TEXT,
        FOREIGN KEY(raw_message_id) REFERENCES raw_messages(id)
    )
    """)

    cur.execute(
        """
        INSERT INTO schema_info (schema_version, created_at, comment)
        VALUES (?, ?, ?)
        """,
        (SCHEMA_VERSION, now(), "Initial OSBB database structure")
    )

    conn.commit()
    conn.close()

    print("OSBB database created successfully")
    print("DB:", db_path)


if __name__ == "__main__":
    create_db()