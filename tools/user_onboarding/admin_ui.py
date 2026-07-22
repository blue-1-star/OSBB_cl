#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from tools.access_control.access_control import (
    ROLE_CODE_BY_SIMPLE_ROLE,
    grant_role_to_user,
    revoke_role_from_user,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

BTN_USERS_ROLES = "👥 Пользователи и роли"
BTN_NEW_RESIDENTS = "🆕 Новые жители"
BTN_CONFIRMED_RESIDENTS = "👤 Жильцы"
BTN_GUARDS = "👮 Охранники"
BTN_CASHIERS = "💰 Кассиры"
BTN_OPERATORS = "🛠 Операторы"
BTN_ADMINS = "⭐ Администраторы"
BTN_PERMISSIONS = "🔑 Права доступа"
BTN_PERMISSIONS_REFERENCE = "📖 Справочник возможностей"

BTN_BACK = "⬅ Назад"
BTN_BACK_TO_USERS = "⬅ К пользователям"
BTN_BACK_TO_ADMINS = "⬅ К администраторам"
BTN_BACK_TO_ROLE_LIST = "⬅ К списку роли"

BTN_CONFIRM = "✔ Подтвердить"
BTN_NEEDS_CLARIFICATION = "✏ Требует уточнения"
BTN_REJECT = "❌ Отклонить"

BTN_ADD_ADMIN = "➕ Добавить администратора"
BTN_ADD_GUARD = "➕ Добавить охранника"
BTN_ADD_CASHIER = "➕ Добавить кассира"
BTN_ADD_OPERATOR = "➕ Добавить оператора"
BTN_ADD_RESIDENT = "➕ Добавить жильца"

BTN_REFRESH = "🔄 Обновить"
BTN_DISABLE_ADMIN = "🚫 Отключить администратора"
BTN_ENABLE_ADMIN = "✅ Включить администратора"
BTN_DISABLE_USER = "🚫 Отключить доступ"
BTN_ENABLE_USER = "✅ Включить доступ"
BTN_CANCEL = "❌ Отмена"

ADMIN_BUTTON_RE = re.compile(r"^⭐\s*#(\d+)")
ACCESS_BUTTON_RE = re.compile(r"^(👤|👮|💰|🛠)\s*#(\d+)")

ROLE_META = {
    "resident": {
        "button": BTN_CONFIRMED_RESIDENTS,
        "title": "👤 Жильцы",
        "emoji": "👤",
        "add": BTN_ADD_RESIDENT,
        "back": BTN_BACK_TO_USERS,
    },
    "guard": {
        "button": BTN_GUARDS,
        "title": "👮 Охранники",
        "emoji": "👮",
        "add": BTN_ADD_GUARD,
        "back": BTN_BACK_TO_USERS,
    },
    "cashier": {
        "button": BTN_CASHIERS,
        "title": "💰 Кассиры",
        "emoji": "💰",
        "add": BTN_ADD_CASHIER,
        "back": BTN_BACK_TO_USERS,
    },
    "operator": {
        "button": BTN_OPERATORS,
        "title": "🛠 Операторы",
        "emoji": "🛠",
        "add": BTN_ADD_OPERATOR,
        "back": BTN_BACK_TO_USERS,
    },
}


def db_path() -> Path:
    return DEFAULT_DB


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row
    return con


def keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def users_roles_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([
        [BTN_NEW_RESIDENTS],
        [BTN_CONFIRMED_RESIDENTS, BTN_GUARDS],
        [BTN_CASHIERS, BTN_OPERATORS],
        [BTN_ADMINS],
        [BTN_PERMISSIONS, BTN_PERMISSIONS_REFERENCE],
        [BTN_BACK],
    ])


def pending_resident_keyboard(has_item: bool) -> ReplyKeyboardMarkup:
    if has_item:
        return keyboard([
            [BTN_CONFIRM],
            [BTN_NEEDS_CLARIFICATION, BTN_REJECT],
            [BTN_BACK_TO_USERS],
        ])
    return keyboard([[BTN_BACK_TO_USERS]])


def admins_keyboard(rows: list[sqlite3.Row]) -> ReplyKeyboardMarkup:
    buttons: list[list[str]] = []
    for row in rows:
        buttons.append([f"⭐ #{row['id']} {admin_display_name(row)}"])
    buttons.append([BTN_ADD_ADMIN])
    buttons.append([BTN_REFRESH, BTN_BACK_TO_USERS])
    return keyboard(buttons)


