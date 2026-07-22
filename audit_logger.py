from pathlib import Path
import sys
import sqlite3
from datetime import datetime
import json

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

try:
    from config import paths, USE_TEST_DB
except Exception:
    paths = None
    USE_TEST_DB = True


def get_db_file():
    if paths is None:
        raise RuntimeError("config.py не найден. Передайте db_file явно.")
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def add_column_if_missing(cur, table_name, column_name, column_def):
    cols = table_columns(cur, table_name)
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        return True
    return False


def ensure_audit_log(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS operator_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator_id TEXT,
            user_id TEXT,
            actor_type TEXT DEFAULT 'system',
            action_type TEXT,
            table_name TEXT,
            row_id TEXT,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            action_status TEXT DEFAULT 'applied',
            review_status TEXT DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_comment TEXT,
            source_context TEXT,
            comment TEXT,
            extra_json TEXT,
            db_mode TEXT,
            db_file TEXT
        )
    """)

    migrations = {
        "created_at": "TEXT",
        "operator_id": "TEXT",
        "user_id": "TEXT",
        "actor_type": "TEXT DEFAULT 'system'",
        "action_type": "TEXT",
        "table_name": "TEXT",
        "row_id": "TEXT",
        "field_name": "TEXT",
        "old_value": "TEXT",
        "new_value": "TEXT",
        "action_status": "TEXT DEFAULT 'applied'",
        "review_status": "TEXT DEFAULT 'pending'",
        "reviewed_by": "TEXT",
        "reviewed_at": "TEXT",
        "review_comment": "TEXT",
        "source_context": "TEXT",
        "comment": "TEXT",
        "extra_json": "TEXT",
        "db_mode": "TEXT",
        "db_file": "TEXT",
    }

    added = []
    for col, col_def in migrations.items():
        if add_column_if_missing(cur, "operator_audit_log", col, col_def):
            added.append(col)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_created ON operator_audit_log(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_operator ON operator_audit_log(operator_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_user ON operator_audit_log(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_actor ON operator_audit_log(actor_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_action ON operator_audit_log(action_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_table_row ON operator_audit_log(table_name, row_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_review ON operator_audit_log(review_status)")

    return added


def audit_log(
    *,
    conn=None,
    db_file=None,
    operator_id=None,
    user_id=None,
    actor_type="system",
    action_type,
    table_name,
    row_id=None,
    field_name=None,
    old_value=None,
    new_value=None,
    action_status="applied",
    review_status="pending",
    source_context=None,
    comment=None,
    extra=None,
    commit=True,
):
    owns_connection = False

    if conn is None:
        db_file = db_file or get_db_file()
        conn = sqlite3.connect(db_file)
        owns_connection = True
    else:
        db_file = db_file or ""

    ensure_audit_log(conn)
    cur = conn.cursor()

    values = {
        "created_at": now_db(),
        "operator_id": "" if operator_id is None else str(operator_id),
        "user_id": "" if user_id is None else str(user_id),
        "actor_type": actor_type or "system",
        "action_type": action_type,
        "table_name": table_name,
        "row_id": "" if row_id is None else str(row_id),
        "field_name": "" if field_name is None else str(field_name),
        "old_value": "" if old_value is None else str(old_value),
        "new_value": "" if new_value is None else str(new_value),
        "action_status": action_status or "applied",
        "review_status": review_status or "pending",
        "source_context": "" if source_context is None else str(source_context),
        "comment": "" if comment is None else str(comment),
        "extra_json": json.dumps(extra or {}, ensure_ascii=False),
        "db_mode": "TEST/WORK" if USE_TEST_DB else "PROD",
        "db_file": str(db_file or get_db_file()),
    }

    cols = table_columns(cur, "operator_audit_log")
    insert_cols = [k for k in values if k in cols]
    placeholders = ",".join("?" for _ in insert_cols)

    cur.execute(
        f"INSERT INTO operator_audit_log ({', '.join(insert_cols)}) VALUES ({placeholders})",
        tuple(values[k] for k in insert_cols),
    )

    audit_id = cur.lastrowid

    if commit:
        conn.commit()

    if owns_connection:
        conn.close()

    return audit_id


def audit_field_change(
    *,
    conn,
    table_name,
    row_id,
    field_name,
    old_value,
    new_value,
    operator_id=None,
    user_id=None,
    actor_type="operator",
    action_type="update_field",
    source_context=None,
    comment=None,
    extra=None,
):
    return audit_log(
        conn=conn,
        operator_id=operator_id,
        user_id=user_id,
        actor_type=actor_type,
        action_type=action_type,
        table_name=table_name,
        row_id=row_id,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        source_context=source_context,
        comment=comment,
        extra=extra,
        commit=False,
    )


def audit_system_event(
    *,
    conn=None,
    db_file=None,
    action_type,
    source_context=None,
    comment=None,
    extra=None,
    commit=True,
):
    return audit_log(
        conn=conn,
        db_file=db_file,
        actor_type="system",
        operator_id="system",
        user_id="system",
        action_type=action_type,
        table_name="system",
        row_id="",
        field_name="",
        old_value="",
        new_value="",
        source_context=source_context,
        comment=comment,
        extra=extra,
        commit=commit,
    )


def self_test():
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)

    audit_id = audit_log(
        conn=conn,
        actor_type="system",
        operator_id="self_test",
        user_id="self_test",
        action_type="audit_logger_self_test",
        table_name="operator_audit_log",
        row_id="",
        field_name="self_test",
        old_value="",
        new_value="ok",
        source_context="audit_logger.py self-test",
        comment="Проверочная запись журнала аудита",
        extra={"module": "audit_logger.py"},
        commit=True,
    )

    conn.close()

    print("=" * 70)
    print("AUDIT LOGGER SELF TEST")
    print("=" * 70)
    print("DB:", db_file)
    print("Audit row inserted:", audit_id)
    print("=" * 70)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit logger helper for OSBB modules.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        print("audit_logger.py is a helper module.")
        print("Use --self-test to insert one test audit row.")


if __name__ == "__main__":
    main()
