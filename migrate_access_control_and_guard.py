"""
Миграция гранулярных прав доступа и рабочего кабинета охраны.

По умолчанию dry-run.
Миграция НЕ назначает роль ни одному Telegram-пользователю.
После установки доступа нет ни у кого, пока руководитель явно не выдаст роль
или отдельное разрешение через manage_staff_access.py.

Создаются:
- staff_principals             сотрудники / рабочие учётные записи;
- access_roles                роли;
- access_role_permissions     стандартные полномочия ролей;
- access_user_roles           назначение роли конкретному человеку + scope;
- access_user_permissions     индивидуальный ALLOW / DENY;
- access_audit_log            аудит доступа;
- remote_handover_events      физический приём / выдача пультов на посту.

Первый стандартный профиль:
GUARD_O — охранник поста / кассы O.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    definition: str,
) -> bool:
    if column in columns(cur, table):
        return False
    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    return True


def index_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def create_index_if_missing(cur: sqlite3.Cursor, name: str, sql: str) -> bool:
    if index_exists(cur, name):
        return False
    cur.execute(sql)
    return True


def create_tables(cur: sqlite3.Cursor) -> dict[str, bool]:
    existed = {}

    existed["staff_principals"] = table_exists(cur, "staff_principals")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staff_principals (
            telegram_user_id TEXT PRIMARY KEY,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    existed["access_roles"] = table_exists(cur, "access_roles")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_roles (
            role_code TEXT PRIMARY KEY,
            role_name TEXT NOT NULL,
            description TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    existed["access_role_permissions"] = table_exists(cur, "access_role_permissions")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_role_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_code TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'ALL',
            scope_value TEXT NOT NULL DEFAULT '*',
            effect TEXT NOT NULL DEFAULT 'ALLOW',
            is_active INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(role_code, resource, action, scope_type, scope_value)
        )
    """)

    existed["access_user_roles"] = table_exists(cur, "access_user_roles")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_user_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT NOT NULL,
            role_code TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'ALL',
            scope_value TEXT NOT NULL DEFAULT '*',
            is_active INTEGER NOT NULL DEFAULT 1,
            valid_from TEXT,
            valid_to TEXT,
            granted_by TEXT,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(telegram_user_id, role_code, scope_type, scope_value)
        )
    """)

    existed["access_user_permissions"] = table_exists(cur, "access_user_permissions")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_user_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'ALL',
            scope_value TEXT NOT NULL DEFAULT '*',
            effect TEXT NOT NULL DEFAULT 'ALLOW',
            is_active INTEGER NOT NULL DEFAULT 1,
            valid_from TEXT,
            valid_to TEXT,
            granted_by TEXT,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(telegram_user_id, resource, action, scope_type, scope_value)
        )
    """)

    existed["access_audit_log"] = table_exists(cur, "access_audit_log")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor_telegram_user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'ALL',
            scope_value TEXT NOT NULL DEFAULT '*',
            target_table TEXT,
            target_id TEXT,
            success INTEGER NOT NULL DEFAULT 1,
            details TEXT
        )
    """)

    existed["remote_handover_events"] = table_exists(cur, "remote_handover_events")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS remote_handover_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_kind TEXT NOT NULL,
            event_status TEXT NOT NULL DEFAULT 'CONFIRMED',
            post_code TEXT NOT NULL DEFAULT 'O',
            remote_request_id INTEGER,
            apartment_id INTEGER,
            apartment_number TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            operator_id TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    return {name: not was_existing for name, was_existing in existed.items()}


COMPAT_COLUMNS: dict[str, dict[str, str]] = {
    "staff_principals": {
        "telegram_user_id": "TEXT",
        "display_name": "TEXT",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "access_roles": {
        "role_code": "TEXT",
        "role_name": "TEXT",
        "description": "TEXT",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "access_role_permissions": {
        "role_code": "TEXT",
        "resource": "TEXT",
        "action": "TEXT",
        "scope_type": "TEXT NOT NULL DEFAULT 'ALL'",
        "scope_value": "TEXT NOT NULL DEFAULT '*'",
        "effect": "TEXT NOT NULL DEFAULT 'ALLOW'",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "access_user_roles": {
        "telegram_user_id": "TEXT",
        "role_code": "TEXT",
        "scope_type": "TEXT NOT NULL DEFAULT 'ALL'",
        "scope_value": "TEXT NOT NULL DEFAULT '*'",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "granted_by": "TEXT",
        "note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "access_user_permissions": {
        "telegram_user_id": "TEXT",
        "resource": "TEXT",
        "action": "TEXT",
        "scope_type": "TEXT NOT NULL DEFAULT 'ALL'",
        "scope_value": "TEXT NOT NULL DEFAULT '*'",
        "effect": "TEXT NOT NULL DEFAULT 'ALLOW'",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "granted_by": "TEXT",
        "note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "access_audit_log": {
        "created_at": "TEXT",
        "actor_telegram_user_id": "TEXT",
        "action_type": "TEXT",
        "resource": "TEXT",
        "action": "TEXT",
        "scope_type": "TEXT NOT NULL DEFAULT 'ALL'",
        "scope_value": "TEXT NOT NULL DEFAULT '*'",
        "target_table": "TEXT",
        "target_id": "TEXT",
        "success": "INTEGER NOT NULL DEFAULT 1",
        "details": "TEXT",
    },
    "remote_handover_events": {
        "event_kind": "TEXT",
        "event_status": "TEXT NOT NULL DEFAULT 'CONFIRMED'",
        "post_code": "TEXT NOT NULL DEFAULT 'O'",
        "remote_request_id": "INTEGER",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "quantity": "INTEGER NOT NULL DEFAULT 1",
        "operator_id": "TEXT",
        "note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
}


def ensure_columns(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for table, fields in COMPAT_COLUMNS.items():
        result[table] = []
        if not table_exists(cur, table):
            result[table].append("<table missing>")
            continue
        for field, definition in fields.items():
            if add_column_if_missing(cur, table, field, definition):
                result[table].append(field)
    return result


ROLE_ROWS = [
    (
        "GUARD_O",
        "Охранник — пост / касса O",
        "Подтверждает только поступления O и физические события по пультам на посту O.",
    ),
    (
        "CONCIERGE_K",
        "Консьерж — касса K",
        "Шаблон для отдельного консьержа K1–K6. Права назначаются со scope конкретной кассы.",
    ),
    (
        "FINANCE_OPERATOR",
        "Финансовый оператор",
        "Шаблон для банка, сверки, разнесения, массового ввода и исторических импортов.",
    ),
    (
        "ACCESS_MANAGER",
        "Руководитель доступа",
        "Назначает роли и индивидуальные разрешения. Не получает обычные кассовые права автоматически.",
    ),
    (
        "AUDITOR",
        "Контролёр / ревизор",
        "Только чтение финансовых и аудиторских данных.",
    ),
]

# For GUARD_O rights are deliberately narrow. No BANK, ALLOCATE_PAYMENT,
# VOID, CORRECT, CASHIER_BATCHES or access-management permissions.
ROLE_PERMISSION_ROWS = [
    ("GUARD_O", "guard_workspace", "ENTER", "CASHBOX", "O", "ALLOW", "Вход в отдельный кабинет охраны O."),
    ("GUARD_O", "payment_notices", "VIEW", "CASHBOX", "O", "ALLOW", "Видит только уведомления о наличных для O."),
    ("GUARD_O", "payment_notices", "CONFIRM", "CASHBOX", "O", "ALLOW", "Подтверждает факт получения наличных в O."),
    ("GUARD_O", "cashier_receipts", "VIEW", "CASHBOX", "O", "ALLOW", "Видит только свои рабочие записи O."),
    ("GUARD_O", "cashier_receipts", "CREATE", "CASHBOX", "O", "ALLOW", "Регистрирует новый фактический приём наличных в O."),
    ("GUARD_O", "cashbox_operations", "VIEW", "CASHBOX", "O", "ALLOW", "Видит операции своей кассы O."),
    ("GUARD_O", "remote_requests", "VIEW", "POST", "O", "ALLOW", "Видит очередь заявок на пульты поста O."),
    ("GUARD_O", "remote_requests", "ISSUE", "POST", "O", "ALLOW", "Подтверждает физическую выдачу пульта по заявке."),
    ("GUARD_O", "remote_handover_events", "VIEW", "POST", "O", "ALLOW", "Видит свои события приёма/выдачи пультов."),
    ("GUARD_O", "remote_handover_events", "CREATE", "POST", "O", "ALLOW", "Регистрирует физический приём/выдачу пульта."),
    ("ACCESS_MANAGER", "access_control", "MANAGE", "ALL", "*", "ALLOW", "Управляет ролями и персональными правами."),
    ("AUDITOR", "access_audit_log", "VIEW", "ALL", "*", "ALLOW", "Просмотр журнала прав."),
]


def seed_roles(cur: sqlite3.Cursor) -> dict[str, int]:
    roles_added = 0
    permissions_added = 0
    for role_code, role_name, description in ROLE_ROWS:
        cur.execute(
            """
            INSERT INTO access_roles (
                role_code, role_name, description, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(role_code) DO UPDATE SET
                role_name = excluded.role_name,
                description = excluded.description,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (role_code, role_name, description, now_db(), now_db()),
        )
        roles_added += int(cur.rowcount or 0)

    for role_code, resource, action, scope_type, scope_value, effect, note in ROLE_PERMISSION_ROWS:
        cur.execute(
            """
            SELECT id
            FROM access_role_permissions
            WHERE role_code = ?
              AND resource = ?
              AND action = ?
              AND scope_type = ?
              AND scope_value = ?
            """,
            (role_code, resource, action, scope_type, scope_value),
        )
        exists = cur.fetchone()
        if exists:
            cur.execute(
                """
                UPDATE access_role_permissions
                SET effect = ?, is_active = 1, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (effect, note, now_db(), int(exists[0])),
            )
        else:
            cur.execute(
                """
                INSERT INTO access_role_permissions (
                    role_code, resource, action,
                    scope_type, scope_value, effect, is_active,
                    note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    role_code, resource, action,
                    scope_type, scope_value, effect,
                    note, now_db(), now_db(),
                ),
            )
            permissions_added += 1
    return {"roles_upserted": roles_added, "permissions_inserted": permissions_added}


