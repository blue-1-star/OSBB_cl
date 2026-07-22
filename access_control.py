"""
Гранулярные права доступа ОСББ.

Модель:
- роли задают обычный набор полномочий;
- access_user_permissions добавляет индивидуальные ALLOW / DENY;
- DENY конкретного пользователя имеет высший приоритет;
- каждое право проверяется с областью действия (scope):
  CASHBOX:O, POST:O, ENTRANCE:4, ALL:* и т.п.

Это прикладная авторизация для Telegram-бота. Она не даёт никому прямого
доступа к SQLite: каждый обработчик обязан вызвать has_permission() перед
просмотром и перед записью.
"""

from __future__ import annotations

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


ALL_SCOPE_TYPE = "ALL"
ALL_SCOPE_VALUE = "*"


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def norm_scope_type(value: Any) -> str:
    raw = text(value).upper()
    return raw or ALL_SCOPE_TYPE


def norm_scope_value(value: Any) -> str:
    raw = text(value).upper()
    return raw or ALL_SCOPE_VALUE


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def schema_ready(conn: sqlite3.Connection | None = None) -> tuple[bool, str]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        required = {
            "staff_principals",
            "access_roles",
            "access_role_permissions",
            "access_user_roles",
            "access_user_permissions",
            "access_audit_log",
        }
        missing = sorted(name for name in required if not table_exists(cur, name))
        if missing:
            return False, "Не установлена модель прав: " + ", ".join(missing)
        return True, ""
    finally:
        if owns:
            conn.close()


def scope_matches(
    granted_type: Any,
    granted_value: Any,
    requested_type: Any,
    requested_value: Any,
) -> bool:
    """
    True when a permission scope covers the requested scope.
    ALL:* covers everything. CASHBOX:* covers every cashbox.
    """
    g_type = norm_scope_type(granted_type)
    g_value = norm_scope_value(granted_value)
    r_type = norm_scope_type(requested_type)
    r_value = norm_scope_value(requested_value)

    if g_type in {ALL_SCOPE_TYPE, "*"}:
        return True
    if g_type != r_type:
        return False
    return g_value in {ALL_SCOPE_VALUE, "*"} or g_value == r_value


def _active_user_overrides(
    cur: sqlite3.Cursor,
    user_id: int | str,
    resource: str,
    action: str,
) -> list[dict]:
    cur.execute(
        """
        SELECT effect, scope_type, scope_value
        FROM access_user_permissions
        WHERE telegram_user_id = ?
          AND resource = ?
          AND action = ?
          AND is_active = 1
          AND (valid_from IS NULL OR valid_from <= ?)
          AND (valid_to IS NULL OR valid_to >= ?)
        """,
        (str(user_id), resource, action, now_db(), now_db()),
    )
    return [dict(row) for row in cur.fetchall()]


def _active_role_permissions(
    cur: sqlite3.Cursor,
    user_id: int | str,
    resource: str,
    action: str,
) -> list[dict]:
    cur.execute(
        """
        SELECT
            rp.effect,
            rp.scope_type AS permission_scope_type,
            rp.scope_value AS permission_scope_value,
            ur.scope_type AS assignment_scope_type,
            ur.scope_value AS assignment_scope_value,
            ur.role_code
        FROM access_user_roles ur
        JOIN access_roles r
          ON r.role_code = ur.role_code
         AND r.is_active = 1
        JOIN access_role_permissions rp
          ON rp.role_code = ur.role_code
         AND rp.is_active = 1
        WHERE ur.telegram_user_id = ?
          AND ur.is_active = 1
          AND rp.resource = ?
          AND rp.action = ?
          AND (ur.valid_from IS NULL OR ur.valid_from <= ?)
          AND (ur.valid_to IS NULL OR ur.valid_to >= ?)
        """,
        (str(user_id), resource, action, now_db(), now_db()),
    )
    return [dict(row) for row in cur.fetchall()]


