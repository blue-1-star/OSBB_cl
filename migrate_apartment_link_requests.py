"""
Миграция безопасных запросов на привязку квартиры.

Пользователь больше не получает автоматическую привязку к произвольно введённой
квартире. Он создаёт запрос, а оператор подтверждает или отклоняет его.

Это устраняет две проблемы:
- нельзя увидеть автомобили другой квартиры до подтверждения;
- нельзя самостоятельно перепривязать свой Telegram-кабинет на чужую квартиру.

По умолчанию dry-run.

Запуск:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_apartment_link_requests.py
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_apartment_link_requests.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

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
        raise SystemExit("Не найдена таблица resident_accounts.")
    if not table_exists(cur, "apartments"):
        raise SystemExit("Не найдена таблица apartments.")

    existed = table_exists(cur, "apartment_link_requests")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS apartment_link_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resident_account_id INTEGER NOT NULL,
            telegram_user_id TEXT NOT NULL,
            current_apartment_id INTEGER,
            current_apartment_number TEXT,
            requested_apartment_id INTEGER NOT NULL,
            requested_apartment_number TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'NEW',
            resident_comment TEXT,
            operator_id TEXT,
            operator_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            reviewed_at TEXT,
            FOREIGN KEY(resident_account_id) REFERENCES resident_accounts(id),
            FOREIGN KEY(current_apartment_id) REFERENCES apartments(id),
            FOREIGN KEY(requested_apartment_id) REFERENCES apartments(id)
        )
    """)

    created_indexes = []
    indexes = [
        (
            "idx_apartment_link_requests_status",
            """
            CREATE INDEX idx_apartment_link_requests_status
            ON apartment_link_requests(status, created_at)
            """,
        ),
        (
            "idx_apartment_link_requests_resident",
            """
            CREATE INDEX idx_apartment_link_requests_resident
            ON apartment_link_requests(resident_account_id, status, created_at)
            """,
        ),
        (
            "idx_apartment_link_requests_requested_unit",
            """
            CREATE INDEX idx_apartment_link_requests_requested_unit
            ON apartment_link_requests(requested_apartment_id, status)
            """,
        ),
    ]

    for name, sql in indexes:
        if not index_exists(cur, name):
            cur.execute(sql)
            created_indexes.append(name)

    report_dir = paths.OSBB_EXPORTS_DIR / "users"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / (
        f"apartment_link_requests_migration_"
        f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )

    lines = [
        "=" * 100,
        "APARTMENT LINK REQUESTS MIGRATION",
        "=" * 100,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        f"Table existed before: {existed}",
        f"New indexes: {', '.join(created_indexes) if created_indexes else 'нет'}",
        "",
        "Новая логика:",
        "  • пользователь вводит номер квартиры;",
        "  • бот НЕ показывает автомобили и НЕ меняет привязку автоматически;",
        "  • создаётся запрос со статусом NEW;",
        "  • оператор подтверждает или отклоняет запрос;",
        "  • подтверждение меняет resident_accounts и пишется в operator_audit_log.",
        "",
    ]

    if args.apply:
        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="apartment_link_requests_migration",
                table_name="apartment_link_requests",
                row_id="",
                field_name="schema",
                old_value="",
                new_value="safe operator-approved apartment linking",
                source_context="migrate_apartment_link_requests.py",
                comment="Создана очередь заявок на привязку квартиры.",
                extra={"indexes_created": created_indexes},
                commit=False,
            )
        conn.commit()
        lines.append("APPLIED")
    else:
        conn.rollback()
        lines.append("DRY RUN COMPLETED - NO CHANGES SAVED")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    conn.close()

    print("=" * 100)
    print("APARTMENT LINK REQUESTS MIGRATION")
    print("=" * 100)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Table existed before:", existed)
    print("New indexes:", len(created_indexes))
    print("Report:", report_path)
    print()
    print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