def ensure_indexes(cur: sqlite3.Cursor) -> list[str]:
    items = [
        (
            "idx_access_user_roles_user",
            "CREATE INDEX idx_access_user_roles_user ON access_user_roles(telegram_user_id, is_active)",
        ),
        (
            "idx_access_user_permissions_user",
            "CREATE INDEX idx_access_user_permissions_user ON access_user_permissions(telegram_user_id, resource, action, is_active)",
        ),
        (
            "idx_access_role_permissions_role",
            "CREATE INDEX idx_access_role_permissions_role ON access_role_permissions(role_code, resource, action, is_active)",
        ),
        (
            "idx_access_audit_actor",
            "CREATE INDEX idx_access_audit_actor ON access_audit_log(actor_telegram_user_id, created_at)",
        ),
        (
            "idx_remote_handover_post",
            "CREATE INDEX idx_remote_handover_post ON remote_handover_events(post_code, created_at)",
        ),
        (
            "idx_remote_handover_operator",
            "CREATE INDEX idx_remote_handover_operator ON remote_handover_events(operator_id, created_at)",
        ),
        (
            "idx_remote_handover_request",
            "CREATE INDEX idx_remote_handover_request ON remote_handover_events(remote_request_id)",
        ),
    ]
    created = []
    for name, sql in items:
        if create_index_if_missing(cur, name, sql):
            created.append(name)
    return created


