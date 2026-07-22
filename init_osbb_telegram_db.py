from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_telegram_db():
    paths.ensure_directories()

    db_file = paths.OSBB_TELEGRAM_DB_FILE
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_name TEXT,
        chat_type TEXT,
        telegram_chat_id TEXT,
        source_file TEXT,
        imported_at TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        chat_id INTEGER,
        telegram_message_id TEXT,

        message_type TEXT,
        message_date TEXT,
        message_unixtime TEXT,
        edited_date TEXT,
        edited_unixtime TEXT,

        sender_name TEXT,
        sender_id TEXT,

        text_raw TEXT,

        file_name TEXT,
        file_size INTEGER,
        mime_type TEXT,

        imported_at TEXT,

        FOREIGN KEY(chat_id) REFERENCES telegram_chats(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_message_db_id INTEGER,
        chat_id INTEGER,

        fact_type TEXT,
        apartment_number TEXT,

        person_name TEXT,
        phone_raw TEXT,
        phone_normalized TEXT,

        ownership_type TEXT,

        license_plate TEXT,
        license_plate_normalized TEXT,

        car_brand TEXT,
        car_model TEXT,
        car_color TEXT,
        car_color_normalized TEXT,

        amount REAL,
        remote_count INTEGER,

        fact_status TEXT DEFAULT 'new',
        comment TEXT,

        created_at TEXT,

        FOREIGN KEY(telegram_message_db_id) REFERENCES telegram_messages(id),
        FOREIGN KEY(chat_id) REFERENCES telegram_chats(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat
    ON telegram_messages(chat_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_telegram_messages_date
    ON telegram_messages(message_date)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_telegram_facts_apartment
    ON telegram_facts(apartment_number)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_telegram_facts_plate
    ON telegram_facts(license_plate_normalized)
    """)

    conn.commit()
    conn.close()

    print("Telegram database created successfully")
    print("DB:", db_file)


if __name__ == "__main__":
    init_telegram_db()