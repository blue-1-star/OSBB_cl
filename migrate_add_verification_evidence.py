from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def migrate():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS verification_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        task_id INTEGER,

        source_name TEXT,
        source_db TEXT,
        source_table TEXT,
        source_record_id TEXT,

        evidence_type TEXT,
        evidence_value TEXT,
        normalized_value TEXT,
        match_type TEXT,

        comment TEXT,
        created_at TEXT,

        FOREIGN KEY(task_id) REFERENCES verification_tasks(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_verification_evidence_task
    ON verification_evidence(task_id)
    """)

    conn.commit()
    conn.close()

    print("verification_evidence table created successfully")


if __name__ == "__main__":
    migrate()