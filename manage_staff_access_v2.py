r"""
Управление индивидуальными правами сотрудников ОСББ.

Работает только после migrate_access_control_and_guard.py.
По умолчанию ничего не меняет. Для записи добавьте --apply.

Примеры:

1. Посмотреть роли и их права:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\manage_staff_access.py --roles

2. Посмотреть права конкретного Telegram ID:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\manage_staff_access.py --show-user 123456789

3. Выдать человеку кабинет охранника O:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\manage_staff_access.py ^
  --grant-role 123456789 GUARD_O ^
  --name "Охранник дневной смены" ^
  --note "Тестовый доступ к посту O"

Для роли GUARD_O scope не задавайте: используется ALL:*.
Это безопасно, потому что сама роль уже содержит только узкие права
CASHBOX:O и POST:O; она не даёт доступ к K1–K6, C, банку или корректировкам.

4. После проверки выполнить именно такую же команду с --apply.

5. Дать индивидуальное дополнительное право:
... --grant 123456789 remote_handover_events CREATE --scope POST:O --apply

6. Индивидуально запретить действие даже при наличии роли:
... --deny 123456789 payment_notices CONFIRM --scope CASHBOX:O --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SANDBOX_DIR = ROOT / "Data" / "db" / "sandbox"
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from access_control import (
    get_db_file,
    has_permission,
    norm_scope_type,
    norm_scope_value,
    schema_ready,
    text,
)

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_scope(raw: str | None) -> tuple[str, str]:
    value = text(raw)
    if not value:
        return "ALL", "*"
    if ":" not in value:
        raise ValueError("Scope: TYPE:VALUE, например CASHBOX:O или POST:O.")
    scope_type, scope_value = value.split(":", 1)
    if not text(scope_type) or not text(scope_value):
        raise ValueError("Scope: TYPE:VALUE, например CASHBOX:O или POST:O.")
    return norm_scope_type(scope_type), norm_scope_value(scope_value)


def print_roles(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT role_code, role_name, description, is_active
        FROM access_roles
        ORDER BY role_code
    """)
    rows = cur.fetchall()
    print("РОЛИ")
    print("=" * 100)
    for role in rows:
        print(f"{role[0]} | {role[1]} | active={role[3]}")
        print(f"  {role[2] or '-'}")
        cur.execute("""
            SELECT resource, action, scope_type, scope_value, effect, note
            FROM access_role_permissions
            WHERE role_code = ? AND is_active = 1
            ORDER BY resource, action, scope_type, scope_value
        """, (role[0],))
        for item in cur.fetchall():
            print(
                f"  • {item[0]}.{item[1]} | {item[4]} | "
                f"{item[2]}:{item[3]} | {item[5] or '-'}"
            )
        print()


def print_user(conn: sqlite3.Connection, user_id: str) -> None:
    cur = conn.cursor()
    print(f"ПРАВА ПОЛЬЗОВАТЕЛЯ {user_id}")
    print("=" * 100)

    cur.execute("""
        SELECT telegram_user_id, display_name, is_active, note
        FROM staff_principals
        WHERE telegram_user_id = ?
    """, (str(user_id),))
    principal = cur.fetchone()
    if principal:
        print(
            f"Сотрудник: {principal[1] or '-'} | "
            f"active={principal[2]} | {principal[3] or '-'}"
        )
    else:
        print("Сотрудник ещё не внесён в staff_principals.")

    cur.execute("""
        SELECT role_code, scope_type, scope_value, is_active,
               valid_from, valid_to, granted_by, note
        FROM access_user_roles
        WHERE telegram_user_id = ?
        ORDER BY is_active DESC, role_code, scope_type, scope_value
    """, (str(user_id),))
    rows = cur.fetchall()
    print("\nРоли:")
    if not rows:
        print("  нет")
    for item in rows:
        print(
            f"  {item[0]} | {item[1]}:{item[2]} | active={item[3]} | "
            f"from={item[4] or '-'} to={item[5] or '-'} | "
            f"by={item[6] or '-'} | {item[7] or '-'}"
        )

    cur.execute("""
        SELECT resource, action, scope_type, scope_value, effect,
               is_active, valid_from, valid_to, granted_by, note
        FROM access_user_permissions
        WHERE telegram_user_id = ?
        ORDER BY is_active DESC, resource, action, scope_type, scope_value
    """, (str(user_id),))
    rows = cur.fetchall()
    print("\nИндивидуальные права:")
    if not rows:
        print("  нет")
    for item in rows:
        print(
            f"  {item[0]}.{item[1]} | {item[4]} | {item[2]}:{item[3]} | "
            f"active={item[5]} | from={item[6] or '-'} to={item[7] or '-'} | "
            f"by={item[8] or '-'} | {item[9] or '-'}"
        )

    print("\nКонтрольный доступ охраны O:")
    for resource, action in [
        ("guard_workspace", "ENTER"),
        ("payment_notices", "VIEW"),
        ("payment_notices", "CONFIRM"),
        ("cashier_receipts", "CREATE"),
        ("remote_requests", "ISSUE"),
    ]:
        allowed = has_permission(
            user_id, resource, action,
            scope_type="CASHBOX" if resource in {
                "guard_workspace", "payment_notices", "cashier_receipts"
            } else "POST",
            scope_value="O",
            conn=conn,
        )
        print(f"  {resource}.{action}: {'ALLOW' if allowed else 'DENY'}")


