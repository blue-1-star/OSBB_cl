#!/usr/bin/env python3
"""Seed and grant a scoped resident self-service role.

The role permits viewing only one confirmed apartment and creating correction
requests. It deliberately does not grant direct edits to vehicles, payments,
tariffs, or apartment links.

The command is dry-run by default. ``--apply`` requires an exact confirmation.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
ROLE_CODE = "RESIDENT_SELF_SERVICE"

PERMISSIONS = [
    ("resident_portal", "ENTER", "Вход в личный кабинет жителя."),
    ("apartments", "VIEW", "Просмотр только подтверждённой квартиры."),
    ("vehicles", "VIEW", "Просмотр автомобилей подтверждённой квартиры."),
    ("parking_balances", "VIEW", "Просмотр баланса парковки своей квартиры."),
    ("parking_charges", "VIEW", "Просмотр начислений парковки своей квартиры."),
    ("parking_payments", "VIEW", "Просмотр оплат парковки своей квартиры."),
    ("apartment_link_requests", "CREATE", "Запросить смену привязки квартиры."),
    ("vehicle_change_requests", "CREATE", "Предложить исправление или добавление автомобиля."),
    ("resident_profile_change_requests", "CREATE", "Предложить исправление собственных данных."),
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def backup_database(source: Path) -> Path:
    backup_dir = BACKUP_DIR if source.resolve() == DEFAULT_DB.resolve() else source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        "before_2026-09-18_007_resident_self_service_"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + ".db"
    )
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return target


def require_schema(cur: sqlite3.Cursor) -> None:
    required = {
        "resident_accounts",
        "access_roles",
        "access_role_permissions",
        "access_user_roles",
        "access_audit_log",
    }
    missing = sorted(name for name in required if not table_exists(cur, name))
    if missing:
        raise RuntimeError("Нет таблиц модели доступа: " + ", ".join(missing))


def resolve_resident(cur: sqlite3.Cursor, telegram_id: str, apartment_number: str) -> sqlite3.Row:
    row = cur.execute(
        """
        SELECT id, telegram_user_id, apartment_id, apartment_number, status
        FROM resident_accounts
        WHERE telegram_user_id=?
        """,
        (telegram_id,),
    ).fetchone()
    if not row:
        raise RuntimeError("Telegram-пользователь ещё не запускал бота.")
    if row["status"] != "apartment_confirmed":
        raise RuntimeError("Квартира пользователя ещё не подтверждена оператором.")
    if str(row["apartment_number"] or "").strip() != apartment_number:
        raise RuntimeError(
            "Подтверждённая квартира пользователя не совпадает с указанной: "
            f"{row['apartment_number']!r} != {apartment_number!r}."
        )
    return row


def apply_role(cur: sqlite3.Cursor, telegram_id: str, apartment_number: str) -> None:
    timestamp = now()
    cur.execute(
        """
        INSERT INTO access_roles(role_code, role_name, description, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(role_code) DO UPDATE SET
            role_name=excluded.role_name,
            description=excluded.description,
            is_active=1,
            updated_at=excluded.updated_at
        """,
        (
            ROLE_CODE,
            "Житель — самообслуживание",
            "Просмотр только своей квартиры и создание заявок на исправление. "
            "Без прямого редактирования реестров и финансов.",
            timestamp,
            timestamp,
        ),
    )

    for resource, action, note in PERMISSIONS:
        existing = cur.execute(
            """
            SELECT id FROM access_role_permissions
            WHERE role_code=? AND resource=? AND action=? AND scope_type='APARTMENT' AND scope_value='*'
            """,
            (ROLE_CODE, resource, action),
        ).fetchone()
        if existing:
            cur.execute(
                """
                UPDATE access_role_permissions
                SET effect='ALLOW', is_active=1, note=?, updated_at=?
                WHERE id=?
                """,
                (note, timestamp, existing[0]),
            )
        else:
            cur.execute(
                """
                INSERT INTO access_role_permissions(
                    role_code, resource, action, scope_type, scope_value,
                    effect, is_active, note, created_at, updated_at
                ) VALUES (?, ?, ?, 'APARTMENT', '*', 'ALLOW', 1, ?, ?, ?)
                """,
                (ROLE_CODE, resource, action, note, timestamp, timestamp),
            )

    cur.execute(
        """
        INSERT INTO access_user_roles(
            telegram_user_id, role_code, scope_type, scope_value, is_active,
            valid_from, valid_to, granted_by, note, created_at, updated_at
        ) VALUES (?, ?, 'APARTMENT', ?, 1, ?, NULL, 'system', ?, ?, ?)
        ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value) DO UPDATE SET
            is_active=1, valid_to=NULL, granted_by='system', note=excluded.note,
            updated_at=excluded.updated_at
        """,
        (
            telegram_id,
            ROLE_CODE,
            apartment_number,
            timestamp,
            "First resident self-service test; operator-approved apartment link.",
            timestamp,
            timestamp,
        ),
    )

    cur.execute(
        """
        INSERT INTO access_audit_log(
            created_at, actor_telegram_user_id, action_type,
            resource, action, scope_type, scope_value,
            target_table, target_id, success, details
        ) VALUES (?, 'system', 'resident_self_service_granted',
                  'access_user_roles', 'GRANT', 'APARTMENT', ?,
                  'resident_accounts', ?, 1, ?)
        """,
        (
            timestamp,
            apartment_number,
            telegram_id,
            f"Granted {ROLE_CODE}; direct registry and finance edits are excluded.",
        ),
    )


def verify(conn: sqlite3.Connection, telegram_id: str, apartment_number: str) -> None:
    cur = conn.cursor()
    grants = cur.execute(
        """
        SELECT resource, action
        FROM access_role_permissions
        WHERE role_code=? AND is_active=1
        ORDER BY resource, action
        """,
        (ROLE_CODE,),
    ).fetchall()
    assignment = cur.execute(
        """
        SELECT role_code, scope_type, scope_value, is_active
        FROM access_user_roles
        WHERE telegram_user_id=? AND role_code=?
        """,
        (telegram_id, ROLE_CODE),
    ).fetchone()
    if not assignment or tuple(assignment) != (ROLE_CODE, "APARTMENT", apartment_number, 1):
        raise RuntimeError("Не удалось проверить назначение роли.")
    if ("vehicles", "EDIT") in [tuple(row) for row in grants]:
        raise RuntimeError("Опасное прямое право vehicles.EDIT не должно выдаваться жителю.")
    if len(grants) != len(PERMISSIONS):
        raise RuntimeError("Список прав роли не совпадает с ожидаемым.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant scoped resident self-service role.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--telegram-id", required=True)
    parser.add_argument("--apartment", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = args.db.resolve()
    apartment = str(args.apartment).strip()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        require_schema(conn.cursor())
        resident = resolve_resident(conn.cursor(), str(args.telegram_id), apartment)
        print(f"Resident: {resident['telegram_user_id']} | apartment {apartment}")
        print(f"Role: {ROLE_CODE} | permissions: {len(PERMISSIONS)}")
        print("Direct edits: excluded")
        if not args.apply:
            print("DRY RUN ONLY — no changes made.")
            return 0
    finally:
        conn.close()

    backup = backup_database(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        apply_role(conn.cursor(), str(args.telegram_id), apartment)
        verify(conn, str(args.telegram_id), apartment)
        conn.commit()
        print(f"APPLIED. Backup: {backup}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