def admin_card_keyboard(row: sqlite3.Row) -> ReplyKeyboardMarkup:
    buttons: list[list[str]] = []
    role = str(row["role"] or "")
    is_active = int(row["is_active"] or 0) == 1
    if role != "super_admin":
        buttons.append([BTN_DISABLE_ADMIN if is_active else BTN_ENABLE_ADMIN])
    buttons.append([BTN_BACK_TO_ADMINS])
    return keyboard(buttons)


def add_admin_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([[BTN_CANCEL], [BTN_BACK_TO_ADMINS]])


def role_keyboard(role: str, rows: list[sqlite3.Row]) -> ReplyKeyboardMarkup:
    meta = ROLE_META[role]
    buttons: list[list[str]] = []
    for row in rows:
        buttons.append([f"{meta['emoji']} #{row['id']} {access_display_name(row)}"])
    buttons.append([meta["add"]])
    buttons.append([BTN_REFRESH, BTN_BACK_TO_USERS])
    return keyboard(buttons)


def access_card_keyboard(row: sqlite3.Row) -> ReplyKeyboardMarkup:
    is_active = str(row["status"] or "").upper() == "ACTIVE"
    buttons = [[BTN_DISABLE_USER if is_active else BTN_ENABLE_USER]]
    buttons.append([BTN_BACK_TO_ROLE_LIST])
    return keyboard(buttons)