def ensure_access_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    cur = conn.cursor()
    tables_created = create_tables(cur)
    fields_added = ensure_columns(cur)
    seed = seed_roles(cur)
    indexes = ensure_indexes(cur)
    return {
        "tables_created": tables_created,
        "fields_added": fields_added,
        "seed": seed,
        "indexes_created": indexes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        result = ensure_access_schema(conn)
        report_dir = paths.OSBB_EXPORTS_DIR / "access"
        report_dir.mkdir(parents=True, exist_ok=True)
        report = report_dir / f"access_guard_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

        lines = [
            "=" * 100,
            "ACCESS CONTROL + GUARD WORKSPACE MIGRATION",
            "=" * 100,
            f"DB: {db}",
            f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
            f"Apply: {args.apply}",
            "",
            "Таблицы:",
            *[
                f"  {name}: {'created' if created else 'already existed'}"
                for name, created in result["tables_created"].items()
            ],
            "",
            "Добавленные поля:",
            *[
                f"  {table}: {', '.join(fields) if fields else 'нет'}"
                for table, fields in result["fields_added"].items()
            ],
            "",
            f"Seed: {result['seed']}",
            f"Indexes: {', '.join(result['indexes_created']) if result['indexes_created'] else 'нет'}",
            "",
            "ВАЖНО: роль GUARD_O никому не выдана автоматически.",
            "Доступ появится только после явного назначения конкретному Telegram ID.",
        ]

        if args.apply:
            if audit_log:
                audit_log(
                    conn=conn,
                    operator_id="system",
                    user_id="system",
                    actor_type="system",
                    action_type="access_control_guard_migration",
                    table_name="access_roles",
                    row_id="",
                    field_name="schema",
                    old_value="",
                    new_value="granular access schema + GUARD_O role",
                    source_context="migrate_access_control_and_guard.py",
                    comment="Созданы роли, индивидуальные права и журнал доступа. Роли никому не назначены автоматически.",
                    extra=result,
                    commit=False,
                )
            conn.commit()
            lines.append("")
            lines.append("APPLIED")
        else:
            conn.rollback()
            lines.append("")
            lines.append("DRY RUN COMPLETED - NO CHANGES SAVED")

        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
