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


def column_exists(cur, table_name, column_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return column_name in [row[1] for row in cur.fetchall()]


def add_column_if_missing(cur, table_name, column_name, column_def):
    if column_exists(cur, table_name, column_name):
        return False

    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    return True


def index_exists(cur, index_name):
    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'index'
          AND name = ?
    """, (index_name,))
    return cur.fetchone() is not None


def create_index_if_missing(cur, index_name, sql):
    if index_exists(cur, index_name):
        return False

    cur.execute(sql)
    return True


def main():
    paths.ensure_directories()

    db_file = get_db_file()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("OPERATOR AUDIT LOG V2 MIGRATION")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
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

    added_columns = []

    columns_to_add = [
        ("db_mode", "TEXT"),
        ("db_file", "TEXT"),
        ("table_name", "TEXT"),
        ("field_name", "TEXT"),
        ("row_label", "TEXT"),
        ("action_status", "TEXT DEFAULT 'applied'"),
        ("review_status", "TEXT DEFAULT 'pending'"),
        ("reviewed_by", "TEXT"),
        ("reviewed_at", "TEXT"),
        ("review_comment", "TEXT"),
        ("source_context", "TEXT"),
        ("extra_json", "TEXT"),
    ]

    for column_name, column_def in columns_to_add:
        if add_column_if_missing(cur, "operator_audit_log", column_name, column_def):
            added_columns.append(column_name)

    indexes = [
        ("idx_operator_audit_created_at", "CREATE INDEX idx_operator_audit_created_at ON operator_audit_log(created_at)"),
        ("idx_operator_audit_operator", "CREATE INDEX idx_operator_audit_operator ON operator_audit_log(operator_telegram_id)"),
        ("idx_operator_audit_action", "CREATE INDEX idx_operator_audit_action ON operator_audit_log(action_type)"),
        ("idx_operator_audit_apartment", "CREATE INDEX idx_operator_audit_apartment ON operator_audit_log(apartment_number)"),
        ("idx_operator_audit_vehicle", "CREATE INDEX idx_operator_audit_vehicle ON operator_audit_log(vehicle_id)"),
        ("idx_operator_audit_review_status", "CREATE INDEX idx_operator_audit_review_status ON operator_audit_log(review_status)"),
        ("idx_operator_audit_action_status", "CREATE INDEX idx_operator_audit_action_status ON operator_audit_log(action_status)"),
        ("idx_operator_audit_field", "CREATE INDEX idx_operator_audit_field ON operator_audit_log(table_name, field_name)"),
    ]

    added_indexes = []

    for index_name, sql in indexes:
        if create_index_if_missing(cur, index_name, sql):
            added_indexes.append(index_name)

    cur.execute("""
        UPDATE operator_audit_log
        SET
            db_mode = COALESCE(db_mode, ?),
            db_file = COALESCE(db_file, ?),
            action_status = COALESCE(action_status, 'applied'),
            review_status = COALESCE(review_status, 'pending')
    """, (
        "TEST/WORK" if USE_TEST_DB else "PROD",
        str(db_file),
    ))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM operator_audit_log")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT review_status, COUNT(*)
        FROM operator_audit_log
        GROUP BY review_status
    """)
    review_stats = cur.fetchall()

    conn.close()

    print("operator_audit_log is ready for supervisor review.")
    print("Rows:", total)
    print("")

    print("Added columns:")
    if added_columns:
        for column in added_columns:
            print("  +", column)
    else:
        print("  none")

    print("")
    print("Added indexes:")
    if added_indexes:
        for index in added_indexes:
            print("  +", index)
    else:
        print("  none")

    print("")
    print("Review status:")
    if review_stats:
        for status, count in review_stats:
            print(f"  {status}: {count}")
    else:
        print("  no rows yet")

    print("")
    print("=" * 70)
    print("MIGRATION COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    main()