def add_access_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([[BTN_CANCEL], [BTN_BACK_TO_ROLE_LIST]])


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def next_pending_request() -> sqlite3.Row | None:
    con = connect()
    try:
        if not table_exists(con, "resident_verification_requests"):
            return None
        return con.execute(
            """
            SELECT *
            FROM resident_verification_requests
            WHERE status='PENDING_ADMIN_CONFIRMATION'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()


def pending_count() -> int:
    con = connect()
    try:
        if not table_exists(con, "resident_verification_requests"):
            return 0
        row = con.execute(
            """
            SELECT COUNT(*) AS n
            FROM resident_verification_requests
            WHERE status='PENDING_ADMIN_CONFIRMATION'
            """
        ).fetchone()
        return int(row["n"])
    finally:
        con.close()


def access_role_count(role: str) -> int:
    con = connect()
    try:
        if not table_exists(con, "resident_access_accounts"):
            return 0
        row = con.execute(
            """
            SELECT COUNT(*) AS n
            FROM resident_access_accounts
            WHERE role=? AND status='ACTIVE'
            """,
            (role,),
        ).fetchone()
        return int(row["n"])
    finally:
        con.close()


def bot_admin_count() -> int:
    con = connect()
    try:
        if not table_exists(con, "bot_admins"):
            return 0
        row = con.execute(
            "SELECT COUNT(*) AS n FROM bot_admins WHERE COALESCE(is_active, 1)=1"
        ).fetchone()
        return int(row["n"])
    finally:
        con.close()


def list_bot_admins() -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "bot_admins"):
            return []
        return con.execute(
            """
            SELECT *
            FROM bot_admins
            ORDER BY
                CASE role WHEN 'super_admin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                COALESCE(is_active, 1) DESC,
                id
            """
        ).fetchall()
    finally:
        con.close()


def get_bot_admin(admin_id: int) -> sqlite3.Row | None:
    con = connect()
    try:
        if not table_exists(con, "bot_admins"):
            return None
        return con.execute("SELECT * FROM bot_admins WHERE id=?", (admin_id,)).fetchone()
    finally:
        con.close()


def list_access_accounts(role: str) -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "resident_access_accounts"):
            return []
        return con.execute(
            """
            SELECT *
            FROM resident_access_accounts
            WHERE role=?
            ORDER BY
                CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END,
                apartment_number,
                id
            """,
            (role,),
        ).fetchall()
    finally:
        con.close()


def get_access_account(account_id: int) -> sqlite3.Row | None:
    con = connect()
    try:
        if not table_exists(con, "resident_access_accounts"):
            return None
        return con.execute(
            "SELECT * FROM resident_access_accounts WHERE id=?",
            (account_id,),
        ).fetchone()
    finally:
        con.close()


def admin_display_name(row: sqlite3.Row) -> str:
    display = (row["display_name"] if "display_name" in row.keys() else None) or ""
    username = (row["telegram_username"] if "telegram_username" in row.keys() else None) or ""
    tg_id = row["telegram_user_id"] if "telegram_user_id" in row.keys() else ""
    if display:
        return str(display)
    if username:
        return "@" + str(username).lstrip("@")
    return str(tg_id)


def access_display_name(row: sqlite3.Row) -> str:
    username = (row["telegram_username"] if "telegram_username" in row.keys() else None) or ""
    tg_id = row["telegram_user_id"] if "telegram_user_id" in row.keys() else ""
    apartment = (row["apartment_number"] if "apartment_number" in row.keys() else None) or ""
    if username:
        base = "@" + str(username).lstrip("@")
    else:
        base = str(tg_id)
    return f"{base} кв.{apartment}" if apartment else base


def admin_status(row: sqlite3.Row) -> str:
    return "✅ активен" if int(row["is_active"] or 0) == 1 else "🚫 отключён"


def access_status(row: sqlite3.Row) -> str:
    return "✅ активен" if str(row["status"] or "").upper() == "ACTIVE" else "🚫 отключён"


def parse_tg_input(text: str) -> tuple[str, str | None, str | None]:
    """
    Accepted formats:
        1198872172
        1198872172 @username
        1198872172 @username Display Name
        1198872172 Display Name
    """
    parts = text.strip().split()
    if not parts:
        return "", None, None
    tg_id = parts[0].strip()
    username: str | None = None
    display_name: str | None = None
    rest = parts[1:]
    if rest and rest[0].startswith("@"):
        username = rest[0].lstrip("@")
        rest = rest[1:]
    if rest:
        display_name = " ".join(rest).strip()
    return tg_id, username, display_name


def format_admin_list(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "⭐ Администраторы\n\nАдминистраторы не найдены."

    lines = ["⭐ Администраторы", "", f"Всего: {len(rows)}", ""]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {admin_display_name(row)}")
        lines.append(f"   ID записи: {row['id']}")
        lines.append(f"   Telegram: {row['telegram_user_id'] or '—'}")
        lines.append(f"   Username: @{row['telegram_username'] or '—'}")
        lines.append(f"   Роль: {row['role'] or '—'}")
        lines.append(f"   {admin_status(row)}")
        lines.append("")
    lines.append("Нажмите кнопку администратора для карточки.")
    return "\n".join(lines).strip()


def format_admin_card(row: sqlite3.Row) -> str:
    rights = []
    for col, title in [
        ("can_read", "просмотр"),
        ("can_write", "изменение"),
        ("can_manage_users", "пользователи"),
        ("can_manage_payments", "платежи"),
        ("can_manage_bot", "бот"),
    ]:
        if col in row.keys():
            rights.append(("✅ " if int(row[col] or 0) == 1 else "☐ ") + title)
    rights_text = "\n".join(rights) if rights else "—"

    return (
        "⭐ Карточка администратора\n\n"
        f"Имя: {admin_display_name(row)}\n"
        f"Username: @{row['telegram_username'] or '—'}\n"
        f"Telegram ID: {row['telegram_user_id'] or '—'}\n"
        f"Роль: {row['role'] or '—'}\n"
        f"Статус: {admin_status(row)}\n"
        f"ID записи: {row['id']}\n\n"
        "Права:\n"
        f"{rights_text}\n\n"
        f"Заметка: {row['notes'] or '—'}"
    )


def format_access_list(role: str, rows: list[sqlite3.Row]) -> str:
    title = ROLE_META[role]["title"]
    if not rows:
        return f"{title}\n\nЗаписей нет."
    lines = [title, "", f"Всего: {len(rows)}", ""]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {access_display_name(row)}")
        lines.append(f"   ID записи: {row['id']}")
        lines.append(f"   Telegram: {row['telegram_user_id'] or '—'}")
        lines.append(f"   Username: @{row['telegram_username'] or '—'}")
        lines.append(f"   Квартира: {row['apartment_number'] or '—'}")
        lines.append(f"   {access_status(row)}")
        lines.append("")
    lines.append("Нажмите кнопку записи для карточки.")
    return "\n".join(lines).strip()


def format_access_card(row: sqlite3.Row) -> str:
    role = str(row["role"] or "")
    title = ROLE_META.get(role, {}).get("title", "👤 Пользователь")
    return (
        f"{title}\n\n"
        f"Имя: {access_display_name(row)}\n"
        f"Username: @{row['telegram_username'] or '—'}\n"
        f"Telegram ID: {row['telegram_user_id'] or '—'}\n"
        f"Квартира: {row['apartment_number'] or '—'}\n"
        f"Роль: {row['role'] or '—'}\n"
        f"Статус: {access_status(row)}\n"
        f"ID записи: {row['id']}\n\n"
        f"Заметка: {row['notes'] or '—'}"
    )


def format_request(row: sqlite3.Row | None) -> str:
    if row is None:
        return "🆕 Новые жители\n\nОчередь подтверждения пуста."
    return (
        "🆕 Новые жители\n\n"
        f"Заявка #{row['id']}\n"
        f"Квартира: {row['apartment_number'] or '—'}\n"
        f"ФИО: {row['claimed_full_name'] or '—'}\n"
        f"Телефон: {row['claimed_phone'] or '—'}\n"
        f"Telegram ID: {row['telegram_user_id'] or '—'}\n"
        f"Username: @{row['telegram_username'] or '—'}\n\n"
        "Выберите действие."
    )


def show_users_roles_text() -> str:
    return (
        "👥 Пользователи и роли\n\n"
        f"🆕 Новые жители: {pending_count()}\n"
        f"👤 Жильцы: {access_role_count('resident')}\n"
        f"👮 Охранники: {access_role_count('guard')}\n"
        f"💰 Кассиры: {access_role_count('cashier')}\n"
        f"🛠 Операторы: {access_role_count('operator')}\n"
        f"⭐ Администраторы: {bot_admin_count()}"
    )


async def show_users_roles(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "menu"}
    await update.message.reply_text(show_users_roles_text(), reply_markup=users_roles_keyboard())


async def show_pending_residents(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    row = next_pending_request()
    user_states[user_id] = {
        "mode": "user_onboarding_admin",
        "screen": "pending_residents",
        "request_id": row["id"] if row else None,
    }
    await update.message.reply_text(format_request(row), reply_markup=pending_resident_keyboard(row is not None))


async def show_admins(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    rows = list_bot_admins()
    user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "admins"}
    await update.message.reply_text(format_admin_list(rows), reply_markup=admins_keyboard(rows))


async def show_admin_card(update: Update, user_states: dict[int, Any], user_id: int, admin_id: int) -> None:
    row = get_bot_admin(admin_id)
    if row is None:
        await update.message.reply_text("⚠ Администратор не найден.")
        await show_admins(update, user_states, user_id)
        return
    user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "admin_card", "admin_id": admin_id}
    await update.message.reply_text(format_admin_card(row), reply_markup=admin_card_keyboard(row))


async def show_role_list(update: Update, user_states: dict[int, Any], user_id: int, role: str) -> None:
    rows = list_access_accounts(role)
    user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "role_list", "role": role}
    await update.message.reply_text(format_access_list(role, rows), reply_markup=role_keyboard(role, rows))


async def show_access_card(update: Update, user_states: dict[int, Any], user_id: int, account_id: int) -> None:
    row = get_access_account(account_id)
    if row is None:
        await update.message.reply_text("⚠ Запись не найдена.")
        state = user_states.get(user_id, {})
        await show_role_list(update, user_states, user_id, state.get("role", "guard"))
        return
    role = str(row["role"] or "guard")
    user_states[user_id] = {
        "mode": "user_onboarding_admin",
        "screen": "access_card",
        "role": role,
        "account_id": account_id,
    }
    await update.message.reply_text(format_access_card(row), reply_markup=access_card_keyboard(row))


def confirm_request(request_id: int, admin_id: int) -> tuple[bool, str]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM resident_verification_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return False, "Заявка не найдена."
        if row["status"] != "PENDING_ADMIN_CONFIRMATION":
            return False, f"Заявка уже не ожидает подтверждения: {row['status']}"

        con.execute(
            """
            INSERT OR REPLACE INTO resident_access_accounts
            (telegram_user_id, telegram_username, apartment_number, role, status,
             confirmed_by_admin_id, confirmed_at, notes)
            VALUES (?, ?, ?, 'resident', 'ACTIVE', ?, CURRENT_TIMESTAMP, ?)
            """,
            (
                row["telegram_user_id"], row["telegram_username"], row["apartment_number"],
                str(admin_id), f"Confirmed from verification request #{request_id}",
            ),
        )
        con.execute(
            """
            UPDATE resident_verification_requests
            SET status='APPROVED', reviewed_by_admin_id=?, reviewed_at=CURRENT_TIMESTAMP,
                review_note='Confirmed by admin'
            WHERE id=?
            """,
            (str(admin_id), request_id),
        )
        con.commit()
        return True, "Житель подтверждён."
    finally:
        con.close()


def create_clarification_task(request_id: int, admin_id: int, reject: bool = False) -> tuple[bool, str]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM resident_verification_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return False, "Заявка не найдена."
        status = "REJECTED" if reject else "NEEDS_CLARIFICATION"
        title = "Уточнить данные жителя" if not reject else "Заявка жителя отклонена"
        description = (
            f"Квартира: {row['apartment_number'] or '—'}; "
            f"ФИО: {row['claimed_full_name'] or '—'}; "
            f"Телефон: {row['claimed_phone'] or '—'}; "
            f"Telegram: {row['telegram_user_id'] or '—'}"
        )
        if not reject:
            con.execute(
                """
                INSERT INTO operator_task_queue
                (priority, task_type, status, apartment_number, telegram_user_id,
                 title, description, origin, created_by)
                VALUES ('HIGH', 'VERIFY_RESIDENT', 'PENDING', ?, ?, ?, ?, 'ADMIN_REVIEW', ?)
                """,
                (row["apartment_number"], row["telegram_user_id"], title, description, str(admin_id)),
            )
        con.execute(
            """
            UPDATE resident_verification_requests
            SET status=?, reviewed_by_admin_id=?, reviewed_at=CURRENT_TIMESTAMP, review_note=?
            WHERE id=?
            """,
            (status, str(admin_id), "Rejected by admin" if reject else "Sent to operator clarification", request_id),
        )
        con.commit()
        return True, "Заявка отклонена." if reject else "Создана задача оператору на уточнение данных."
    finally:
        con.close()


def add_admin_from_text(text: str, created_by: int) -> tuple[bool, str]:
    telegram_id, username, display_name = parse_tg_input(text)
    if not telegram_id.isdigit():
        return False, "Telegram ID должен состоять только из цифр."

    con = connect()
    try:
        if not table_exists(con, "bot_admins"):
            return False, "Таблица bot_admins не найдена."

        existing = con.execute("SELECT * FROM bot_admins WHERE telegram_user_id=?", (int(telegram_id),)).fetchone()
        if existing:
            con.execute(
                """
                UPDATE bot_admins
                SET is_active=1,
                    telegram_username=COALESCE(?, telegram_username),
                    display_name=COALESCE(?, display_name),
                    updated_at=CURRENT_TIMESTAMP,
                    notes=COALESCE(notes, '') || ' | Reactivated from admin UI'
                WHERE id=?
                """,
                (username, display_name, existing["id"]),
            )
            con.commit()
            return True, f"Администратор уже был в базе. Доступ активирован: #{existing['id']}."

        con.execute(
            """
            INSERT INTO bot_admins
            (telegram_user_id, telegram_username, display_name, role,
             can_read, can_write, can_manage_users, can_manage_payments, can_manage_bot,
             is_active, created_at, updated_at, notes)
            VALUES (?, ?, ?, 'admin', 1, 1, 1, 1, 0, 1,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            """,
            (int(telegram_id), username, display_name, f"Added from admin UI by {created_by}"),
        )
        con.commit()
        return True, "Новый администратор добавлен."
    finally:
        con.close()


def add_access_from_text(role: str, text: str, created_by: int) -> tuple[bool, str]:
    telegram_id, username, display_name = parse_tg_input(text)
    if not telegram_id.isdigit():
        return False, "Telegram ID должен состоять только из цифр."

    con = connect()
    try:
        if not table_exists(con, "resident_access_accounts"):
            return False, "Таблица resident_access_accounts не найдена."

        existing = con.execute(
            "SELECT * FROM resident_access_accounts WHERE telegram_user_id=?",
            (telegram_id,),
        ).fetchone()

        note_name = f"; name={display_name}" if display_name else ""
        if existing:
            con.execute(
                """
                UPDATE resident_access_accounts
                SET role=?,
                    status='ACTIVE',
                    telegram_username=COALESCE(?, telegram_username),
                    notes=COALESCE(notes, '') || ?,
                    confirmed_by_admin_id=?,
                    confirmed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (role, username, f" | Role set to {role} from admin UI{note_name}", str(created_by), existing["id"]),
            )
            con.commit()
            mapped_role = ROLE_CODE_BY_SIMPLE_ROLE.get(role)
            if mapped_role:
                grant_role_to_user(
                    telegram_id,
                    mapped_role,
                    granted_by=created_by,
                    note=f"Bridge update from resident_access_accounts role={role}",
                )
                return True, f"Запись уже была в базе. Роль обновлена: {role}; access role: {mapped_role}."
            return True, f"Запись уже была в базе. Роль обновлена: {role}."

        con.execute(
            """
            INSERT INTO resident_access_accounts
            (telegram_user_id, telegram_username, apartment_number, role, status,
             confirmed_by_admin_id, confirmed_at, created_at, notes)
            VALUES (?, ?, NULL, ?, 'ACTIVE', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            """,
            (telegram_id, username, role, str(created_by), f"Added from admin UI by {created_by}{note_name}"),
        )
        con.commit()
        mapped_role = ROLE_CODE_BY_SIMPLE_ROLE.get(role)
        if mapped_role:
            grant_role_to_user(
                telegram_id,
                mapped_role,
                granted_by=created_by,
                note=f"Bridge from resident_access_accounts role={role}",
            )
            return True, f"Пользователь добавлен. Роль доступа: {mapped_role}."
        return True, "Пользователь добавлен."
    finally:
        con.close()


def set_admin_active(admin_id: int, active: bool, current_user_id: int) -> tuple[bool, str]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM bot_admins WHERE id=?", (admin_id,)).fetchone()
        if row is None:
            return False, "Администратор не найден."
        if str(row["telegram_user_id"]) == str(current_user_id) and not active:
            return False, "Нельзя отключить самого себя."
        if row["role"] == "super_admin" and not active:
            return False, "Super admin не отключается из Telegram-интерфейса."
        con.execute(
            "UPDATE bot_admins SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (1 if active else 0, admin_id),
        )
        con.commit()
        return True, "Администратор включён." if active else "Администратор отключён."
    finally:
        con.close()


def set_access_active(account_id: int, active: bool) -> tuple[bool, str]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM resident_access_accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            return False, "Запись не найдена."
        con.execute(
            """
            UPDATE resident_access_accounts
            SET status=?, confirmed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            ("ACTIVE" if active else "DISABLED", account_id),
        )
        con.commit()
        mapped_role = ROLE_CODE_BY_SIMPLE_ROLE.get(row["role"])
        if mapped_role:
            if active:
                grant_role_to_user(
                    row["telegram_user_id"],
                    mapped_role,
                    granted_by="admin_ui",
                    note="Access re-enabled from admin UI",
                )
            else:
                revoke_role_from_user(row["telegram_user_id"], mapped_role)
        return True, "Доступ включён." if active else "Доступ отключён."
    finally:
        con.close()


def access_roles() -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "access_roles"):
            return []
        return con.execute(
            "SELECT * FROM access_roles WHERE COALESCE(is_active, 1)=1 ORDER BY role_code"
        ).fetchall()
    finally:
        con.close()


def permissions_roles_keyboard() -> ReplyKeyboardMarkup:
    rows = access_roles()
    buttons: list[list[str]] = [[f"🔑 {r['role_code']}"] for r in rows]
    buttons.append([BTN_PERMISSIONS_REFERENCE])
    buttons.append([BTN_BACK_TO_USERS])
    return keyboard(buttons)


def list_role_rules(role_code: str) -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "access_role_permissions"):
            return []
        return con.execute(
            """
            SELECT *
            FROM access_role_permissions
            WHERE role_code=?
            ORDER BY resource, action, scope_type, scope_value
            """,
            (role_code,),
        ).fetchall()
    finally:
        con.close()


def role_rules_keyboard(role_code: str, rows: list[sqlite3.Row]) -> ReplyKeyboardMarkup:
    buttons: list[list[str]] = []
    for row in rows:
        mark = "✅" if int(row["is_active"] or 0) == 1 and row["effect"] == "ALLOW" else "☐"
        buttons.append([f"{mark} #{row['id']} {row['resource']}.{row['action']}"])
    buttons.append([BTN_BACK_TO_USERS])
    return keyboard(buttons)


def format_permissions_menu() -> str:
    rows = access_roles()
    if not rows:
        return "🔑 Права доступа\n\nРоли access_roles не найдены."
    lines = ["🔑 Права доступа", "", "Выберите роль:"]
    for row in rows:
        lines.append(f"{row['role_code']} — {row['role_name']}")
    return "\n".join(lines)


def format_role_rules(role_code: str, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return f"🔑 {role_code}\n\nПравил пока нет."
    lines = [f"🔑 {role_code}", ""]
    current_resource = None
    for row in rows:
        resource = row["resource"]
        if resource != current_resource:
            current_resource = resource
            lines.append(f"— {resource} —")
        mark = "✅" if int(row["is_active"] or 0) == 1 and row["effect"] == "ALLOW" else "☐"
        scope = "" if row["scope_type"] == "ALL" else f" [{row['scope_type']}={row['scope_value']}]"
        lines.append(f"{mark} #{row['id']} {row['action']}{scope}")
    lines.append("")
    lines.append("Нажмите правило, чтобы включить/выключить.")
    return "\n".join(lines)


def toggle_role_rule(rule_id: int) -> tuple[bool, str]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM access_role_permissions WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            return False, "Правило не найдено."
        new_active = 0 if int(row["is_active"] or 0) == 1 else 1
        con.execute(
            "UPDATE access_role_permissions SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_active, rule_id),
        )
        con.commit()
        return True, ("Правило включено: " if new_active else "Правило выключено: ") + f"{row['resource']}.{row['action']}"
    finally:
        con.close()


def format_permissions_reference() -> str:
    con = connect()
    try:
        if not table_exists(con, "access_permissions"):
            return "📖 Справочник возможностей\n\nТаблица access_permissions не найдена."
        rows = con.execute(
            """
            SELECT permission_code, permission_name, category, description
            FROM access_permissions
            WHERE COALESCE(is_active, 1)=1
            ORDER BY category, permission_code
            """
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return (
            "📖 Справочник возможностей\n\n"
            "Справочник пуст.\n"
            "Выполните:\n"
            "python .\\OSBB\\tools\\access_control\\seed_access_permissions_reference.py --apply"
        )

    lines = ["📖 Справочник возможностей", ""]
    current_category = None
    for row in rows:
        category = row["category"] or "Без категории"
        if category != current_category:
            current_category = category
            lines.append(f"— {category} —")
        lines.append(row["permission_code"])
        lines.append(row["permission_name"])
        if row["description"]:
            lines.append(row["description"])
        lines.append("")
    return "\n".join(lines).strip()


def role_from_button(text: str) -> str | None:
    for role, meta in ROLE_META.items():
        if text == meta["button"]:
            return role
    return None


def role_from_add_button(text: str) -> str | None:
    for role, meta in ROLE_META.items():
        if text == meta["add"]:
            return role
    return None


async def handle_user_onboarding_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user_states: dict[int, Any], user_id: int) -> bool:
    text = (update.message.text or "").strip()
    state = user_states.get(user_id, {})

    if state.get("mode") != "user_onboarding_admin" and text != BTN_USERS_ROLES:
        return False

    if text == BTN_USERS_ROLES:
        await show_users_roles(update, user_states, user_id)
        return True

    if text == BTN_NEW_RESIDENTS:
        await show_pending_residents(update, user_states, user_id)
        return True

    if text == BTN_ADMINS:
        await show_admins(update, user_states, user_id)
        return True


    if text == BTN_PERMISSIONS:
        user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "permissions_menu"}
        await update.message.reply_text(format_permissions_menu(), reply_markup=permissions_roles_keyboard())
        return True

    if text == BTN_PERMISSIONS_REFERENCE:
        user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "permissions_reference"}
        await update.message.reply_text(format_permissions_reference(), reply_markup=permissions_roles_keyboard())
        return True

    if text.startswith("🔑 "):
        role_code = text.replace("🔑", "", 1).strip()
        rows = list_role_rules(role_code)
        user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "role_rules", "role_code": role_code}
        await update.message.reply_text(format_role_rules(role_code, rows), reply_markup=role_rules_keyboard(role_code, rows))
        return True

    role = role_from_button(text)
    if role:
        await show_role_list(update, user_states, user_id, role)
        return True

    if text == BTN_BACK_TO_USERS:
        await show_users_roles(update, user_states, user_id)
        return True

    if text == BTN_BACK_TO_ADMINS:
        await show_admins(update, user_states, user_id)
        return True

    if text == BTN_BACK_TO_ROLE_LIST:
        await show_role_list(update, user_states, user_id, state.get("role", "guard"))
        return True

    if text == BTN_REFRESH:
        if state.get("screen") == "admins":
            await show_admins(update, user_states, user_id)
        elif state.get("screen") in {"role_list", "access_card"}:
            await show_role_list(update, user_states, user_id, state.get("role", "guard"))
        else:
            await show_users_roles(update, user_states, user_id)
        return True

    if text == BTN_BACK:
        user_states[user_id] = {}
        await update.message.reply_text("Возврат в меню.")
        return True

    m = ADMIN_BUTTON_RE.match(text)
    if m:
        await show_admin_card(update, user_states, user_id, int(m.group(1)))
        return True

    m = ACCESS_BUTTON_RE.match(text)
    if m:
        await show_access_card(update, user_states, user_id, int(m.group(2)))
        return True

    if text == BTN_ADD_ADMIN:
        user_states[user_id] = {"mode": "user_onboarding_admin", "screen": "add_admin_wait_id"}
        await update.message.reply_text(
            "➕ Добавить администратора\n\n"
            "Введите Telegram ID.\n\n"
            "Можно также указать username и имя:\n"
            "1198872172 @username Виктория",
            reply_markup=add_admin_keyboard(),
        )
        return True

    add_role = role_from_add_button(text)
    if add_role:
        user_states[user_id] = {
            "mode": "user_onboarding_admin",
            "screen": "add_access_wait_id",
            "role": add_role,
        }
        await update.message.reply_text(
            f"{ROLE_META[add_role]['add']}\n\n"
            "Введите Telegram ID.\n\n"
            "Можно также указать username и имя:\n"
            "1198872172 @username Виктория",
            reply_markup=add_access_keyboard(),
        )
        return True

    if state.get("screen") == "add_admin_wait_id":
        if text in {BTN_CANCEL, BTN_BACK_TO_ADMINS}:
            await show_admins(update, user_states, user_id)
            return True
        ok, msg = add_admin_from_text(text, user_id)
        await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
        await show_admins(update, user_states, user_id)
        return True

    if state.get("screen") == "add_access_wait_id":
        role = state.get("role", "guard")
        if text in {BTN_CANCEL, BTN_BACK_TO_ROLE_LIST}:
            await show_role_list(update, user_states, user_id, role)
            return True
        ok, msg = add_access_from_text(role, text, user_id)
        await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
        await show_role_list(update, user_states, user_id, role)
        return True

    if state.get("screen") == "admin_card":
        admin_id = int(state.get("admin_id") or 0)
        if text == BTN_DISABLE_ADMIN:
            ok, msg = set_admin_active(admin_id, False, user_id)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_admin_card(update, user_states, user_id, admin_id)
            return True
        if text == BTN_ENABLE_ADMIN:
            ok, msg = set_admin_active(admin_id, True, user_id)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_admin_card(update, user_states, user_id, admin_id)
            return True

    if state.get("screen") == "access_card":
        account_id = int(state.get("account_id") or 0)
        role = state.get("role", "guard")
        if text == BTN_DISABLE_USER:
            ok, msg = set_access_active(account_id, False)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_access_card(update, user_states, user_id, account_id)
            return True
        if text == BTN_ENABLE_USER:
            ok, msg = set_access_active(account_id, True)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_access_card(update, user_states, user_id, account_id)
            return True
        if text == BTN_BACK_TO_ROLE_LIST:
            await show_role_list(update, user_states, user_id, role)
            return True

    if state.get("screen") == "pending_residents":
        request_id = state.get("request_id")
        if not request_id:
            await show_pending_residents(update, user_states, user_id)
            return True

        if text == BTN_CONFIRM:
            ok, msg = confirm_request(int(request_id), user_id)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_pending_residents(update, user_states, user_id)
            return True

        if text == BTN_NEEDS_CLARIFICATION:
            ok, msg = create_clarification_task(int(request_id), user_id, reject=False)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_pending_residents(update, user_states, user_id)
            return True

        if text == BTN_REJECT:
            ok, msg = create_clarification_task(int(request_id), user_id, reject=True)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_pending_residents(update, user_states, user_id)
            return True


    if state.get("screen") == "role_rules":
        role_code = state.get("role_code", "")
        m_rule = re.match(r"^(✅|☐)\s*#(\d+)", text)
        if m_rule:
            ok, msg = toggle_role_rule(int(m_rule.group(2)))
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            rows = list_role_rules(role_code)
            await update.message.reply_text(format_role_rules(role_code, rows), reply_markup=role_rules_keyboard(role_code, rows))
            return True

    return False
