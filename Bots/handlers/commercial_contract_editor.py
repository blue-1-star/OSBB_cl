"""
Операторский редактор индивидуальных договоров коммерческих помещений.

Встраивается в handlers/unit_registry_editor.py.  Работает только в
изолированном state редактора помещений (_module == 'unit_registry').

Что делает:
- создаёт и редактирует договоры КП;
- ведёт индивидуальные условия/ставки;
- привязывает Telegram-представителей;
- ведёт договорные номера доступа;
- фиксирует все действия в operator_audit_log.

Что НЕ делает:
- не создаёт начисления;
- не отправляет Telegram-сообщения;
- не отправляет SMS и не управляет GEOS RC-4000.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from telegram import Update, ReplyKeyboardMarkup

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent
for folder in (BOTS_DIR, OSBB_ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_field_change, audit_log


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------

CONTRACT_STATUS = {
    "DRAFT": "🟡 Черновик",
    "ACTIVE": "✅ Активен",
    "SUSPENDED": "⏸ Приостановлен",
    "CLOSED": "⚫ Закрыт",
}

MODE_LABEL = {
    "FIXED_MONTHLY": "Фиксированная сумма в месяц",
    "PER_SQM_MONTHLY": "Ставка за м² в месяц",
    "MANUAL_MONTHLY": "Ручная сумма за месяц",
    "ONE_TIME": "Разовая сумма",
}

COUNTERPARTY_LABEL = {
    "PERSON": "Физическое лицо",
    "FOP": "ФОП",
    "COMPANY": "Компания",
    "OTHER": "Другое",
    "UNKNOWN": "Не указан",
}

CONTRACT_MENU = [
    ["✏️ Номер", "✏️ Контрагент"],
    ["📅 Срок", "⚙️ Сроки оплаты"],
    ["💳 Условия", "👤 Telegram"],
    ["📞 Телефонный доступ", "📝 Заметка"],
]

TYPE_MENU = [
    ["👤 Физлицо", "🧑‍💼 ФОП"],
    ["🏢 Компания", "❔ Другое"],
    ["⬅️ К договору"],
]

DATES_MENU = [
    ["▶️ Дата начала", "⏹ Дата окончания"],
    ["⬅️ К договору"],
]

TERMS_MENU = [
    ["📆 День оплаты", "⏳ Льгота"],
    ["🔔 Напомнить за", "⚠️ Предупреждение через"],
    ["⛔ Кандидат на отключение"],
    ["⬅️ К договору"],
]

ITEM_MODE_MENU = [
    ["💵 Фикс. / месяц", "📐 За м² / месяц"],
    ["✍️ Ручная сумма", "1️⃣ Разовая сумма"],
    ["⬅️ К условиям"],
]

BLOCK_MENU = [
    ["🛡️ Блокировать доступ", "🚫 Не блокировать"],
    ["⬅️ К условиям"],
]

PURPOSE_MENU = [
    ["👨‍💼 Сотрудник", "👑 Руководитель"],
    ["🛡️ Охрана", "📌 Другое"],
    ["⬅️ К телефонам"],
]


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# -----------------------------------------------------------------------------
# Common helpers
# -----------------------------------------------------------------------------


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def state_for(user_states: dict, user_id: int) -> dict | None:
    state = user_states.get(user_id)
    if isinstance(state, dict) and state.get("_module") == "unit_registry":
        return state
    return None


def is_commercial_schema_ready() -> tuple[bool, str]:
    required = {
        "commercial_contracts",
        "commercial_contract_items",
        "commercial_contract_recipients",
        "commercial_access_phones",
        "commercial_notifications",
        "commercial_access_actions",
    }
    conn = get_conn()
    try:
        cur = conn.cursor()
        missing = sorted(name for name in required if not table_exists(cur, name))
        if missing:
            return False, "Не выполнена миграция договорного ядра КП: " + ", ".join(missing)
        return True, ""
    finally:
        conn.close()


def label_status(status: str | None) -> str:
    return CONTRACT_STATUS.get(text(status), text(status) or "-")


def label_mode(mode: str | None) -> str:
    return MODE_LABEL.get(text(mode), text(mode) or "-")


def label_type(value: str | None) -> str:
    return COUNTERPARTY_LABEL.get(text(value), text(value) or "-")


def parse_date(value: str) -> str | None:
    value = text(value)
    if value == "-":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Используйте формат даты ГГГГ-ММ-ДД, например 2026-07-01.") from exc


def parse_amount(value: str, *, allow_blank: bool = False) -> float | None:
    value = text(value)
    if allow_blank and value == "-":
        return None
    try:
        number = float(value.replace(",", "."))
    except ValueError as exc:
        raise ValueError("Введите сумму числом, например 2500 или 120.50.") from exc
    if number < 0:
        raise ValueError("Сумма не может быть отрицательной.")
    return number


def parse_int(value: str, caption: str, *, min_value: int, max_value: int) -> int:
    try:
        result = int(text(value))
    except ValueError as exc:
        raise ValueError(f"{caption}: введите целое число.") from exc
    if not min_value <= result <= max_value:
        raise ValueError(f"{caption}: допустимо от {min_value} до {max_value}.")
    return result


def normalize_phone(value: str) -> tuple[str, str]:
    display = text(value)
    digits = re.sub(r"\D+", "", display)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(digits) == 12 and digits.startswith("380"):
        return "+" + digits, display
    raise ValueError("Введите номер в украинском формате, например +380671234567.")


# -----------------------------------------------------------------------------
# DB read functions
# -----------------------------------------------------------------------------


def get_unit(unit_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, apartment_number, unit_code, unit_type, entrance_number,
                   display_name, area_sqm, record_status
            FROM apartments
            WHERE id = ?
        """, (int(unit_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_contracts(unit_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, c.unit_id, c.contract_number, c.counterparty_name,
                   c.counterparty_type, c.status, c.valid_from, c.valid_to,
                   c.payment_due_day, c.grace_days,
                   c.reminder_days_before_due, c.warning_days_overdue,
                   c.suspension_candidate_days_overdue, c.internal_note,
                   (SELECT COUNT(*) FROM commercial_contract_items i
                     WHERE i.contract_id=c.id AND i.is_active=1) AS active_items,
                   (SELECT COUNT(*) FROM commercial_contract_recipients r
                     WHERE r.contract_id=c.id AND r.status='ACTIVE'
                       AND r.notification_enabled=1
                       AND COALESCE(TRIM(r.telegram_user_id),'') <> '') AS telegram_people,
                   (SELECT COUNT(*) FROM commercial_access_phones p
                     WHERE p.contract_id=c.id AND p.status <> 'CLOSED') AS access_phones
            FROM commercial_contracts c
            WHERE c.unit_id=?
            ORDER BY CASE c.status
                       WHEN 'ACTIVE' THEN 1 WHEN 'DRAFT' THEN 2
                       WHEN 'SUSPENDED' THEN 3 ELSE 4 END, c.id DESC
        """, (int(unit_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_contract(contract_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.*, a.unit_code, a.apartment_number, a.area_sqm,
                   (SELECT COUNT(*) FROM commercial_contract_items i
                     WHERE i.contract_id=c.id AND i.is_active=1) AS active_items,
                   (SELECT COUNT(*) FROM commercial_contract_recipients r
                     WHERE r.contract_id=c.id AND r.status='ACTIVE'
                       AND r.notification_enabled=1
                       AND COALESCE(TRIM(r.telegram_user_id),'') <> '') AS telegram_people,
                   (SELECT COUNT(*) FROM commercial_access_phones p
                     WHERE p.contract_id=c.id AND p.status <> 'CLOSED') AS access_phones
            FROM commercial_contracts c
            JOIN apartments a ON a.id=c.unit_id
            WHERE c.id=?
        """, (int(contract_id),))
        row = cur.fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            cur.execute("""
                SELECT outstanding_amount, access_blocking_outstanding_amount,
                       first_open_due_date
                FROM v_commercial_contract_debt_summary
                WHERE commercial_contract_id=?
            """, (int(contract_id),))
            debt = cur.fetchone()
            result["debt"] = float(debt[0] or 0) if debt else 0.0
            result["blocking_debt"] = float(debt[1] or 0) if debt else 0.0
            result["first_due"] = debt[2] if debt else None
        except sqlite3.Error:
            result["debt"] = result["blocking_debt"] = 0.0
            result["first_due"] = None
        return result
    finally:
        conn.close()


def list_items(contract_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM commercial_contract_items
            WHERE contract_id=?
            ORDER BY is_active DESC, id
        """, (int(contract_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_item(item_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT i.*, c.unit_id, a.area_sqm
            FROM commercial_contract_items i
            JOIN commercial_contracts c ON c.id=i.contract_id
            JOIN apartments a ON a.id=c.unit_id
            WHERE i.id=?
        """, (int(item_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_recipients(contract_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.*, a.telegram_username, a.telegram_first_name, a.telegram_last_name
            FROM commercial_contract_recipients r
            LEFT JOIN resident_accounts a ON a.id=r.resident_account_id
            WHERE r.contract_id=?
            ORDER BY r.status='ACTIVE' DESC, r.is_primary DESC, r.id
        """, (int(contract_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_recipient(recipient_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.*, a.telegram_username, a.telegram_first_name, a.telegram_last_name
            FROM commercial_contract_recipients r
            LEFT JOIN resident_accounts a ON a.id=r.resident_account_id
            WHERE r.id=?
        """, (int(recipient_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_phones(contract_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM commercial_access_phones
            WHERE contract_id=?
            ORDER BY status <> 'CLOSED' DESC, id
        """, (int(contract_id),))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_phone(phone_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM commercial_access_phones WHERE id=?", (int(phone_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# DB write functions + audit
# -----------------------------------------------------------------------------


def create_contract(unit_id: int, operator_id: int) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT unit_type FROM apartments WHERE id=?", (int(unit_id),))
        unit = cur.fetchone()
        if not unit or unit[0] != "COMMERCIAL":
            raise ValueError("Договор доступен только для коммерческого помещения.")
        stamp = now_db()
        cur.execute("""
            INSERT INTO commercial_contracts (
                unit_id, status, payment_due_day, grace_days,
                reminder_days_before_due, warning_days_overdue,
                suspension_candidate_days_overdue, internal_note,
                created_by, updated_by, created_at, updated_at
            ) VALUES (?, 'DRAFT', 10, 0, 3, 3, 7, ?, ?, ?, ?, ?)
        """, (
            int(unit_id),
            "Требуется заполнение индивидуальных условий договора.",
            str(operator_id), str(operator_id), stamp, stamp,
        ))
        contract_id = int(cur.lastrowid)
        audit_log(
            conn=conn, operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_created",
            table_name="commercial_contracts", row_id=contract_id,
            field_name="unit_id,status", old_value="",
            new_value=f"{unit_id}, DRAFT",
            source_context="commercial_contract_editor",
            comment="Создан черновик индивидуального договора КП.",
            commit=False,
        )
        conn.commit()
        return contract_id
    finally:
        conn.close()


def update_contract(contract_id: int, field: str, value: Any, operator_id: int) -> tuple[Any, Any]:
    allowed = {
        "contract_number", "counterparty_type", "counterparty_name",
        "valid_from", "valid_to", "payment_due_day", "grace_days",
        "reminder_days_before_due", "warning_days_overdue",
        "suspension_candidate_days_overdue", "internal_note",
    }
    if field not in allowed:
        raise ValueError("Недопустимое поле договора.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {field} FROM commercial_contracts WHERE id=?", (int(contract_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Договор не найден.")
        old = row[0]
        cur.execute(
            f"UPDATE commercial_contracts SET {field}=?, updated_by=?, updated_at=? WHERE id=?",
            (value, str(operator_id), now_db(), int(contract_id)),
        )
        audit_field_change(
            conn=conn, table_name="commercial_contracts", row_id=contract_id,
            field_name=field, old_value=old, new_value=value,
            operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_field_update",
            source_context="commercial_contract_editor",
            comment="Изменение поля индивидуального договора КП.",
        )
        conn.commit()
        return old, value
    finally:
        conn.close()


def set_contract_status(contract_id: int, status: str, operator_id: int) -> tuple[str, str]:
    if status not in {"DRAFT", "ACTIVE", "SUSPENDED", "CLOSED"}:
        raise ValueError("Недопустимый статус.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, counterparty_name, valid_from, valid_to FROM commercial_contracts WHERE id=?", (int(contract_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Договор не найден.")
        old = text(row[0])
        if status == "ACTIVE":
            errors = []
            if not text(row[1]):
                errors.append("не указан контрагент")
            if not text(row[2]):
                errors.append("не указана дата начала")
            if text(row[2]) and text(row[3]) and row[3] < row[2]:
                errors.append("дата окончания раньше даты начала")
            cur.execute("SELECT COUNT(*) FROM commercial_contract_items WHERE contract_id=? AND is_active=1", (int(contract_id),))
            if int(cur.fetchone()[0] or 0) == 0:
                errors.append("нет активных индивидуальных условий")
            if errors:
                raise ValueError("Нельзя активировать договор:\n• " + "\n• ".join(errors))
        try:
            cur.execute(
                "UPDATE commercial_contracts SET status=?, updated_by=?, updated_at=? WHERE id=?",
                (status, str(operator_id), now_db(), int(contract_id)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("У помещения уже есть другой ACTIVE договор.") from exc
        audit_field_change(
            conn=conn, table_name="commercial_contracts", row_id=contract_id,
            field_name="status", old_value=old, new_value=status,
            operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_status_update",
            source_context="commercial_contract_editor",
            comment="Изменение статуса договора КП.",
        )
        conn.commit()
        return old, status
    finally:
        conn.close()


def create_item(contract_id: int, name: str, mode: str, amount: float | None,
                quantity: float, blocks_access: int, operator_id: int) -> int:
    fixed = amount if mode in {"FIXED_MONTHLY", "ONE_TIME"} else None
    rate = amount if mode == "PER_SQM_MONTHLY" else None
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO commercial_contract_items (
                contract_id, item_name, reference_service_code,
                calculation_mode, fixed_amount, rate_amount, quantity_default,
                currency, blocks_phone_access_on_debt, is_active,
                created_at, updated_at
            ) VALUES (?, ?, 'COMMERCIAL_CONTRACT', ?, ?, ?, ?, 'UAH', ?, 1, ?, ?)
        """, (
            int(contract_id), name, mode, fixed, rate, float(quantity),
            int(blocks_access), now_db(), now_db(),
        ))
        item_id = int(cur.lastrowid)
        audit_log(
            conn=conn, operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_item_created",
            table_name="commercial_contract_items", row_id=item_id,
            field_name="item_name,calculation_mode,blocks_phone_access_on_debt",
            old_value="", new_value=f"{name}, {mode}, {blocks_access}",
            source_context="commercial_contract_editor",
            comment="Добавлено индивидуальное условие договора КП.",
            extra={"contract_id": contract_id}, commit=False,
        )
        conn.commit()
        return item_id
    finally:
        conn.close()


def update_item(item_id: int, field: str, value: Any, operator_id: int) -> tuple[Any, Any]:
    allowed = {"item_name", "fixed_amount", "rate_amount", "quantity_default", "blocks_phone_access_on_debt", "is_active"}
    if field not in allowed:
        raise ValueError("Недопустимое поле условия.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {field}, contract_id FROM commercial_contract_items WHERE id=?", (int(item_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Условие не найдено.")
        old, contract_id = row[0], row[1]
        cur.execute(f"UPDATE commercial_contract_items SET {field}=?, updated_at=? WHERE id=?", (value, now_db(), int(item_id)))
        audit_field_change(
            conn=conn, table_name="commercial_contract_items", row_id=item_id,
            field_name=field, old_value=old, new_value=value,
            operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_item_update",
            source_context="commercial_contract_editor",
            comment="Изменение индивидуального условия договора КП.",
            extra={"contract_id": contract_id},
        )
        conn.commit()
        return old, value
    finally:
        conn.close()


def create_recipient(contract_id: int, name: str | None, telegram_id: str, operator_id: int) -> int:
    telegram_id = text(telegram_id)
    if not re.fullmatch(r"\d{5,20}", telegram_id):
        raise ValueError("Telegram ID должен состоять только из цифр.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, telegram_first_name, telegram_last_name, telegram_username
            FROM resident_accounts WHERE telegram_user_id=?
        """, (telegram_id,))
        account = cur.fetchone()
        if not account:
            raise ValueError("Пользователь не найден в боте. Пусть сначала нажмёт /start.")
        cur.execute("""
            SELECT 1 FROM commercial_contract_recipients
            WHERE contract_id=? AND telegram_user_id=? AND status='ACTIVE'
        """, (int(contract_id), telegram_id))
        if cur.fetchone():
            raise ValueError("Этот Telegram-представитель уже активен в договоре.")
        cur.execute("""
            SELECT COUNT(*) FROM commercial_contract_recipients
            WHERE contract_id=? AND status='ACTIVE' AND is_primary=1
        """, (int(contract_id),))
        is_primary = 0 if int(cur.fetchone()[0] or 0) else 1
        fallback = " ".join(x for x in [text(account[1]), text(account[2])] if x) or text(account[3]) or telegram_id
        cur.execute("""
            INSERT INTO commercial_contract_recipients (
                contract_id, resident_account_id, telegram_user_id, recipient_name,
                recipient_role, is_primary, notification_enabled, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'REPRESENTATIVE', ?, 1, 'ACTIVE', ?, ?)
        """, (int(contract_id), int(account[0]), telegram_id, name or fallback, is_primary, now_db(), now_db()))
        recipient_id = int(cur.lastrowid)
        audit_log(
            conn=conn, operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_recipient_created",
            table_name="commercial_contract_recipients", row_id=recipient_id,
            field_name="contract_id,telegram_user_id", old_value="",
            new_value=f"{contract_id}, {telegram_id}",
            source_context="commercial_contract_editor",
            comment="Добавлен Telegram-представитель договора КП.",
            commit=False,
        )
        conn.commit()
        return recipient_id
    finally:
        conn.close()


def update_recipient(recipient_id: int, field: str, value: Any, operator_id: int) -> tuple[Any, Any]:
    allowed = {"recipient_name", "notification_enabled", "status", "is_primary"}
    if field not in allowed:
        raise ValueError("Недопустимое поле представителя.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {field}, contract_id FROM commercial_contract_recipients WHERE id=?", (int(recipient_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Представитель не найден.")
        old, contract_id = row[0], row[1]
        if field == "is_primary" and int(value or 0):
            cur.execute("UPDATE commercial_contract_recipients SET is_primary=0, updated_at=? WHERE contract_id=?", (now_db(), int(contract_id)))
        cur.execute(f"UPDATE commercial_contract_recipients SET {field}=?, updated_at=? WHERE id=?", (value, now_db(), int(recipient_id)))
        audit_field_change(
            conn=conn, table_name="commercial_contract_recipients", row_id=recipient_id,
            field_name=field, old_value=old, new_value=value,
            operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_contract_recipient_update",
            source_context="commercial_contract_editor",
            comment="Изменение Telegram-представителя договора КП.",
            extra={"contract_id": contract_id},
        )
        conn.commit()
        return old, value
    finally:
        conn.close()


def create_phone(contract_id: int, raw_phone: str, operator_id: int) -> int:
    normalized, display = normalize_phone(raw_phone)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO commercial_access_phones (
                contract_id, phone_normalized, phone_display, access_purpose,
                status, status_reason, created_at, updated_at
            ) VALUES (?, ?, ?, 'STAFF', 'ACTIVE', ?, ?, ?)
        """, (
            int(contract_id), normalized, display,
            "Добавлено оператором в договорный реестр. GSM-команда не отправлялась.",
            now_db(), now_db(),
        ))
        phone_id = int(cur.lastrowid)
        audit_log(
            conn=conn, operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_access_phone_created",
            table_name="commercial_access_phones", row_id=phone_id,
            field_name="contract_id,phone_normalized,status", old_value="",
            new_value=f"{contract_id}, {normalized}, ACTIVE",
            source_context="commercial_contract_editor",
            comment="Добавлено договорное право телефонного доступа. GEOS не менялся.",
            commit=False,
        )
        conn.commit()
        return phone_id
    except sqlite3.IntegrityError as exc:
        raise ValueError("Этот номер уже есть в данном договоре.") from exc
    finally:
        conn.close()


def update_phone(phone_id: int, field: str, value: Any, operator_id: int) -> tuple[Any, Any]:
    allowed = {"access_purpose", "status"}
    if field not in allowed:
        raise ValueError("Недопустимое поле телефонного права.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {field}, contract_id FROM commercial_access_phones WHERE id=?", (int(phone_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Номер не найден.")
        old, contract_id = row[0], row[1]
        if field == "status":
            cur.execute("UPDATE commercial_access_phones SET status=?, status_changed_at=?, updated_at=? WHERE id=?", (value, now_db(), now_db(), int(phone_id)))
        else:
            cur.execute("UPDATE commercial_access_phones SET access_purpose=?, updated_at=? WHERE id=?", (value, now_db(), int(phone_id)))
        audit_field_change(
            conn=conn, table_name="commercial_access_phones", row_id=phone_id,
            field_name=field, old_value=old, new_value=value,
            operator_id=operator_id, user_id=operator_id,
            actor_type="operator", action_type="commercial_access_phone_update",
            source_context="commercial_contract_editor",
            comment="Изменено договорное право телефонного доступа. GEOS не менялся.",
            extra={"contract_id": contract_id},
        )
        conn.commit()
        return old, value
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------------


def number_label(contract: dict) -> str:
    return f"№ {contract['contract_number']}" if text(contract.get("contract_number")) else f"черновик #{contract['id']}"


def item_amount(item: dict) -> str:
    mode = text(item.get("calculation_mode"))
    if mode == "PER_SQM_MONTHLY":
        rate = item.get("rate_amount")
        qty = float(item.get("quantity_default") or 0)
        return f"{float(rate or 0):g} грн. × {qty:g} м² = {float(rate or 0) * qty:g} грн./мес."
    if mode == "FIXED_MONTHLY":
        return f"{float(item.get('fixed_amount') or 0):g} грн./мес."
    if mode == "ONE_TIME":
        return f"{float(item.get('fixed_amount') or 0):g} грн. разово"
    return "сумма задаётся оператором при начислении"


def format_contract_list(unit: dict, contracts: list[dict]) -> str:
    code = unit.get("unit_code") or unit.get("apartment_number") or "-"
    lines = [f"📄 Договоры КП {code}", "", "Цена КП задаётся только строками конкретного договора.", ""]
    if not contracts:
        lines.append("Договоров пока нет.")
    for i, contract in enumerate(contracts, 1):
        lines.append(f"{i}. {number_label(contract)} | {label_status(contract.get('status'))} | {contract.get('counterparty_name') or 'контрагент не указан'}")
    lines.append("\nВыберите договор или создайте черновик.")
    return "\n".join(lines)


def format_contract(contract: dict) -> str:
    code = contract.get("unit_code") or contract.get("apartment_number") or "-"
    return "\n".join([
        f"📄 Договор {number_label(contract)}",
        "",
        f"КП: {code}",
        f"Статус: {label_status(contract.get('status'))}",
        f"Контрагент: {contract.get('counterparty_name') or 'не указан'}",
        f"Тип: {label_type(contract.get('counterparty_type'))}",
        f"Срок: {contract.get('valid_from') or 'не указан'} — {contract.get('valid_to') or 'бессрочно'}",
        "",
        f"Оплата до: {contract.get('payment_due_day')} числа",
        f"Льгота: {contract.get('grace_days')} дн.",
        f"Напомнить за: {contract.get('reminder_days_before_due')} дн.",
        f"Предупреждение через: {contract.get('warning_days_overdue')} дн.",
        f"Кандидат на отключение через: {contract.get('suspension_candidate_days_overdue')} дн.",
        "",
        f"Активных условий: {contract.get('active_items')}",
        f"Telegram-представителей: {contract.get('telegram_people')}",
        f"Телефонных прав: {contract.get('access_phones')}",
        "",
        f"Долг: {float(contract.get('debt') or 0):g} грн.",
        f"Блокирующий доступ долг: {float(contract.get('blocking_debt') or 0):g} грн.",
        f"Заметка: {contract.get('internal_note') or 'нет'}",
        "",
        "Начисления и уведомления этим экраном не создаются.",
        "GEOS RC-4000 не управляется автоматически.",
    ])


def format_items(items: list[dict]) -> str:
    lines = ["💳 Индивидуальные условия", "", "Ставка берётся из договора, не из общего тарифа.", ""]
    if not items:
        lines.append("Условий пока нет.")
    for i, item in enumerate(items, 1):
        mark = "✅" if int(item.get("is_active") or 0) else "⚪"
        lines.append(f"{i}. {mark} {item.get('item_name') or 'без названия'}\n   {label_mode(item.get('calculation_mode'))}\n   {item_amount(item)}\n   Блокирует доступ при долге: {'Да' if int(item.get('blocks_phone_access_on_debt') or 0) else 'Нет'}")
    return "\n".join(lines)


def format_item(item: dict) -> str:
    return "\n".join([
        f"💳 {item.get('item_name') or 'без названия'}",
        "",
        f"Статус: {'✅ активно' if int(item.get('is_active') or 0) else '⚪ закрыто'}",
        f"Расчёт: {label_mode(item.get('calculation_mode'))}",
        f"Сумма/ставка: {item_amount(item)}",
        f"Блокирует доступ: {'Да' if int(item.get('blocks_phone_access_on_debt') or 0) else 'Нет'}",
    ])


def recipient_name(row: dict) -> str:
    return text(row.get("recipient_name")) or " ".join(v for v in [text(row.get("telegram_first_name")), text(row.get("telegram_last_name"))] if v) or text(row.get("telegram_user_id")) or "без имени"


def format_recipients(rows: list[dict]) -> str:
    lines = ["👤 Telegram-представители", "", "Уведомления должникам идут через Telegram.", "Представитель должен сначала нажать /start у бота.", ""]
    if not rows:
        lines.append("Представителей пока нет.")
    for i, row in enumerate(rows, 1):
        primary = " ⭐ основной" if int(row.get("is_primary") or 0) else ""
        enabled = "уведомления ВКЛ" if int(row.get("notification_enabled") or 0) else "уведомления ВЫКЛ"
        lines.append(f"{i}. {'✅' if row.get('status') == 'ACTIVE' else '⚪'} {recipient_name(row)}{primary}\n   ID: {row.get('telegram_user_id') or '-'} | {enabled}")
    return "\n".join(lines)


def format_recipient(row: dict) -> str:
    username = f"@{row['telegram_username']}" if text(row.get("telegram_username")) else "не указан"
    return "\n".join([
        f"👤 {recipient_name(row)}",
        "",
        f"Telegram ID: {row.get('telegram_user_id') or '-'}",
        f"Username: {username}",
        f"Статус: {'✅ активен' if row.get('status') == 'ACTIVE' else '⚪ закрыт'}",
        f"Уведомления: {'✅ включены' if int(row.get('notification_enabled') or 0) else '🔕 выключены'}",
        f"Основной: {'Да' if int(row.get('is_primary') or 0) else 'Нет'}",
    ])


def phone_label(row: dict) -> str:
    return row.get("phone_display") or row.get("phone_normalized") or "без номера"


def format_phones(rows: list[dict]) -> str:
    purpose = {"STAFF": "Сотрудник", "MANAGER": "Руководитель", "SECURITY": "Охрана", "OTHER": "Другое"}
    status = {"ACTIVE": "✅ активно", "WARNING": "⚠️ предупреждение", "SUSPENSION_CANDIDATE": "🟠 кандидат", "SUSPENDED_DEBT": "⛔ отключено вручную", "MANUAL_BLOCK": "🔒 ручная блокировка", "CLOSED": "⚪ закрыто"}
    lines = ["📞 Телефонный доступ", "", "Это реестр договорных прав. Команды GEOS здесь не отправляются.", ""]
    if not rows:
        lines.append("Номеров пока нет.")
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {phone_label(row)}\n   {purpose.get(row.get('access_purpose'), row.get('access_purpose') or '-')} | {status.get(row.get('status'), row.get('status') or '-')}")
    return "\n".join(lines)


def format_phone(row: dict) -> str:
    purpose = {"STAFF": "Сотрудник", "MANAGER": "Руководитель", "SECURITY": "Охрана", "OTHER": "Другое"}
    return "\n".join([
        f"📞 {phone_label(row)}",
        "",
        f"Назначение: {purpose.get(row.get('access_purpose'), row.get('access_purpose') or '-')}",
        f"Статус: {row.get('status') or '-'}",
        f"Причина: {row.get('status_reason') or 'нет'}",
        "",
        "GEOS RC-4000 этой карточкой не меняется.",
    ])


# -----------------------------------------------------------------------------
# Message screens
# -----------------------------------------------------------------------------


async def show_contracts(update: Update, state: dict, unit_id: int) -> None:
    unit = get_unit(unit_id)
    if not unit:
        await update.message.reply_text("Помещение не найдено.")
        return
    contracts = list_contracts(unit_id)
    mapping, labels = {}, []
    for row in contracts:
        label = f"📄 {number_label(row)}"
        if label in mapping:
            label += f" [{row['id']}]"
        mapping[label] = int(row["id"])
        labels.append(label)
    buttons = [labels[i:i+2] for i in range(0, len(labels), 2)]
    buttons += [["➕ Создать договор"], ["⬅️ К помещению", "🏢 Помещения"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_list"
    state["commercial_contract_map"] = mapping
    state["unit_registry_unit_id"] = int(unit_id)
    state.pop("commercial_contract_id", None)
    await update.message.reply_text(format_contract_list(unit, contracts), reply_markup=kb(buttons))


async def show_contract_card(update: Update, state: dict, contract_id: int) -> None:
    contract = get_contract(contract_id)
    if not contract:
        await update.message.reply_text("Договор не найден.")
        return
    rows = [r[:] for r in CONTRACT_MENU]
    if contract["status"] in {"DRAFT", "SUSPENDED"}:
        rows.append(["✅ Активировать договор"])
    if contract["status"] == "ACTIVE":
        rows.append(["⏸ Приостановить договор"])
    if contract["status"] != "CLOSED":
        rows.append(["⚫ Закрыть договор"])
    rows += [["📋 К договорам", "⬅️ К помещению"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_card"
    state["commercial_contract_id"] = int(contract_id)
    state["unit_registry_unit_id"] = int(contract["unit_id"])
    await update.message.reply_text(format_contract(contract), reply_markup=kb(rows))


async def show_items(update: Update, state: dict, contract_id: int) -> None:
    rows = list_items(contract_id)
    mapping, labels = {}, []
    for row in rows:
        label = f"{'✅' if int(row.get('is_active') or 0) else '⚪'} {row.get('item_name') or 'без названия'}"
        if label in mapping:
            label += f" [{row['id']}]"
        mapping[label] = int(row["id"])
        labels.append(label)
    buttons = [labels[i:i+2] for i in range(0, len(labels), 2)]
    buttons += [["➕ Добавить условие"], ["⬅️ К договору"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_items"
    state["commercial_contract_item_map"] = mapping
    await update.message.reply_text(format_items(rows), reply_markup=kb(buttons))


async def show_item_card(update: Update, state: dict, item_id: int) -> None:
    item = get_item(item_id)
    if not item:
        await update.message.reply_text("Условие не найдено.")
        return
    rows = [["✏️ Наименование", "✏️ Сумма/ставка"], ["✏️ Количество", "🛡️ Доступ при долге"]]
    rows.append(["🔒 Закрыть условие"] if int(item.get("is_active") or 0) else ["↩️ Активировать условие"])
    rows += [["⬅️ К условиям"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_item_card"
    state["commercial_contract_item_id"] = int(item_id)
    state["commercial_contract_id"] = int(item["contract_id"])
    await update.message.reply_text(format_item(item), reply_markup=kb(rows))


async def show_recipients(update: Update, state: dict, contract_id: int) -> None:
    rows = list_recipients(contract_id)
    mapping, labels = {}, []
    for row in rows:
        label = f"{'✅' if row.get('status') == 'ACTIVE' else '⚪'} {recipient_name(row)}"
        if label in mapping:
            label += f" [{row['id']}]"
        mapping[label] = int(row["id"])
        labels.append(label)
    buttons = [labels[i:i+2] for i in range(0, len(labels), 2)]
    buttons += [["➕ Добавить представителя"], ["⬅️ К договору"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_recipients"
    state["commercial_contract_recipient_map"] = mapping
    await update.message.reply_text(format_recipients(rows), reply_markup=kb(buttons))


async def show_recipient_card(update: Update, state: dict, recipient_id: int) -> None:
    row = get_recipient(recipient_id)
    if not row:
        await update.message.reply_text("Представитель не найден.")
        return
    buttons = [["✏️ Имя", "⭐ Сделать основным"]]
    buttons.append(["🔕 Выключить уведомления"] if int(row.get("notification_enabled") or 0) else ["🔔 Включить уведомления"])
    buttons.append(["⚪ Закрыть представителя"] if row.get("status") == "ACTIVE" else ["✅ Активировать представителя"])
    buttons += [["⬅️ К представителям"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_recipient_card"
    state["commercial_contract_recipient_id"] = int(recipient_id)
    state["commercial_contract_id"] = int(row["contract_id"])
    await update.message.reply_text(format_recipient(row), reply_markup=kb(buttons))


async def show_phones(update: Update, state: dict, contract_id: int) -> None:
    rows = list_phones(contract_id)
    mapping, labels = {}, []
    for row in rows:
        label = f"📞 {phone_label(row)}"
        if label in mapping:
            label += f" [{row['id']}]"
        mapping[label] = int(row["id"])
        labels.append(label)
    buttons = [labels[i:i+2] for i in range(0, len(labels), 2)]
    buttons += [["➕ Добавить номер"], ["⬅️ К договору"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_phones"
    state["commercial_contract_phone_map"] = mapping
    await update.message.reply_text(format_phones(rows), reply_markup=kb(buttons))


async def show_phone_card(update: Update, state: dict, phone_id: int) -> None:
    row = get_phone(phone_id)
    if not row:
        await update.message.reply_text("Номер не найден.")
        return
    buttons = [["✏️ Назначение"]]
    if row.get("status") == "CLOSED":
        buttons.append(["✅ Активировать право"])
    elif row.get("status") in {"ACTIVE", "WARNING"}:
        buttons.append(["⚪ Закрыть право"])
    buttons += [["⬅️ К телефонам"], ["🏠 Главное меню"]]
    state["mode"] = "commercial_contract_phone_card"
    state["commercial_contract_phone_id"] = int(phone_id)
    state["commercial_contract_id"] = int(row["contract_id"])
    await update.message.reply_text(format_phone(row), reply_markup=kb(buttons))


async def back_to_unit_card(update: Update, user_states: dict, user_id: int, unit_id: int) -> None:
    """Ленивая загрузка avoids circular import during initial module import."""
    try:
        from handlers.unit_registry_editor import show_unit_card
    except Exception:
        from unit_registry_editor import show_unit_card
    await show_unit_card(update, user_states, user_id, int(unit_id))


# -----------------------------------------------------------------------------
# Main integration router
# -----------------------------------------------------------------------------


async def handle_commercial_contract_editor_text(
    update: Update,
    user_states: dict,
    user_id: int,
    raw_text: str,
) -> bool:
    """Return True only when this module really handles the message."""
    state = state_for(user_states, user_id)
    if not state:
        return False

    value = text(raw_text)
    mode = text(state.get("mode"))

    # Entry from commercial unit card.
    if mode == "unit_registry_card" and value == "📄 Договоры":
        unit_id = state.get("unit_registry_unit_id")
        unit = get_unit(int(unit_id)) if unit_id else None
        if not unit or unit.get("unit_type") != "COMMERCIAL":
            await update.message.reply_text("Договоры доступны только для коммерческих помещений.")
            return True
        ok, message = is_commercial_schema_ready()
        if not ok:
            await update.message.reply_text("⚠️ " + message)
            return True
        await show_contracts(update, state, int(unit_id))
        return True

    if not mode.startswith("commercial_contract_"):
        return False

    ok, message = is_commercial_schema_ready()
    if not ok:
        await update.message.reply_text("⚠️ " + message)
        return True

    # Leave main-menu processing to unit_registry_editor / parking_bot.
    if value == "🏠 Главное меню":
        return False

    contract_id = state.get("commercial_contract_id")
    unit_id = state.get("unit_registry_unit_id")

    # -----------------------------------------------------------------
    # Back navigation.
    # -----------------------------------------------------------------
    if value.startswith("⬅️"):
        if mode == "commercial_contract_list":
            await back_to_unit_card(update, user_states, user_id, int(unit_id))
            return True
        if mode == "commercial_contract_card":
            if value == "⬅️ К помещению":
                await back_to_unit_card(update, user_states, user_id, int(unit_id))
            else:
                await show_contracts(update, state, int(unit_id))
            return True
        if mode in {"commercial_contract_dates", "commercial_contract_terms", "commercial_contract_field", "commercial_contract_counterparty_type", "commercial_contract_counterparty_name", "commercial_contract_close_confirm"}:
            await show_contract_card(update, state, int(contract_id))
            return True
        if mode in {"commercial_contract_items", "commercial_contract_item_new_mode", "commercial_contract_item_new_name", "commercial_contract_item_new_amount", "commercial_contract_item_new_quantity", "commercial_contract_item_new_block", "commercial_contract_item_field", "commercial_contract_item_block", "commercial_contract_item_card"}:
            await show_items(update, state, int(contract_id))
            return True
        if mode in {"commercial_contract_recipients", "commercial_contract_recipient_new_name", "commercial_contract_recipient_new_id", "commercial_contract_recipient_field", "commercial_contract_recipient_card"}:
            await show_recipients(update, state, int(contract_id))
            return True
        if mode in {"commercial_contract_phones", "commercial_contract_phone_new", "commercial_contract_phone_purpose", "commercial_contract_phone_card"}:
            await show_phones(update, state, int(contract_id))
            return True

    # -----------------------------------------------------------------
    # Contract list.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_list":
        if value == "➕ Создать договор":
            try:
                contract_id = create_contract(int(unit_id), user_id)
            except Exception as exc:
                await update.message.reply_text(f"Не удалось создать договор: {exc}")
                return True
            await update.message.reply_text("✅ Черновик договора создан.")
            await show_contract_card(update, state, contract_id)
            return True
        selected = (state.get("commercial_contract_map") or {}).get(value)
        if selected:
            await show_contract_card(update, state, int(selected))
            return True
        if value == "⬅️ К помещению":
            await back_to_unit_card(update, user_states, user_id, int(unit_id))
            return True
        await update.message.reply_text("Выберите договор кнопкой или создайте новый черновик.")
        return True

    # -----------------------------------------------------------------
    # Contract card.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_card":
        if value == "✏️ Номер":
            state["mode"], state["commercial_contract_field"] = "commercial_contract_field", "contract_number"
            await update.message.reply_text("Введите номер договора или «-», если он пока отсутствует.", reply_markup=kb([["⬅️ К договору"]]))
            return True
        if value == "✏️ Контрагент":
            state["mode"] = "commercial_contract_counterparty_type"
            await update.message.reply_text("Выберите тип контрагента.", reply_markup=kb(TYPE_MENU))
            return True
        if value == "📅 Срок":
            state["mode"] = "commercial_contract_dates"
            await update.message.reply_text("📅 Срок действия договора", reply_markup=kb(DATES_MENU))
            return True
        if value == "⚙️ Сроки оплаты":
            state["mode"] = "commercial_contract_terms"
            await update.message.reply_text("⚙️ Сроки оплаты и уведомлений", reply_markup=kb(TERMS_MENU))
            return True
        if value == "💳 Условия":
            await show_items(update, state, int(contract_id))
            return True
        if value == "👤 Telegram":
            await show_recipients(update, state, int(contract_id))
            return True
        if value == "📞 Телефонный доступ":
            await show_phones(update, state, int(contract_id))
            return True
        if value == "📝 Заметка":
            state["mode"], state["commercial_contract_field"] = "commercial_contract_field", "internal_note"
            await update.message.reply_text("Введите заметку или «-» чтобы очистить.", reply_markup=kb([["⬅️ К договору"]]))
            return True
        if value == "✅ Активировать договор":
            try:
                old, new = set_contract_status(int(contract_id), "ACTIVE", user_id)
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(f"✅ {label_status(old)} → {label_status(new)}")
            await show_contract_card(update, state, int(contract_id))
            return True
        if value == "⏸ Приостановить договор":
            old, new = set_contract_status(int(contract_id), "SUSPENDED", user_id)
            await update.message.reply_text(f"✅ {label_status(old)} → {label_status(new)}")
            await show_contract_card(update, state, int(contract_id))
            return True
        if value == "⚫ Закрыть договор":
            state["mode"] = "commercial_contract_close_confirm"
            await update.message.reply_text("Закрыть договор? Он останется в истории, но перестанет быть рабочим.", reply_markup=kb([["✅ Да, закрыть договор"], ["⬅️ К договору"]]))
            return True
        if value == "📋 К договорам":
            await show_contracts(update, state, int(unit_id))
            return True
        if value == "⬅️ К помещению":
            await back_to_unit_card(update, user_states, user_id, int(unit_id))
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "commercial_contract_close_confirm":
        if value == "✅ Да, закрыть договор":
            old, new = set_contract_status(int(contract_id), "CLOSED", user_id)
            await update.message.reply_text(f"✅ {label_status(old)} → {label_status(new)}")
            await show_contract_card(update, state, int(contract_id))
            return True
        await update.message.reply_text("Подтвердите действие кнопкой.")
        return True

    # -----------------------------------------------------------------
    # Counterparty / text fields / dates / numeric terms.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_counterparty_type":
        types = {"👤 Физлицо": "PERSON", "🧑‍💼 ФОП": "FOP", "🏢 Компания": "COMPANY", "❔ Другое": "OTHER"}
        chosen = types.get(value)
        if not chosen:
            await update.message.reply_text("Выберите тип кнопкой.")
            return True
        state["commercial_contract_counterparty_type"] = chosen
        state["mode"] = "commercial_contract_counterparty_name"
        await update.message.reply_text("Введите название / ПІБ контрагента.", reply_markup=kb([["⬅️ К договору"]]))
        return True

    if mode == "commercial_contract_counterparty_name":
        name = None if value == "-" else value
        try:
            update_contract(int(contract_id), "counterparty_type", state.get("commercial_contract_counterparty_type") or "UNKNOWN", user_id)
            update_contract(int(contract_id), "counterparty_name", name, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка сохранения: {exc}")
            return True
        state.pop("commercial_contract_counterparty_type", None)
        await update.message.reply_text("✅ Контрагент сохранён.")
        await show_contract_card(update, state, int(contract_id))
        return True

    if mode == "commercial_contract_dates":
        mapping = {"▶️ Дата начала": "valid_from", "⏹ Дата окончания": "valid_to"}
        field = mapping.get(value)
        if not field:
            await update.message.reply_text("Выберите поле кнопкой.")
            return True
        state["mode"], state["commercial_contract_field"] = "commercial_contract_field", field
        await update.message.reply_text("Введите дату ГГГГ-ММ-ДД или «-».", reply_markup=kb([["⬅️ К договору"]]))
        return True

    if mode == "commercial_contract_terms":
        mapping = {
            "📆 День оплаты": ("payment_due_day", 1, 31, "День оплаты"),
            "⏳ Льгота": ("grace_days", 0, 365, "Льгота"),
            "🔔 Напомнить за": ("reminder_days_before_due", 0, 365, "Срок напоминания"),
            "⚠️ Предупреждение через": ("warning_days_overdue", 0, 365, "Срок предупреждения"),
            "⛔ Кандидат на отключение": ("suspension_candidate_days_overdue", 0, 365, "Срок кандидата"),
        }
        selected = mapping.get(value)
        if not selected:
            await update.message.reply_text("Выберите настройку кнопкой.")
            return True
        state["mode"] = "commercial_contract_field"
        state["commercial_contract_field"] = selected[0]
        state["commercial_contract_field_limits"] = selected[1:]
        await update.message.reply_text(f"Введите: {selected[3]}.", reply_markup=kb([["⬅️ К срокам оплаты"]]))
        return True

    if mode == "commercial_contract_field":
        field = state.get("commercial_contract_field")
        try:
            if field in {"valid_from", "valid_to"}:
                data = parse_date(value)
            elif field in {"payment_due_day", "grace_days", "reminder_days_before_due", "warning_days_overdue", "suspension_candidate_days_overdue"}:
                min_v, max_v, caption = state.get("commercial_contract_field_limits", (0, 365, "Значение"))
                data = parse_int(value, caption, min_value=min_v, max_value=max_v)
            else:
                data = None if value == "-" else value
            update_contract(int(contract_id), str(field), data, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка сохранения: {exc}")
            return True
        state.pop("commercial_contract_field", None)
        state.pop("commercial_contract_field_limits", None)
        await update.message.reply_text("✅ Сохранено.")
        await show_contract_card(update, state, int(contract_id))
        return True

    # -----------------------------------------------------------------
    # Conditions.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_items":
        if value == "➕ Добавить условие":
            state["mode"] = "commercial_contract_item_new_mode"
            await update.message.reply_text("Выберите способ расчёта.", reply_markup=kb(ITEM_MODE_MENU))
            return True
        item_id = (state.get("commercial_contract_item_map") or {}).get(value)
        if item_id:
            await show_item_card(update, state, int(item_id))
            return True
        await update.message.reply_text("Выберите условие кнопкой.")
        return True

    if mode == "commercial_contract_item_new_mode":
        mapping = {"💵 Фикс. / месяц": "FIXED_MONTHLY", "📐 За м² / месяц": "PER_SQM_MONTHLY", "✍️ Ручная сумма": "MANUAL_MONTHLY", "1️⃣ Разовая сумма": "ONE_TIME"}
        chosen = mapping.get(value)
        if not chosen:
            await update.message.reply_text("Выберите способ расчёта кнопкой.")
            return True
        state["commercial_new_item_mode"] = chosen
        state["mode"] = "commercial_contract_item_new_name"
        await update.message.reply_text("Введите наименование условия, например «Парковка сотрудников».", reply_markup=kb([["⬅️ К условиям"]]))
        return True

    if mode == "commercial_contract_item_new_name":
        if value == "-":
            await update.message.reply_text("Наименование обязательно.")
            return True
        state["commercial_new_item_name"] = value
        if state.get("commercial_new_item_mode") == "MANUAL_MONTHLY":
            state["commercial_new_item_amount"] = None
            state["commercial_new_item_quantity"] = 1.0
            state["mode"] = "commercial_contract_item_new_block"
            await update.message.reply_text("Блокировать телефонный доступ при долге по этому условию?", reply_markup=kb(BLOCK_MENU))
            return True
        state["mode"] = "commercial_contract_item_new_amount"
        await update.message.reply_text("Введите сумму/ставку в гривнах.", reply_markup=kb([["⬅️ К условиям"]]))
        return True

    if mode == "commercial_contract_item_new_amount":
        try:
            state["commercial_new_item_amount"] = parse_amount(value)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        if state.get("commercial_new_item_mode") == "PER_SQM_MONTHLY":
            contract = get_contract(int(contract_id))
            area = float(contract.get("area_sqm") or 0) if contract else 0
            if area > 0:
                state["commercial_new_item_quantity"] = area
                state["mode"] = "commercial_contract_item_new_block"
                await update.message.reply_text(f"Количество взято из площади помещения: {area:g} м².\n\nБлокировать доступ при долге?", reply_markup=kb(BLOCK_MENU))
                return True
            state["mode"] = "commercial_contract_item_new_quantity"
            await update.message.reply_text("Площадь не указана. Введите количество для расчёта.", reply_markup=kb([["⬅️ К условиям"]]))
            return True
        state["commercial_new_item_quantity"] = 1.0
        state["mode"] = "commercial_contract_item_new_block"
        await update.message.reply_text("Блокировать телефонный доступ при долге по этому условию?", reply_markup=kb(BLOCK_MENU))
        return True

    if mode == "commercial_contract_item_new_quantity":
        try:
            qty = parse_amount(value)
            if qty is None or qty <= 0:
                raise ValueError("Количество должно быть больше нуля.")
            state["commercial_new_item_quantity"] = qty
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = "commercial_contract_item_new_block"
        await update.message.reply_text("Блокировать доступ при долге по этому условию?", reply_markup=kb(BLOCK_MENU))
        return True

    if mode == "commercial_contract_item_new_block":
        blocks = {"🛡️ Блокировать доступ": 1, "🚫 Не блокировать": 0}.get(value)
        if blocks is None:
            await update.message.reply_text("Выберите правило кнопкой.")
            return True
        try:
            item_id = create_item(
                int(contract_id), state.get("commercial_new_item_name"),
                state.get("commercial_new_item_mode"),
                state.get("commercial_new_item_amount"),
                float(state.get("commercial_new_item_quantity") or 1),
                blocks, user_id,
            )
        except Exception as exc:
            await update.message.reply_text(f"Не удалось создать условие: {exc}")
            return True
        for key in list(state):
            if key.startswith("commercial_new_item_"):
                state.pop(key, None)
        await update.message.reply_text("✅ Условие создано.")
        await show_item_card(update, state, item_id)
        return True

    if mode == "commercial_contract_item_card":
        item_id = state.get("commercial_contract_item_id")
        item = get_item(int(item_id)) if item_id else None
        if not item:
            await update.message.reply_text("Условие не найдено.")
            return True
        if value == "✏️ Наименование":
            state["mode"], state["commercial_item_field"] = "commercial_contract_item_field", "item_name"
            await update.message.reply_text("Введите новое наименование.", reply_markup=kb([["⬅️ К условию"]]))
            return True
        if value == "✏️ Сумма/ставка":
            if item.get("calculation_mode") == "MANUAL_MONTHLY":
                await update.message.reply_text("У ручной суммы нет фиксированной ставки.")
                return True
            state["mode"], state["commercial_item_field"] = "commercial_contract_item_field", "rate_amount" if item.get("calculation_mode") == "PER_SQM_MONTHLY" else "fixed_amount"
            await update.message.reply_text("Введите новую сумму/ставку.", reply_markup=kb([["⬅️ К условию"]]))
            return True
        if value == "✏️ Количество":
            if item.get("calculation_mode") != "PER_SQM_MONTHLY":
                await update.message.reply_text("Количество используется только для расчёта за м².")
                return True
            state["mode"], state["commercial_item_field"] = "commercial_contract_item_field", "quantity_default"
            await update.message.reply_text("Введите новое количество.", reply_markup=kb([["⬅️ К условию"]]))
            return True
        if value == "🛡️ Доступ при долге":
            state["mode"] = "commercial_contract_item_block"
            await update.message.reply_text("Выберите правило.", reply_markup=kb(BLOCK_MENU))
            return True
        if value == "🔒 Закрыть условие":
            update_item(int(item_id), "is_active", 0, user_id)
            await update.message.reply_text("✅ Условие закрыто.")
            await show_item_card(update, state, int(item_id))
            return True
        if value == "↩️ Активировать условие":
            update_item(int(item_id), "is_active", 1, user_id)
            await update.message.reply_text("✅ Условие активировано.")
            await show_item_card(update, state, int(item_id))
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "commercial_contract_item_field":
        item_id = int(state.get("commercial_contract_item_id"))
        field = state.get("commercial_item_field")
        try:
            if field == "item_name":
                if value == "-":
                    raise ValueError("Наименование нельзя очистить.")
                data = value
            else:
                data = parse_amount(value)
                if field == "quantity_default" and (data is None or data <= 0):
                    raise ValueError("Количество должно быть больше нуля.")
            update_item(item_id, str(field), data, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state.pop("commercial_item_field", None)
        await update.message.reply_text("✅ Сохранено.")
        await show_item_card(update, state, item_id)
        return True

    if mode == "commercial_contract_item_block":
        blocks = {"🛡️ Блокировать доступ": 1, "🚫 Не блокировать": 0}.get(value)
        if blocks is None:
            await update.message.reply_text("Выберите правило кнопкой.")
            return True
        item_id = int(state.get("commercial_contract_item_id"))
        update_item(item_id, "blocks_phone_access_on_debt", blocks, user_id)
        await update.message.reply_text("✅ Правило сохранено.")
        await show_item_card(update, state, item_id)
        return True

    # -----------------------------------------------------------------
    # Telegram recipients.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_recipients":
        if value == "➕ Добавить представителя":
            state["mode"] = "commercial_contract_recipient_new_name"
            await update.message.reply_text("Введите имя представителя или «-».", reply_markup=kb([["⬅️ К представителям"]]))
            return True
        recipient_id = (state.get("commercial_contract_recipient_map") or {}).get(value)
        if recipient_id:
            await show_recipient_card(update, state, int(recipient_id))
            return True
        await update.message.reply_text("Выберите представителя кнопкой.")
        return True

    if mode == "commercial_contract_recipient_new_name":
        state["commercial_new_recipient_name"] = None if value == "-" else value
        state["mode"] = "commercial_contract_recipient_new_id"
        await update.message.reply_text("Введите цифровой Telegram ID. Представитель должен уже нажать /start у бота.", reply_markup=kb([["⬅️ К представителям"]]))
        return True

    if mode == "commercial_contract_recipient_new_id":
        try:
            recipient_id = create_recipient(int(contract_id), state.get("commercial_new_recipient_name"), value, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Не удалось добавить: {exc}")
            return True
        state.pop("commercial_new_recipient_name", None)
        await update.message.reply_text("✅ Telegram-представитель добавлен.")
        await show_recipient_card(update, state, recipient_id)
        return True

    if mode == "commercial_contract_recipient_card":
        recipient_id = int(state.get("commercial_contract_recipient_id"))
        row = get_recipient(recipient_id)
        if not row:
            await update.message.reply_text("Представитель не найден.")
            return True
        if value == "✏️ Имя":
            state["mode"] = "commercial_contract_recipient_field"
            await update.message.reply_text("Введите имя или «-».", reply_markup=kb([["⬅️ К представителям"]]))
            return True
        if value == "⭐ Сделать основным":
            update_recipient(recipient_id, "is_primary", 1, user_id)
            await update.message.reply_text("✅ Назначен основным.")
            await show_recipient_card(update, state, recipient_id)
            return True
        if value == "🔕 Выключить уведомления":
            update_recipient(recipient_id, "notification_enabled", 0, user_id)
            await update.message.reply_text("✅ Уведомления выключены.")
            await show_recipient_card(update, state, recipient_id)
            return True
        if value == "🔔 Включить уведомления":
            update_recipient(recipient_id, "notification_enabled", 1, user_id)
            await update.message.reply_text("✅ Уведомления включены.")
            await show_recipient_card(update, state, recipient_id)
            return True
        if value == "⚪ Закрыть представителя":
            update_recipient(recipient_id, "status", "CLOSED", user_id)
            await update.message.reply_text("✅ Представитель закрыт.")
            await show_recipient_card(update, state, recipient_id)
            return True
        if value == "✅ Активировать представителя":
            update_recipient(recipient_id, "status", "ACTIVE", user_id)
            await update.message.reply_text("✅ Представитель активирован.")
            await show_recipient_card(update, state, recipient_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "commercial_contract_recipient_field":
        recipient_id = int(state.get("commercial_contract_recipient_id"))
        update_recipient(recipient_id, "recipient_name", None if value == "-" else value, user_id)
        await update.message.reply_text("✅ Сохранено.")
        await show_recipient_card(update, state, recipient_id)
        return True

    # -----------------------------------------------------------------
    # Contract access phones.
    # -----------------------------------------------------------------
    if mode == "commercial_contract_phones":
        if value == "➕ Добавить номер":
            state["mode"] = "commercial_contract_phone_new"
            await update.message.reply_text("Введите номер, например +380671234567. Это не отправит команду GEOS.", reply_markup=kb([["⬅️ К телефонам"]]))
            return True
        phone_id = (state.get("commercial_contract_phone_map") or {}).get(value)
        if phone_id:
            await show_phone_card(update, state, int(phone_id))
            return True
        await update.message.reply_text("Выберите номер кнопкой.")
        return True

    if mode == "commercial_contract_phone_new":
        try:
            phone_id = create_phone(int(contract_id), value, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Не удалось добавить номер: {exc}")
            return True
        await update.message.reply_text("✅ Номер добавлен. GEOS не менялся.")
        await show_phone_card(update, state, phone_id)
        return True

    if mode == "commercial_contract_phone_card":
        phone_id = int(state.get("commercial_contract_phone_id"))
        row = get_phone(phone_id)
        if not row:
            await update.message.reply_text("Номер не найден.")
            return True
        if value == "✏️ Назначение":
            state["mode"] = "commercial_contract_phone_purpose"
            await update.message.reply_text("Выберите назначение номера.", reply_markup=kb(PURPOSE_MENU))
            return True
        if value == "⚪ Закрыть право":
            update_phone(phone_id, "status", "CLOSED", user_id)
            await update.message.reply_text("✅ Договорное право закрыто. GEOS не менялся.")
            await show_phone_card(update, state, phone_id)
            return True
        if value == "✅ Активировать право":
            update_phone(phone_id, "status", "ACTIVE", user_id)
            await update.message.reply_text("✅ Договорное право активно. GEOS не менялся.")
            await show_phone_card(update, state, phone_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "commercial_contract_phone_purpose":
        mapping = {"👨‍💼 Сотрудник": "STAFF", "👑 Руководитель": "MANAGER", "🛡️ Охрана": "SECURITY", "📌 Другое": "OTHER"}
        purpose = mapping.get(value)
        if not purpose:
            await update.message.reply_text("Выберите назначение кнопкой.")
            return True
        phone_id = int(state.get("commercial_contract_phone_id"))
        update_phone(phone_id, "access_purpose", purpose, user_id)
        await update.message.reply_text("✅ Назначение сохранено.")
        await show_phone_card(update, state, phone_id)
        return True

    return False
