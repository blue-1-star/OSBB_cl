"""Authoritative resident access workspace for the Streamlit admin console.

The page uses ``resident_accounts`` for people and ``access_user_roles`` for
permissions.  It deliberately does not use the legacy
``resident_access_accounts`` table for resident decisions.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin_console.utils.db import get_conn


SELF_SERVICE_ROLE = "RESIDENT_SELF_SERVICE"
ACTOR = "admin_console/user_access"

st.set_page_config(page_title="Пользователи и доступ", page_icon="👥", layout="wide")
st.title("👥 Пользователи и доступ")
st.caption("Жители — из resident_accounts; права — из access_user_roles. Прямое редактирование реестров жителю не выдаётся.")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def dashboard_counts() -> dict[str, int]:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status='apartment_confirmed' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status<>'apartment_confirmed' OR status IS NULL THEN 1 ELSE 0 END)
            FROM resident_accounts
            """
        ).fetchone()
        self_service = conn.execute(
            "SELECT COUNT(*) FROM access_user_roles WHERE role_code=? AND is_active=1",
            (SELF_SERVICE_ROLE,),
        ).fetchone()[0]
        legacy = conn.execute(
            "SELECT COUNT(*) FROM resident_access_accounts WHERE status='ACTIVE'"
        ).fetchone()[0] if table_exists(conn, "resident_access_accounts") else 0
        return {
            "confirmed": int(row[0] or 0),
            "unconfirmed": int(row[1] or 0),
            "self_service": int(self_service or 0),
            "legacy": int(legacy or 0),
        }
    finally:
        conn.close()


