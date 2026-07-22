from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def migrate():
    db_file = paths.OSBB_DB_FILE

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS verification_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        apartment_id INTEGER,
        apartment_number TEXT,

        task_group TEXT,
        task_type TEXT,

        priority INTEGER DEFAULT 100,
        status TEXT DEFAULT 'new',

        source_name TEXT,
        source_record_id TEXT,

        object_table TEXT,
        object_id INTEGER,
        field_name TEXT,

        main_value TEXT,
        candidate_value TEXT,
        normalized_main_value TEXT,
        normalized_candidate_value TEXT,

        suggestion TEXT,
        comment TEXT,

        created_at TEXT,
        created_by TEXT,

        resolved_at TEXT,
        resolved_by TEXT,
        resolution TEXT,
        resolution_comment TEXT,

        FOREIGN KEY(apartment_id) REFERENCES apartments(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_tasks_status
    ON verification_tasks(status)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_tasks_apartment
    ON verification_tasks(apartment_number)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_tasks_type
    ON verification_tasks(task_type)
    """)

    conn.commit()
    conn.close()

    print("verification_tasks table created successfully")
    print("DB:", db_file)


if __name__ == "__main__":
    migrate()