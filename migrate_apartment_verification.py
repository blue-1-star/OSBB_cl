from pathlib import Path
import sys
import sqlite3
from datetime import datetime

ROOT = Path(__file__).resolve().parent
PY_ROOT = ROOT.parent

for p in (ROOT, PY_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import paths, USE_TEST_DB

USE_TEST_DB = True
# USE_TEST_DB = False

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE

def migrate():
    db_file = get_db_file()

    print("=" * 70)
    print("APARTMENT VERIFICATION MIGRATION")
    print("=" * 70)
    print(f"DB: {db_file}")
    print(f"MODE: {'TEST' if USE_TEST_DB else 'PROD'}")
    print()

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS apartment_verification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apartment_id INTEGER NOT NULL,
            apartment_number TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            comment TEXT,
            verified_by INTEGER,
            verified_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(apartment_id)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_apartment_verification_status
        ON apartment_verification(status)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_apartment_verification_apartment_number
        ON apartment_verification(apartment_number)
    """)

    cur.execute("""
        INSERT OR IGNORE INTO apartment_verification (
            apartment_id,
            apartment_number,
            status,
            created_at,
            updated_at
        )
        SELECT
            id,
            apartment_number,
            'new',
            ?,
            ?
        FROM apartments
    """, (now(), now()))

    inserted = cur.rowcount
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM apartment_verification")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT status, COUNT(*)
        FROM apartment_verification
        GROUP BY status
        ORDER BY status
    """)
    stats = cur.fetchall()

    conn.close()

    print(f"Rows inserted/ignored from apartments: {inserted}")
    print(f"apartment_verification total: {total}")
    print()
    print("Status stats:")
    for status, count in stats:
        print(f"  {status}: {count}")

    print()
    print("=" * 70)
    print("MIGRATION COMPLETED")
    print("=" * 70)

if __name__ == "__main__":
    migrate()