def ensure_principal(
    cur: sqlite3.Cursor,
    user_id: str,
    name: str | None,
    note: str | None,
) -> None:
    cur.execute("""
        INSERT INTO staff_principals (
            telegram_user_id, display_name, is_active, note, created_at, updated_at
        )
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            display_name = COALESCE(excluded.display_name, staff_principals.display_name),
            is_active = 1,
            note = COALESCE(excluded.note, staff_principals.note),
            updated_at = excluded.updated_at
    """, (str(user_id), name or None, note or None, now_db(), now_db()))


def grant_role(
    cur: sqlite3.Cursor,
    *,
    user_id: str,
    role_code: str,
    scope_type: str,
    scope_value: str,
    granted_by: str,
    note: str | None,
) -> None:
    cur.execute("SELECT 1 FROM access_roles WHERE role_code = ?", (role_code,))
    if not cur.fetchone():
        raise ValueError(f"Роль не найдена: {role_code}")

    cur.execute("""
        SELECT id
        FROM access_user_roles
        WHERE telegram_user_id = ?
          AND role_code = ?
          AND scope_type = ?
          AND scope_value = ?
    """, (user_id, role_code, scope_type, scope_value))
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE access_user_roles
            SET is_active = 1, granted_by = ?, note = ?, updated_at = ?
            WHERE id = ?
        """, (granted_by, note or None, now_db(), int(existing[0])))
    else:
        cur.execute("""
            INSERT INTO access_user_roles (
                telegram_user_id, role_code, scope_type, scope_value,
                is_active, granted_by, note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (
            user_id, role_code, scope_type, scope_value,
            granted_by, note or None, now_db(), now_db(),
        ))


def set_permission(
    cur: sqlite3.Cursor,
    *,
    user_id: str,
    resource: str,
    action: str,
    scope_type: str,
    scope_value: str,
    effect: str,
    granted_by: str,
    note: str | None,
) -> None:
    effect = effect.upper()
    if effect not in {"ALLOW", "DENY"}:
        raise ValueError("effect должен быть ALLOW или DENY.")

    cur.execute("""
        SELECT id
        FROM access_user_permissions
        WHERE telegram_user_id = ?
          AND resource = ?
          AND action = ?
          AND scope_type = ?
          AND scope_value = ?
    """, (user_id, resource, action, scope_type, scope_value))
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE access_user_permissions
            SET effect = ?, is_active = 1, granted_by = ?, note = ?, updated_at = ?
            WHERE id = ?
        """, (effect, granted_by, note or None, now_db(), int(existing[0])))
    else:
        cur.execute("""
            INSERT INTO access_user_permissions (
                telegram_user_id, resource, action, scope_type, scope_value,
                effect, is_active, granted_by, note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (
            user_id, resource, action, scope_type, scope_value,
            effect, granted_by, note or None, now_db(), now_db(),
        ))