def has_permission(
    user_id: int | str,
    resource: str,
    action: str,
    *,
    scope_type: str = ALL_SCOPE_TYPE,
    scope_value: str = ALL_SCOPE_VALUE,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """
    Permission precedence:
      1. direct active DENY -> deny;
      2. direct active ALLOW -> allow;
      3. role DENY -> deny;
      4. role ALLOW -> allow;
      5. no rule -> deny.

    Passing conn is useful for migration/preflight tests.
    """
    owns = conn is None
    conn = conn or get_conn()
    try:
        ready, _ = schema_ready(conn)
        if not ready:
            return False

        cur = conn.cursor()
        direct = [
            item for item in _active_user_overrides(cur, user_id, resource, action)
            if scope_matches(
                item["scope_type"], item["scope_value"], scope_type, scope_value
            )
        ]
        if any(text(item["effect"]).upper() == "DENY" for item in direct):
            return False
        if any(text(item["effect"]).upper() == "ALLOW" for item in direct):
            return True

        role_rows = [
            item for item in _active_role_permissions(cur, user_id, resource, action)
            if scope_matches(
                item["permission_scope_type"],
                item["permission_scope_value"],
                scope_type,
                scope_value,
            )
            and scope_matches(
                item["assignment_scope_type"],
                item["assignment_scope_value"],
                scope_type,
                scope_value,
            )
        ]
        if any(text(item["effect"]).upper() == "DENY" for item in role_rows):
            return False
        return any(text(item["effect"]).upper() == "ALLOW" for item in role_rows)
    finally:
        if owns:
            conn.close()


def has_guard_workspace_access(
    user_id: int | str,
    *,
    cashbox_code: str = "O",
    conn: sqlite3.Connection | None = None,
) -> bool:
    return has_permission(
        user_id,
        "guard_workspace",
        "ENTER",
        scope_type="CASHBOX",
        scope_value=cashbox_code,
        conn=conn,
    )


def list_user_roles(
    user_id: int | str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ready, _ = schema_ready(conn)
        if not ready:
            return []
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                ur.id, ur.role_code, r.role_name,
                ur.scope_type, ur.scope_value,
                ur.valid_from, ur.valid_to, ur.is_active,
                ur.note
            FROM access_user_roles ur
            LEFT JOIN access_roles r ON r.role_code = ur.role_code
            WHERE ur.telegram_user_id = ?
            ORDER BY ur.is_active DESC, ur.role_code, ur.scope_type, ur.scope_value
            """,
            (str(user_id),),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        if owns:
            conn.close()


def workspace_options(
    user_id: int | str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """
    The bot may later expose more workspaces. Current first workspace:
    guard post O. The result is permission-based, not ADMIN_IDS-based.
    """
    result = []
    if has_guard_workspace_access(user_id, cashbox_code="O", conn=conn):
        result.append({
            "workspace_code": "GUARD_O",
            "label": "🛡 Пост охраны O",
            "scope_type": "CASHBOX",
            "scope_value": "O",
        })
    return result


def write_access_audit(
    conn: sqlite3.Connection,
    *,
    actor_user_id: int | str,
    action_type: str,
    resource: str,
    action: str,
    scope_type: str,
    scope_value: str,
    target_table: str = "",
    target_id: int | str = "",
    success: bool = True,
    details: str = "",
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO access_audit_log (
            created_at, actor_telegram_user_id, action_type,
            resource, action, scope_type, scope_value,
            target_table, target_id, success, details
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_db(),
            str(actor_user_id),
            action_type,
            resource,
            action,
            norm_scope_type(scope_type),
            norm_scope_value(scope_value),
            target_table,
            str(target_id),
            1 if success else 0,
            details,
        ),
    )
    return int(cur.lastrowid)


def write_business_access_audit(
    conn: sqlite3.Connection,
    *,
    actor_user_id: int | str,
    action_type: str,
    resource: str,
    action: str,
    scope_type: str,
    scope_value: str,
    target_table: str = "",
    target_id: int | str = "",
    details: str = "",
) -> None:
    """
    Writes the security audit and, when available, mirrors key information to
    operator_audit_log. Caller controls transaction commit.
    """
    write_access_audit(
        conn,
        actor_user_id=actor_user_id,
        action_type=action_type,
        resource=resource,
        action=action,
        scope_type=scope_type,
        scope_value=scope_value,
        target_table=target_table,
        target_id=target_id,
        success=True,
        details=details,
    )
    if audit_log:
        audit_log(
            conn=conn,
            operator_id=str(actor_user_id),
            user_id=str(actor_user_id),
            actor_type="guard",
            action_type=action_type,
            table_name=target_table or resource,
            row_id=str(target_id or ""),
            field_name=f"{resource}.{action}",
            old_value="",
            new_value=f"{norm_scope_type(scope_type)}:{norm_scope_value(scope_value)}",
            source_context="guard_workspace",
            comment=details,
            extra={
                "resource": resource,
                "action": action,
                "scope_type": norm_scope_type(scope_type),
                "scope_value": norm_scope_value(scope_value),
            },
            commit=False,
        )
