#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

# Bridge from current Telegram UI roles to existing Access Control role codes.
ROLE_CODE_BY_SIMPLE_ROLE = {
    "resident": "RESIDENT",
    "guard": "GUARD_O",
    "cashier": "FINANCE_OPERATOR",
    "operator": "ACCESS_MANAGER",
    "admin": "ACCESS_MANAGER",
    "super_admin": "SUPER_ADMIN",
}

ROLE_TITLES = {
    "RESIDENT": "Жилец",
    "GUARD_O": "Охранник / пост O",
    "CONCIERGE_K": "Консьерж / пост K",
    "FINANCE_OPERATOR": "Кассир / финансовый оператор",
    "ACCESS_MANAGER": "Оператор / менеджер доступа",
    "AUDITOR": "Аудитор",
    "REMOTE_SERVICE_OPERATOR": "Оператор пультов",
    "ACCESS_SERVICE_OPERATOR": "Оператор доступа",
    "SERVICE_OPERATOR_SANDBOX": "Оператор услуг sandbox",
    "SUPER_ADMIN": "Супер-администратор",
}


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path or DEFAULT_DB))
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_access_role(
    con: sqlite3.Connection,
    role_code: str,
    role_name: str | None = None,
    description: str | None = None,
) -> None:
    if not table_exists(con, "access_roles"):
        return

    con.execute(
        """
        INSERT INTO access_roles(role_code, role_name, description, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(role_code) DO UPDATE SET
            role_name=COALESCE(access_roles.role_name, excluded.role_name),
            is_active=1,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            role_code,
            role_name or ROLE_TITLES.get(role_code, role_code),
            description or "Ensured by Access Control v2 bridge",
        ),
    )


def grant_role_to_user(
    telegram_user_id: int | str,
    role_code: str,
    granted_by: int | str | None = None,
    scope_type: str = "ALL",
    scope_value: str = "*",
    note: str | None = None,
    db_path: Path | None = None,
) -> None:
    con = connect(db_path)
    try:
        ensure_access_role(con, role_code)
        if not table_exists(con, "access_user_roles"):
            return

        con.execute(
            """
            INSERT INTO access_user_roles
            (telegram_user_id, role_code, scope_type, scope_value, is_active,
             valid_from, valid_to, granted_by, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, NULL, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value) DO UPDATE SET
                is_active=1,
                valid_to=NULL,
                granted_by=excluded.granted_by,
                note=COALESCE(excluded.note, access_user_roles.note),
                updated_at=CURRENT_TIMESTAMP
            """,
            (str(telegram_user_id), role_code, scope_type, scope_value, str(granted_by or ""), note),
        )
        con.commit()
    finally:
        con.close()


def revoke_role_from_user(
    telegram_user_id: int | str,
    role_code: str,
    scope_type: str = "ALL",
    scope_value: str = "*",
    db_path: Path | None = None,
) -> None:
    con = connect(db_path)
    try:
        if not table_exists(con, "access_user_roles"):
            return

        con.execute(
            """
            UPDATE access_user_roles
            SET is_active=0, valid_to=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE telegram_user_id=? AND role_code=? AND scope_type=? AND scope_value=?
            """,
            (str(telegram_user_id), role_code, scope_type, scope_value),
        )
        con.commit()
    finally:
        con.close()


def user_role_codes(con: sqlite3.Connection, telegram_user_id: int | str) -> list[str]:
    uid = str(telegram_user_id)
    roles: list[str] = []

    if table_exists(con, "bot_admins"):
        row = con.execute(
            "SELECT role FROM bot_admins WHERE telegram_user_id=? AND COALESCE(is_active, 1)=1",
            (int(uid),) if uid.isdigit() else (uid,),
        ).fetchone()
        if row:
            if row["role"] == "super_admin":
                roles.append("SUPER_ADMIN")
            elif row["role"] == "admin":
                roles.append("ACCESS_MANAGER")

    if table_exists(con, "resident_access_accounts"):
        row = con.execute(
            "SELECT role FROM resident_access_accounts WHERE telegram_user_id=? AND status='ACTIVE'",
            (uid,),
        ).fetchone()
        if row:
            mapped = ROLE_CODE_BY_SIMPLE_ROLE.get(str(row["role"] or ""))
            if mapped:
                roles.append(mapped)

    if table_exists(con, "access_user_roles"):
        rows = con.execute(
            """
            SELECT role_code
            FROM access_user_roles
            WHERE telegram_user_id=? AND is_active=1
              AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP)
            """,
            (uid,),
        ).fetchall()
        roles.extend([r["role_code"] for r in rows])

    out: list[str] = []
    for role in roles:
        if role and role not in out:
            out.append(role)
    return out


def _scope_condition(scope_type: str, scope_value: str) -> str:
    # If caller does not request a specific scope, any active rule for resource/action may match.
    if scope_type == "ALL" and scope_value == "*":
        return "1=1"
    return "(scope_type='ALL' OR (scope_type=? AND scope_value IN (?, '*')))"


def _scope_params(scope_type: str, scope_value: str) -> tuple:
    if scope_type == "ALL" and scope_value == "*":
        return ()
    return (scope_type, scope_value)


def has_permission(
    telegram_user_id: int | str,
    resource: str,
    action: str,
    scope_type: str = "ALL",
    scope_value: str = "*",
    db_path: Path | None = None,
) -> bool:
    con = connect(db_path)
    try:
        roles = user_role_codes(con, telegram_user_id)
        if "SUPER_ADMIN" in roles:
            return True

        scope_sql = _scope_condition(scope_type, scope_value)
        scope_params = _scope_params(scope_type, scope_value)

        if table_exists(con, "access_user_permissions"):
            deny = con.execute(
                f"""
                SELECT 1 FROM access_user_permissions
                WHERE telegram_user_id=? AND resource=? AND action=?
                  AND is_active=1 AND effect='DENY'
                  AND {scope_sql}
                LIMIT 1
                """,
                (str(telegram_user_id), resource, action, *scope_params),
            ).fetchone()
            if deny:
                return False

            allow = con.execute(
                f"""
                SELECT 1 FROM access_user_permissions
                WHERE telegram_user_id=? AND resource=? AND action=?
                  AND is_active=1 AND effect='ALLOW'
                  AND {scope_sql}
                LIMIT 1
                """,
                (str(telegram_user_id), resource, action, *scope_params),
            ).fetchone()
            if allow:
                return True

        if not roles or not table_exists(con, "access_role_permissions"):
            return False

        placeholders = ",".join(["?"] * len(roles))

        deny = con.execute(
            f"""
            SELECT 1 FROM access_role_permissions
            WHERE role_code IN ({placeholders}) AND resource=? AND action=?
              AND is_active=1 AND effect='DENY'
              AND {scope_sql}
            LIMIT 1
            """,
            (*roles, resource, action, *scope_params),
        ).fetchone()
        if deny:
            return False

        allow = con.execute(
            f"""
            SELECT 1 FROM access_role_permissions
            WHERE role_code IN ({placeholders}) AND resource=? AND action=?
              AND is_active=1 AND effect='ALLOW'
              AND {scope_sql}
            LIMIT 1
            """,
            (*roles, resource, action, *scope_params),
        ).fetchone()
        return allow is not None
    finally:
        con.close()
