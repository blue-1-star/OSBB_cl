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


def init_quarantine_db():
    paths.ensure_directories()

    db_file = paths.OSBB_QUARANTINE_DB_FILE
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS source_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT,
        file_path TEXT,
        records_count INTEGER,
        imported_at TEXT,
        imported_by TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tbot_parking_import (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        ownership_type TEXT,
        ownership_type_raw TEXT,

        full_name TEXT,
        phone_raw TEXT,
        apartment_number TEXT,

        car_model TEXT,
        car_color TEXT,
        license_plate TEXT,
        status_raw TEXT,

        source TEXT,
        imported_at TEXT,
        imported_by TEXT,
        notes TEXT
    )
    """)

    conn.commit()
    conn.close()

    print("Quarantine database created successfully")
    print("DB:", db_file)


if __name__ == "__main__":
    init_quarantine_db()