from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def main():
    paths.ensure_directories()

    db_file = get_db_file()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("OPERATOR AUDIT LOG MIGRATION")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST" if USE_TEST_DB else "PROD")
    print("")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS operator_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        created_at TEXT NOT NULL,

        operator_telegram_id TEXT,
        operator_username TEXT,
        operator_name TEXT,

        action_type TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,

        apartment_number TEXT,
        vehicle_id INTEGER,

        old_value TEXT,
        new_value TEXT,

        comment TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_operator_audit_created_at
    ON operator_audit_log(created_at)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_operator_audit_operator
    ON operator_audit_log(operator_telegram_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_operator_audit_action
    ON operator_audit_log(action_type)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_operator_audit_apartment
    ON operator_audit_log(apartment_number)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_operator_audit_vehicle
    ON operator_audit_log(vehicle_id)
    """)

    conn.commit()

    cur.execute("""
        SELECT COUNT(*)
        FROM operator_audit_log
    """)
    total = cur.fetchone()[0]

    conn.close()

    print("Table operator_audit_log is ready.")
    print("Rows:", total)
    print("")
    print("=" * 70)
    print("MIGRATION COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    main()