def residents(confirmed: bool) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT r.id, r.telegram_user_id, r.telegram_username, r.telegram_first_name,
                   r.telegram_last_name, r.apartment_number, r.status, r.created_at,
                   r.last_seen_at, COALESCE(ur.is_active, 0) AS self_service_active
            FROM resident_accounts r
            LEFT JOIN access_user_roles ur
              ON ur.telegram_user_id=CAST(r.telegram_user_id AS TEXT)
             AND ur.role_code=? AND ur.scope_type='APARTMENT'
             AND ur.scope_value=CAST(r.apartment_number AS TEXT)
            WHERE """ + ("r.status='apartment_confirmed'" if confirmed else "r.status<>'apartment_confirmed' OR r.status IS NULL") + """
            ORDER BY r.apartment_number, r.id
            """,
            (SELF_SERVICE_ROLE,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def set_self_service_access(resident: dict, enabled: bool, note: str) -> None:
    apartment = str(resident.get("apartment_number") or "").strip()
    if resident.get("status") != "apartment_confirmed" or not apartment:
        raise RuntimeError("Доступ выдаётся только жителю с подтверждённой квартирой.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        role = cur.execute(
            "SELECT 1 FROM access_roles WHERE role_code=? AND is_active=1", (SELF_SERVICE_ROLE,)
        ).fetchone()
        rules = cur.execute(
            "SELECT COUNT(*) FROM access_role_permissions WHERE role_code=? AND is_active=1 AND effect='ALLOW'",
            (SELF_SERVICE_ROLE,),
        ).fetchone()[0]
        if not role or not rules:
            raise RuntimeError("Роль Self Service или её правила не подготовлены в БД.")
        existing = cur.execute(
            """
            SELECT is_active FROM access_user_roles
            WHERE telegram_user_id=? AND role_code=? AND scope_type='APARTMENT' AND scope_value=?
            """,
            (str(resident["telegram_user_id"]), SELF_SERVICE_ROLE, apartment),
        ).fetchone()
        previous = int(existing[0]) if existing else None
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO access_user_roles(
                telegram_user_id, role_code, scope_type, scope_value, is_active,
                valid_from, valid_to, granted_by, note, created_at, updated_at
            ) VALUES (?, ?, 'APARTMENT', ?, ?, ?, CASE WHEN ?=1 THEN NULL ELSE ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value) DO UPDATE SET
                is_active=excluded.is_active,
                valid_from=CASE WHEN excluded.is_active=1 THEN excluded.valid_from ELSE access_user_roles.valid_from END,
                valid_to=excluded.valid_to,
                granted_by=excluded.granted_by, note=excluded.note, updated_at=excluded.updated_at
            """,
            (
                str(resident["telegram_user_id"]), SELF_SERVICE_ROLE, apartment, int(enabled), timestamp,
                int(enabled), timestamp, ACTOR, note.strip() or ("Выдано через Streamlit" if enabled else "Приостановлено через Streamlit"), timestamp, timestamp,
            ),
        )
        cur.execute(
            """
            INSERT INTO access_audit_log(
                created_at, actor_telegram_user_id, action_type, resource, action,
                scope_type, scope_value, target_table, target_id, success, details
            ) VALUES (?, 'operator', ?, 'access_user_roles', ?, 'APARTMENT', ?,
                      'resident_accounts', ?, 1, ?)
            """,
            (
                timestamp,
                "resident_self_service_granted" if enabled else "resident_self_service_suspended",
                "GRANT" if enabled else "REVOKE", apartment, str(resident["id"]),
                f"{SELF_SERVICE_ROLE}; previous_active={previous}; telegram={resident['telegram_user_id']}; {note.strip()}",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


counts = dashboard_counts()
metric1, metric2, metric3, metric4 = st.columns(4)
metric1.metric("Подтверждённые жители", counts["confirmed"])
metric2.metric("Без подтверждённой квартиры", counts["unconfirmed"])
metric3.metric("Self Service активен", counts["self_service"])
metric4.metric("Legacy-записей доступа", counts["legacy"])

tab_residents, tab_roles, tab_audit = st.tabs(["🪪 Жители и Self Service", "🔑 Роли", "📜 Аудит"])

with tab_residents:
    confirmed = residents(True)
    st.subheader("Подтверждённые жители")
    if not confirmed:
        st.info("Подтверждённых жителей пока нет.")
    else:
        table = pd.DataFrame([
            {
                "ID": row["id"], "Квартира": row["apartment_number"],
                "Житель": " ".join(part for part in [row["telegram_first_name"], row["telegram_last_name"]] if part) or row["telegram_username"] or "—",
                "Telegram ID": row["telegram_user_id"], "Self Service": "✅ выдан" if row["self_service_active"] else "○ не выдан",
                "Последний вход": row["last_seen_at"] or "—",
            }
            for row in confirmed
        ])
        st.dataframe(table, hide_index=True, use_container_width=True)
        choices = {row["id"]: f"кв. {row['apartment_number']} · " + (" ".join(part for part in [row['telegram_first_name'], row['telegram_last_name']] if part) or row['telegram_username'] or str(row['telegram_user_id'])) for row in confirmed}
        selected_id = st.selectbox("Открыть карточку жителя", list(choices), format_func=choices.get)
        resident = next(row for row in confirmed if row["id"] == selected_id)
        st.markdown("#### Карточка доступа")
        st.write(f"**Telegram ID:** {resident['telegram_user_id']}  ")
        st.write(f"**Квартира:** {resident['apartment_number']}  ")
        st.write(f"**Роль:** `{SELF_SERVICE_ROLE}` · **область:** `APARTMENT / {resident['apartment_number']}`")
        st.write(f"**Статус:** {'✅ доступ выдан' if resident['self_service_active'] else '○ доступ не выдан'}")
        st.caption("Роль позволяет только просматривать свою квартиру и отправлять заявки оператору. Прямого редактирования данных и финансов нет.")
        note = st.text_input("Комментарий к действию", key=f"access_note_{selected_id}")
        confirmation = st.checkbox(
            f"Подтверждаю {'приостановку' if resident['self_service_active'] else 'выдачу'} доступа только к кв. {resident['apartment_number']}",
            key=f"access_confirm_{selected_id}",
        )
        action_label = "🚫 Приостановить Self Service" if resident["self_service_active"] else "✅ Выдать Self Service"
        if st.button(action_label, type="primary", disabled=not confirmation):
            try:
                set_self_service_access(resident, not bool(resident["self_service_active"]), note)
                st.success("Доступ обновлён и записан в аудит.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

    st.subheader("Регистрации без подтверждённой квартиры")
    unconfirmed = residents(False)
    if unconfirmed:
        st.dataframe(pd.DataFrame([
            {
                "ID": row["id"], "Житель": " ".join(part for part in [row["telegram_first_name"], row["telegram_last_name"]] if part) or row["telegram_username"] or "—",
                "Telegram ID": row["telegram_user_id"], "Квартира": row["apartment_number"] or "—",
                "Статус": row["status"] or "—", "Зарегистрирован": row["created_at"] or "—",
            }
            for row in unconfirmed
        ]), hide_index=True, use_container_width=True)
    else:
        st.success("Нет регистраций без подтверждённой квартиры.")

with tab_roles:
    st.subheader("Справочник ролей")
    conn = get_conn()
    try:
        roles = pd.read_sql_query(
            "SELECT role_code AS 'Код', role_name AS 'Роль', description AS 'Описание', is_active AS 'Активна' FROM access_roles ORDER BY role_code",
            conn,
        )
        permissions = pd.read_sql_query(
            """
            SELECT role_code AS 'Роль', resource AS 'Ресурс', action AS 'Действие',
                   scope_type AS 'Область', scope_value AS 'Значение', effect AS 'Эффект', is_active AS 'Активно'
            FROM access_role_permissions ORDER BY role_code, resource, action
            """,
            conn,
        )
    finally:
        conn.close()
    st.dataframe(roles, hide_index=True, use_container_width=True)
    with st.expander("Правила ролей — только просмотр"):
        st.dataframe(permissions, hide_index=True, use_container_width=True)
    st.info("Изменение глобальных правил роли влияет на всех пользователей. В этом экране оно намеренно отключено; здесь управляется персональное назначение Self Service.")

with tab_audit:
    conn = get_conn()
    try:
        audit_rows = pd.read_sql_query(
            """
            SELECT created_at AS 'Время', actor_telegram_user_id AS 'Кто', action_type AS 'Событие',
                   scope_type || ' / ' || scope_value AS 'Область', target_id AS 'Профиль', details AS 'Детали'
            FROM access_audit_log
            ORDER BY id DESC LIMIT 200
            """,
            conn,
        )
    finally:
        conn.close()
    st.dataframe(audit_rows, hide_index=True, use_container_width=True)
