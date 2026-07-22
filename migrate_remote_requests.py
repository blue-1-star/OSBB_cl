"""
Создаёт очередь заявок на пульты шлагбаума.

Заявка из клиентского кабинета не выдаёт пульт автоматически.
Она попадает оператору со статусом NEW, после чего оператор может:
- взять в работу;
- отметить как выданную;
- отклонить с заметкой.

Все изменения пишутся в operator_audit_log.
По умолчанию dry-run.

Запуск:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_remote_requests.py
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_remote_requests.py --apply
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def index_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    definition: str,
) -> bool:
    cur.execute(f'PRAGMA table_info("{table}")')
    cols = {row[1] for row in cur.fetchall()}
    if column in cols:
        return False
    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    if not table_exists(cur, "resident_accounts"):
        raise SystemExit("Не найдена resident_accounts.")
    if not table_exists(cur, "apartments"):
        raise SystemExit("Не найдена apartments.")

    existed = table_exists(cur, "remote_requests")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS remote_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resident_account_id INTEGER NOT NULL,
            telegram_user_id TEXT NOT NULL,
            apartment_id INTEGER NOT NULL,
            apartment_number TEXT NOT NULL,
            request_kind TEXT NOT NULL DEFAULT 'FIRST',
            quantity INTEGER NOT NULL DEFAULT 1,
            resident_comment TEXT,
            status TEXT NOT NULL DEFAULT 'NEW',
            operator_id TEXT,
            operator_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            reviewed_at TEXT,
            issued_at TEXT,
            closed_at TEXT,
            FOREIGN KEY(resident_account_id) REFERENCES resident_accounts(id),
            FOREIGN KEY(apartment_id) REFERENCES apartments(id)
        )
    """)

    added_cols = []
    for name, definition in {
        "telegram_user_id": "TEXT",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "request_kind": "TEXT NOT NULL DEFAULT 'FIRST'",
        "quantity": "INTEGER NOT NULL DEFAULT 1",
        "resident_comment": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'NEW'",
        "operator_id": "TEXT",
        "operator_note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "reviewed_at": "TEXT",
        "issued_at": "TEXT",
        "closed_at": "TEXT",
    }.items():
        if add_column_if_missing(cur, "remote_requests", name, definition):
            added_cols.append(name)

    indexes_created = []
    indexes = [
        (
            "idx_remote_requests_resident",
            """
            CREATE INDEX idx_remote_requests_resident
            ON remote_requests(resident_account_id, status, created_at)
            """,
        ),
        (
            "idx_remote_requests_status",
            """
            CREATE INDEX idx_remote_requests_status
            ON remote_requests(status, created_at)
            """,
        ),
        (
            "idx_remote_requests_apartment",
            """
            CREATE INDEX idx_remote_requests_apartment
            ON remote_requests(apartment_id, created_at)
            """,
        ),
    ]

    for name, sql in indexes:
        if not index_exists(cur, name):
            cur.execute(sql)
            indexes_created.append(name)

    report_dir = paths.OSBB_EXPORTS_DIR / "remotes"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / (
        f"remote_requests_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )

    lines = [
        "=" * 98,
        "REMOTE REQUESTS MIGRATION",
        "=" * 98,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        f"Table existed before: {existed}",
        f"New columns: {', '.join(added_cols) if added_cols else 'нет'}",
        f"New indexes: {', '.join(indexes_created) if indexes_created else 'нет'}",
        "",
        "Назначение:",
        "  • пользователь создаёт обращение по пульту;",
        "  • пульт не выдаётся автоматически;",
        "  • оператор обрабатывает NEW / IN_REVIEW / ISSUED / REJECTED;",
        "  • сообщения пользователю остаются Telegram, SMS не используется.",
        "",
    ]

    if args.apply:
        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="remote_requests_migration",
                table_name="remote_requests",
                row_id="",
                field_name="schema",
                old_value="",
                new_value="remote request queue created",
                source_context="migrate_remote_requests.py",
                comment="Создана очередь заявок пользователей на пульты.",
                extra={"new_columns": added_cols, "indexes": indexes_created},
                commit=False,
            )
        conn.commit()
        lines.append("APPLIED")
    else:
        conn.rollback()
        lines.append("DRY RUN COMPLETED - NO CHANGES SAVED")

    report.write_text("\n".join(lines), encoding="utf-8")
    conn.close()

    print("=" * 98)
    print("REMOTE REQUESTS MIGRATION")
    print("=" * 98)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Table existed before:", existed)
    print("New columns:", len(added_cols))
    print("New indexes:", len(indexes_created))
    print("Report:", report)
    print()
    print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