def write_manager_audit(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    action_type: str,
    details: str,
) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO access_audit_log (
            created_at, actor_telegram_user_id, action_type,
            resource, action, scope_type, scope_value,
            target_table, target_id, success, details
        )
        VALUES (?, 'LOCAL_ACCESS_MANAGER', ?, 'access_control', 'MANAGE',
                'ALL', '*', 'staff_principals', ?, 1, ?)
    """, (now_db(), action_type, user_id, details))

    if audit_log:
        audit_log(
            conn=conn,
            operator_id="LOCAL_ACCESS_MANAGER",
            user_id="LOCAL_ACCESS_MANAGER",
            actor_type="system",
            action_type=action_type,
            table_name="access_user_roles",
            row_id=user_id,
            field_name="access_control",
            old_value="",
            new_value=details,
            source_context="manage_staff_access.py",
            comment=details,
            commit=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roles", action="store_true")
    parser.add_argument("--show-user")
    parser.add_argument(
        "--sandbox",
        help="Необязательно: .db внутри Data\\db\\sandbox для безопасной проверки.",
    )
    parser.add_argument("--grant-role", nargs=2, metavar=("TELEGRAM_ID", "ROLE_CODE"))
    parser.add_argument("--grant", nargs=3, metavar=("TELEGRAM_ID", "RESOURCE", "ACTION"))
    parser.add_argument("--deny", nargs=3, metavar=("TELEGRAM_ID", "RESOURCE", "ACTION"))
    parser.add_argument("--scope", default="ALL:*")
    parser.add_argument("--name")
    parser.add_argument("--note")
    parser.add_argument("--granted-by", default="LOCAL_ACCESS_MANAGER")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.sandbox:
        db = Path(args.sandbox).resolve()
        try:
            db.relative_to(SANDBOX_DIR.resolve())
        except ValueError:
            raise SystemExit("Для --sandbox разрешены только БД внутри Data\\db\\sandbox.")
    else:
        db = get_db_file()

    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        ready, reason = schema_ready(conn)
        if not ready:
            raise SystemExit(reason)

        requested_writes = sum(bool(value) for value in [
            args.grant_role, args.grant, args.deny,
        ])
        if requested_writes > 1:
            raise SystemExit("Выберите только одно действие: --grant-role, --grant или --deny.")

        if args.roles:
            print_roles(conn)
            return

        if args.show_user:
            print_user(conn, args.show_user)
            return

        if not requested_writes:
            parser.print_help()
            return

        scope_type, scope_value = parse_scope(args.scope)
        cur = conn.cursor()

        if args.grant_role:
            user_id, role_code = args.grant_role
            ensure_principal(cur, str(user_id), args.name, args.note)
            grant_role(
                cur,
                user_id=str(user_id),
                role_code=role_code,
                scope_type=scope_type,
                scope_value=scope_value,
                granted_by=args.granted_by,
                note=args.note,
            )
            action_type = "access_role_granted"
            details = f"role={role_code}; scope={scope_type}:{scope_value}; note={args.note or '-'}"
            target = str(user_id)

        elif args.grant:
            user_id, resource, action = args.grant
            ensure_principal(cur, str(user_id), args.name, args.note)
            set_permission(
                cur,
                user_id=str(user_id),
                resource=resource,
                action=action,
                scope_type=scope_type,
                scope_value=scope_value,
                effect="ALLOW",
                granted_by=args.granted_by,
                note=args.note,
            )
            action_type = "access_permission_allowed"
            details = f"ALLOW {resource}.{action}; scope={scope_type}:{scope_value}; note={args.note or '-'}"
            target = str(user_id)

        else:
            user_id, resource, action = args.deny
            ensure_principal(cur, str(user_id), args.name, args.note)
            set_permission(
                cur,
                user_id=str(user_id),
                resource=resource,
                action=action,
                scope_type=scope_type,
                scope_value=scope_value,
                effect="DENY",
                granted_by=args.granted_by,
                note=args.note,
            )
            action_type = "access_permission_denied"
            details = f"DENY {resource}.{action}; scope={scope_type}:{scope_value}; note={args.note or '-'}"
            target = str(user_id)

        if args.apply:
            write_manager_audit(conn, user_id=target, action_type=action_type, details=details)
            conn.commit()
            print("APPLIED")
        else:
            conn.rollback()
            print("DRY RUN COMPLETED - NO CHANGES SAVED")

        print("Target Telegram ID:", target)
        print(details)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
