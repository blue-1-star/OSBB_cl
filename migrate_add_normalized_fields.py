from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def add_column_if_missing(cur, table_name, column_name, column_type):
    cur.execute(f"PRAGMA table_info({table_name})")
    existing = [row[1] for row in cur.fetchall()]

    if column_name not in existing:
        cur.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )
        print(f"Added: {table_name}.{column_name}")
    else:
        print(f"Exists: {table_name}.{column_name}")


def migrate_main_db():
    db_file = paths.OSBB_DB_FILE
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    add_column_if_missing(cur, "vehicles", "license_plate_normalized", "TEXT")
    add_column_if_missing(cur, "vehicles", "plate_format_status", "TEXT")
    add_column_if_missing(cur, "vehicles", "car_model_normalized", "TEXT")
    add_column_if_missing(cur, "vehicles", "car_color_normalized", "TEXT")

    conn.commit()
    conn.close()


def migrate_quarantine_db():
    db_file = paths.OSBB_QUARANTINE_DB_FILE
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    add_column_if_missing(cur, "tbot_parking_import", "license_plate_normalized", "TEXT")
    add_column_if_missing(cur, "tbot_parking_import", "plate_format_status", "TEXT")
    add_column_if_missing(cur, "tbot_parking_import", "car_model_normalized", "TEXT")
    add_column_if_missing(cur, "tbot_parking_import", "car_color_normalized", "TEXT")
    add_column_if_missing(cur, "tbot_parking_import", "phone_normalized", "TEXT")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    print("Migrating main DB...")
    migrate_main_db()

    print()
    print("Migrating quarantine DB...")
    migrate_quarantine_db()

    print()
    print("Migration completed.")