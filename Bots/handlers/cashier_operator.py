"""
Операторский кассовый редактор ОСББ.

Реальная логика:
- O — основная касса охраны;
- K1..K6 — отдельные точки приёма консьержей;
- C — центральная касса;
- K — только исторический/отчётный агрегат, не точка нового ввода;
- деньги «из рук в руки» создают receipt + payment + cashbox operation;
- бумажка без физически подтверждённых денег создаёт только PAPER_NOTE;
- автоматического разнесения оплаты на начисления НЕТ;
- разнесение выполняет оператор вручную, с выбором конкретного начисления;
- исправление подтверждённой, но неразнесённой записи — через аннулирование
  с обратной кассовой операцией и корректирующим платежом, а не удалением.

Требуется миграция:
  migrate_cashier_operator_editor.py --apply

Подключение:
  from handlers.cashier_operator import handle_cashier_operator_text
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any
from uuid import uuid4

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for folder in (OSBB_ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_log
from unit_resolver import resolve_unit_ref


# ---------------------------------------------------------------------------
# Constants/UI
# ---------------------------------------------------------------------------

CASHBOX_ORDER = ["O", "K1", "K2", "K3", "K4", "K5", "K6", "C"]
RECEIPT_CASHBOX_CODES = ["O", "K1", "K2", "K3", "K4", "K5", "K6", "C"]
TRANSFER_CASHBOX_CODES = ["O", "K1", "K2", "K3", "K4", "K5", "K6", "C"]

CASHIER_MENU = [
    ["➕ Принять наличные", "🗒️ Внести бумажку"],
    ["🎯 Неразнесённые оплаты", "📋 Последние записи"],
    ["💸 Передача между кассами", "🏦 Остатки касс"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

RECEIPT_ORIGIN_MENU = [
    ["🤝 Из рук в руки"],
    ["🗒️ Бумажка от консьержа"],
    ["📌 Другое"],
    ["⬅️ К кассе"],
]

SERVICE_HINT_MENU = [
    ["☀️ Парковка Day", "🌙 Парковка Night"],
    ["🚗 Парковка: вид не указан"],
    ["❔ Назначение не указано"],
    ["⬅️ К кассе"],
]

CONFIRM_RECEIPT_MENU = [
    ["✅ Сохранить запись"],
    ["❌ Отменить запись"],
    ["⬅️ К кассе"],
]

PAPER_NOTE_MENU = [
    ["✅ Сохранить бумажку"],
    ["❌ Отменить запись"],
    ["⬅️ К кассе"],
]

RECEIPT_CARD_MENU = [
    ["🎯 Разнести на начисление"],
    ["🗑 Аннулировать неразнесённую"],
    ["📋 К неразнесённым", "💰 Касса"],
    ["🏠 Главное меню"],
]

TRANSFER_CONFIRM_MENU = [
    ["✅ Подтвердить передачу"],
    ["❌ Отменить передачу"],
    ["⬅️ К кассе"],
]


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def money(value: Any) -> str:
    amount = float(value or 0)
    return (
        f"{int(amount):,}".replace(",", " ")
        if amount.is_integer()
        else f"{amount:,.2f}".replace(",", " ")
    )


def next_month() -> str:
    current = date.today()
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    return f"{year:04d}-{month:02d}"


def normalize_period(value: str | None, *, required: bool = False) -> str | None:
    raw = text(value)
    if raw in {"", "-", "❔ Не указан"}:
        if required:
            raise ValueError("Укажите период в формате ГГГГ-ММ, например 2026-07.")
        return None

    raw = raw.replace("/", "-").replace(".", "-").replace("_", "-")
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        month = int(raw[5:7])
        if 1 <= month <= 12:
            return raw
    if re.fullmatch(r"\d{2}-\d{2}", raw):
        month = int(raw[:2])
        if 1 <= month <= 12:
            return f"20{raw[3:5]}-{raw[:2]}"
    raise ValueError("Период: используйте ГГГГ-ММ, например 2026-07.")


def normalize_date(value: str | None) -> str:
    raw = text(value)
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Дата: используйте ГГГГ-ММ-ДД, например 2026-06-25.") from exc
    return parsed.strftime("%Y-%m-%d")


def parse_amount(value: str) -> float:
    raw = text(value).replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError as exc:
        raise ValueError("Сумма: введите число, например 400 или 1250.50.") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    if amount > 1_000_000:
        raise ValueError("Сумма слишком велика — проверьте ввод.")
    return round(amount, 2)


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row["name"] for row in cur.fetchall()}


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def required_defaults(cur: sqlite3.Cursor, table: str) -> dict[str, Any]:
    """
    Заполняет обязательные поля ранних схем без догадок о бизнес-значении.
    Специальные поля receipt/payment/operation передаются явно.
    """
    cur.execute(f'PRAGMA table_info("{table}")')
    defaults: dict[str, Any] = {}
    for row in cur.fetchall():
        name = row["name"]
        col_type = text(row["type"]).upper()
        not_null = int(row["notnull"] or 0)
        default_value = row["dflt_value"]
        primary_key = int(row["pk"] or 0)

        if primary_key or not not_null or default_value is not None:
            continue

        upper = name.upper()
        if name in {"service_code", "base_service_code"}:
            defaults[name] = "PARKING_UNSPECIFIED"
        elif name in {"service_item_code"}:
            defaults[name] = "PARKING_UNSPECIFIED"
        elif name in {"service_name", "service_item_name", "name", "title"}:
            defaults[name] = "Без названия"
        elif "ACTIVE" in upper:
            defaults[name] = 1
        elif "AMOUNT" in upper or "BALANCE" in upper or "PRICE" in upper:
            defaults[name] = 0
        elif "DATE" in upper or "TIME" in upper or upper.endswith("_AT"):
            defaults[name] = now_db()
        elif "INT" in col_type:
            defaults[name] = 0
        elif any(token in col_type for token in ("REAL", "NUM", "DEC", "FLOAT")):
            defaults[name] = 0
        else:
            defaults[name] = ""
    return defaults


def insert_dynamic(
    cur: sqlite3.Cursor,
    table: str,
    values: dict[str, Any],
) -> int:
    columns = table_columns(cur, table)
    payload = required_defaults(cur, table)
    payload.update(values)

    insert_columns = [key for key in payload if key in columns]
    if not insert_columns:
        raise RuntimeError(f"Нет подходящих полей для вставки в {table}.")

    cur.execute(
        f"INSERT INTO {q(table)} ({', '.join(q(key) for key in insert_columns)}) "
        f"VALUES ({', '.join('?' for _ in insert_columns)})",
        tuple(payload[key] for key in insert_columns),
    )
    return int(cur.lastrowid)


def update_dynamic(
    cur: sqlite3.Cursor,
    table: str,
    row_id: int,
    values: dict[str, Any],
) -> bool:
    columns = table_columns(cur, table)
    pairs = [(key, value) for key, value in values.items() if key in columns]
    if not pairs:
        return False

    cur.execute(
        f"UPDATE {q(table)} SET "
        f"{', '.join(f'{q(key)} = ?' for key, _ in pairs)} "
        f"WHERE id = ?",
        tuple(value for _, value in pairs) + (int(row_id),),
    )
    return cur.rowcount > 0


def cashier_schema_ready() -> tuple[bool, str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        required = [
            "cashboxes",
            "cashbox_operations",
            "cashier_receipts",
            "payments",
        ]
        missing = [name for name in required if not table_exists(cur, name)]
        if missing:
            return (
                False,
                "Не выполнена миграция кассового редактора. Отсутствуют: "
                + ", ".join(missing),
            )
        return True, ""
    finally:
        conn.close()


def user_can_manage_cashier(user_id: int, is_admin: bool) -> bool:
    """
    По умолчанию нужен admin flag от parking_bot.
    Если есть bot_admins, дополнительно учитываем can_manage_payments/can_write.
    """
    if not is_admin:
        return False

    try:
        from db_access import get_admin_record  # type: ignore
        row = get_admin_record(user_id)
        if row:
            can_write = bool(row[5]) if len(row) > 5 else False
            can_manage_payments = bool(row[7]) if len(row) > 7 else False
            return can_write or can_manage_payments
    except Exception:
        pass

    return bool(is_admin)


# ---------------------------------------------------------------------------
# Cashboxes / balances
# ---------------------------------------------------------------------------

def cashbox_label(code: str) -> str:
    labels = {
        "O": "🛡 O — касса охраны (основная)",
        "K1": "K1 — консьерж 1",
        "K2": "K2 — консьерж 2",
        "K3": "K3 — консьерж 3",
        "K4": "K4 — консьерж 4",
        "K5": "K5 — консьерж 5",
        "K6": "K6 — консьерж 6",
        "C": "🏦 C — центральная касса",
        "BANK": "🏦 BANK — безнал",
        "K": "K — агрегат (не вводить)",
    }
    return labels.get(code, code)


def list_active_cashboxes(
    *,
    receipt_only: bool = False,
    transfer_only: bool = False,
) -> list[dict]:
    allowed = (
        set(RECEIPT_CASHBOX_CODES)
        if receipt_only
        else set(TRANSFER_CASHBOX_CODES)
        if transfer_only
        else set(CASHBOX_ORDER)
    )

    conn = get_conn()
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in allowed)
        cur.execute(
            f"""
            SELECT cashbox_code, cashbox_name, current_balance, is_active, comment
            FROM cashboxes
            WHERE cashbox_code IN ({placeholders})
              AND COALESCE(is_active, 1) = 1
            """,
            tuple(sorted(allowed)),
        )
        rows = {row["cashbox_code"]: dict(row) for row in cur.fetchall()}
        return [rows[code] for code in CASHBOX_ORDER if code in rows]
    finally:
        conn.close()


def calculated_cashbox_balance(cur: sqlite3.Cursor, code: str) -> float:
    cur.execute(
        "SELECT initial_balance FROM cashboxes WHERE cashbox_code = ?",
        (code,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Касса {code} не найдена.")

    initial = float(row["initial_balance"] or 0)
    cur.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN direction = 'in' THEN amount
                WHEN direction = 'out' THEN -amount
                ELSE 0
            END
        ), 0) AS delta
        FROM cashbox_operations
        WHERE cashbox_code = ?
        """,
        (code,),
    )
    return round(initial + float(cur.fetchone()["delta"] or 0), 2)


def recalc_and_store_cashbox_balance(cur: sqlite3.Cursor, code: str) -> float:
    balance = calculated_cashbox_balance(cur, code)
    cols = table_columns(cur, "cashboxes")
    if "current_balance" in cols:
        values = {"current_balance": balance}
        if "updated_at" in cols:
            values["updated_at"] = now_db()
        cur.execute(
            f"UPDATE cashboxes SET "
            f"{', '.join(f'{q(k)} = ?' for k in values)} "
            f"WHERE cashbox_code = ?",
            tuple(values.values()) + (code,),
        )
    return balance


def format_cashbox_balances() -> str:
    conn = get_conn()
    try:
        cur = conn.cursor()
        lines = ["🏦 Остатки касс", ""]
        for box in list_active_cashboxes():
            code = box["cashbox_code"]
            balance = calculated_cashbox_balance(cur, code)
            lines.append(
                f"{cashbox_label(code)}\n"
                f"   {money(balance)} грн."
            )
        lines.extend([
            "",
            "K — только исторический агрегат. Новые деньги вводятся в K1–K6.",
            "O — основная касса охраны.",
        ])
        return "\n".join(lines)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Units, receipts and charges
# ---------------------------------------------------------------------------

def unit_from_member(conn: sqlite3.Connection, apartment_id: int) -> dict | None:
    cur = conn.cursor()
    columns = table_columns(cur, "apartments")
    fields = [
        "id",
        "apartment_number",
        "unit_code" if "unit_code" in columns else "NULL AS unit_code",
        "unit_type" if "unit_type" in columns else "NULL AS unit_type",
        "entrance_number" if "entrance_number" in columns else "NULL AS entrance_number",
        "display_name" if "display_name" in columns else "NULL AS display_name",
    ]
    cur.execute(
        f"SELECT {', '.join(fields)} FROM apartments WHERE id = ?",
        (int(apartment_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def resolve_cashier_unit(unit_ref: str) -> tuple[str, list[dict], str]:
    """
    UNIT -> one row
    GROUP -> require explicit member selection
    NOT_FOUND/AMBIGUOUS -> no rows.
    """
    conn = get_conn()
    try:
        resolution = resolve_unit_ref(conn, unit_ref)
        if resolution.kind == "UNIT":
            member = resolution.members[0]
            unit = unit_from_member(conn, member.apartment_id)
            return "UNIT", [unit] if unit else [], resolution.message or ""

        if resolution.kind == "GROUP":
            units = [
                unit_from_member(conn, member.apartment_id)
                for member in resolution.members
            ]
            return "GROUP", [item for item in units if item], (
                "Введён составной код. Выберите конкретную физическую квартиру."
            )

        return resolution.kind, [], resolution.message or "Квартира не найдена."
    finally:
        conn.close()


def receipt_number_for(receipt_date: str, receipt_id: int) -> str:
    return f"R-{receipt_date.replace('-', '')}-{receipt_id:06d}"


def service_hint_label(code: str | None) -> str:
    return {
        "PARKING_DAY": "Парковка Day",
        "PARKING_NIGHT": "Парковка Night",
        "PARKING_UNSPECIFIED": "Парковка: вид не указан",
        "UNSPECIFIED": "Назначение не указано",
    }.get(text(code), text(code) or "не указано")


def origin_label(code: str | None) -> str:
    return {
        "HAND_TO_HAND": "Из рук в руки",
        "CONCIERGE_PAPER": "Бумажка от консьержа",
        "OTHER": "Другое",
    }.get(text(code), text(code) or "не указано")


def receipt_kind_label(kind: str | None) -> str:
    return {
        "CASH_RECEIVED": "Наличные физически получены",
        "PAPER_NOTE": "Только бумажка, деньги не подтверждены",
    }.get(text(kind), text(kind) or "-")


def receipt_status_label(status: str | None) -> str:
    return {
        "CONFIRMED": "✅ Подтверждено",
        "PAPER_NOTE": "🗒️ Бумажка",
        "VOID": "⚪ Аннулировано",
        "DRAFT": "🟡 Черновик",
    }.get(text(status), text(status) or "-")


def allocation_status_label(status: str | None) -> str:
    return {
        "UNALLOCATED": "🟠 Не разнесено",
        "PARTIAL": "🟡 Разнесено частично",
        "ALLOCATED": "✅ Разнесено",
        "NOT_APPLICABLE": "— не применимо",
    }.get(text(status), text(status) or "-")


def build_receipt_comment(data: dict) -> str:
    parts = [
        f"Квитанция {data.get('receipt_number') or '-'}",
        f"Источник: {origin_label(data.get('origin_kind'))}",
    ]
    if data.get("paper_ref"):
        parts.append(f"Бумага/квитанция: {data['paper_ref']}")
    if data.get("source_text"):
        parts.append(f"Дословно: {data['source_text']}")
    if data.get("period_code"):
        parts.append(f"Период: {data['period_code']}")
    if data.get("service_hint"):
        parts.append(f"Назначение: {service_hint_label(data['service_hint'])}")
    return " | ".join(parts)


def create_cashier_receipt(
    data: dict,
    *,
    operator_id: int,
) -> dict:
    """
    Atomic receipt creation.

    CASH_RECEIVED:
       receipt + positive payment + cashbox income operation.
       Allocation remains UNALLOCATED.

    PAPER_NOTE:
       receipt only, no payment and no cashbox balance change.
    """
    kind = text(data.get("receipt_kind")) or "CASH_RECEIVED"
    if kind not in {"CASH_RECEIVED", "PAPER_NOTE"}:
        raise ValueError("Недопустимый тип записи.")

    amount = parse_amount(str(data.get("amount")))
    receipt_date = normalize_date(data.get("receipt_date") or today())
    source_text = text(data.get("source_text"))
    if not source_text:
        raise ValueError(
            "Нужно записать дословную отметку на бумаге или пояснение "
            "получения денег."
        )

    unit = data.get("unit")
    if not unit:
        raise ValueError("Не выбрана квартира.")

    cashbox_code = text(data.get("cashbox_code")).upper()
    if kind == "CASH_RECEIVED":
        if cashbox_code not in RECEIPT_CASHBOX_CODES:
            raise ValueError("Для приёма выберите O, K1–K6 или C.")
    else:
        cashbox_code = ""

    service_hint = text(data.get("service_hint")) or "UNSPECIFIED"
    designation_status = (
        "EXACT"
        if service_hint in {"PARKING_DAY", "PARKING_NIGHT"}
        else "PARKING_UNSPECIFIED"
        if service_hint == "PARKING_UNSPECIFIED"
        else "UNSPECIFIED"
    )

    conn = get_conn()
    try:
        cur = conn.cursor()

        if kind == "CASH_RECEIVED":
            cur.execute(
                "SELECT is_active FROM cashboxes WHERE cashbox_code = ?",
                (cashbox_code,),
            )
            box = cur.fetchone()
            if not box or not int(box["is_active"] or 0):
                raise ValueError(f"Касса {cashbox_code} не активна.")

        temp_number = f"TMP-{uuid4().hex.upper()}"
        receipt_id = insert_dynamic(
            cur,
            "cashier_receipts",
            {
                "receipt_number": temp_number,
                "receipt_kind": kind,
                "entry_status": "DRAFT",
                "cashbox_code": cashbox_code or None,
                "receipt_date": receipt_date,
                "document_date": data.get("document_date") or None,
                "origin_kind": text(data.get("origin_kind")) or "HAND_TO_HAND",
                "origin_cashbox_code": data.get("origin_cashbox_code") or None,
                "payer_name": data.get("payer_name") or None,
                "apartment_id": int(unit["id"]),
                "apartment_number": text(unit.get("apartment_number")),
                "service_hint": service_hint,
                "period_code": data.get("period_code") or None,
                "amount": amount,
                "currency": "UAH",
                "evidence_type": data.get("evidence_type") or (
                    "PAPER" if text(data.get("origin_kind")) == "CONCIERGE_PAPER"
                    else "ORAL"
                ),
                "paper_ref": data.get("paper_ref") or None,
                "source_text": source_text,
                "designation_status": designation_status,
                "allocation_status": (
                    "UNALLOCATED" if kind == "CASH_RECEIVED" else "NOT_APPLICABLE"
                ),
                "operator_id": str(operator_id),
                "created_at": now_db(),
                "updated_at": now_db(),
            },
        )

        receipt_number = receipt_number_for(receipt_date, receipt_id)
        update_dynamic(
            cur,
            "cashier_receipts",
            receipt_id,
            {"receipt_number": receipt_number},
        )

        base_service_code = (
            service_hint
            if service_hint in {"PARKING_DAY", "PARKING_NIGHT"}
            else "PARKING_UNSPECIFIED"
            if service_hint == "PARKING_UNSPECIFIED"
            else "UNSPECIFIED"
        )
        service_type = "MONTHLY" if text(data.get("period_code")) else "UNSPECIFIED"

        if kind == "PAPER_NOTE":
            update_dynamic(
                cur,
                "cashier_receipts",
                receipt_id,
                {
                    "entry_status": "PAPER_NOTE",
                    "confirmed_by": str(operator_id),
                    "confirmed_at": now_db(),
                },
            )

            audit_log(
                conn=conn,
                operator_id=str(operator_id),
                user_id=str(operator_id),
                actor_type="operator",
                action_type="cashier_paper_note_created",
                table_name="cashier_receipts",
                row_id=receipt_id,
                field_name="receipt_kind,amount,apartment_number,period_code",
                old_value="",
                new_value=(
                    f"PAPER_NOTE,{amount},{unit.get('apartment_number')},"
                    f"{data.get('period_code') or ''}"
                ),
                source_context="cashier_operator",
                comment=(
                    "Зарегистрирована бумажка без подтверждённого физического "
                    "получения денег. Платёж и касса не изменены."
                ),
                extra={"receipt_number": receipt_number},
                commit=False,
            )
            conn.commit()
            return get_receipt(receipt_id, conn=conn)

        # Физически подтвержденные деньги: создаём payment.
        payment_id = insert_dynamic(
            cur,
            "payments",
            {
                "payment_date": receipt_date,
                "period_code": data.get("period_code") or None,
                "apartment_id": int(unit["id"]),
                "apartment_number": text(unit.get("apartment_number")),
                "service_code": base_service_code,
                "base_service_code": base_service_code,
                "service_item_code": None,
                "service_type": service_type,
                "amount": amount,
                "currency": "UAH",
                "payment_method": "cash",
                "source": "cashier_operator",
                "source_ref": f"{receipt_number}:payment",
                "comment": (
                    "Ручной приём наличных. "
                    "Авторазнесение на начисления не выполнялось. "
                    + build_receipt_comment(
                        {
                            **data,
                            "receipt_number": receipt_number,
                            "service_hint": service_hint,
                        }
                    )
                ),
                "cashbox_code": cashbox_code,
                "cashier_receipt_id": receipt_id,
                "cashier_entry_status": "CONFIRMED",
                "operator_id": str(operator_id),
                "created_at": now_db(),
            },
        )

        operation_id = insert_dynamic(
            cur,
            "cashbox_operations",
            {
                "operation_date": receipt_date,
                "cashbox_code": cashbox_code,
                "operation_type": "cash_receipt",
                "direction": "in",
                "amount": amount,
                "currency": "UAH",
                "period_code": data.get("period_code") or None,
                "apartment_number": text(unit.get("apartment_number")),
                "vehicle_id": None,
                "service_code": base_service_code,
                "base_service_code": base_service_code,
                "service_item_code": None,
                "service_type": service_type,
                "payment_id": payment_id,
                "charge_id": None,
                "source_type": "cashier_operator",
                "source_ref": f"{receipt_number}:cashbox",
                "operator_id": str(operator_id),
                "actor_type": "operator",
                "cashier_receipt_id": receipt_id,
                "comment": build_receipt_comment(
                    {
                        **data,
                        "receipt_number": receipt_number,
                        "service_hint": service_hint,
                    }
                ),
                "created_at": now_db(),
            },
        )

        update_dynamic(
            cur,
            "payments",
            payment_id,
            {
                "cashbox_operation_id": operation_id,
                "cashier_receipt_id": receipt_id,
                "cashier_entry_status": "CONFIRMED",
            },
        )
        update_dynamic(
            cur,
            "cashier_receipts",
            receipt_id,
            {
                "entry_status": "CONFIRMED",
                "payment_id": payment_id,
                "cashbox_operation_id": operation_id,
                "confirmed_by": str(operator_id),
                "confirmed_at": now_db(),
                "updated_at": now_db(),
            },
        )
        balance = recalc_and_store_cashbox_balance(cur, cashbox_code)

        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashier_receipt_confirmed",
            table_name="cashier_receipts",
            row_id=receipt_id,
            field_name="cashbox_code,amount,apartment_number,period_code,allocation_status",
            old_value="",
            new_value=(
                f"{cashbox_code},{amount},{unit.get('apartment_number')},"
                f"{data.get('period_code') or ''},UNALLOCATED"
            ),
            source_context="cashier_operator",
            comment=(
                "Наличные физически приняты. Платёж создан без "
                "автоматического разнесения на начисления."
            ),
            extra={
                "receipt_number": receipt_number,
                "payment_id": payment_id,
                "cashbox_operation_id": operation_id,
                "cashbox_balance_after": balance,
                "source_text": source_text,
            },
            commit=False,
        )
        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashier_payment_created",
            table_name="payments",
            row_id=payment_id,
            field_name="amount,source,cashbox_code",
            old_value="",
            new_value=f"{amount},cashier_operator,{cashbox_code}",
            source_context="cashier_operator",
            comment=f"Платёж создан из квитанции {receipt_number}.",
            extra={"receipt_id": receipt_id},
            commit=False,
        )
        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashbox_income_created",
            table_name="cashbox_operations",
            row_id=operation_id,
            field_name="cashbox_code,direction,amount",
            old_value="",
            new_value=f"{cashbox_code},in,{amount}",
            source_context="cashier_operator",
            comment=f"Поступление наличных по квитанции {receipt_number}.",
            extra={"receipt_id": receipt_id, "payment_id": payment_id},
            commit=False,
        )

        conn.commit()
        return get_receipt(receipt_id, conn=conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_receipt(
    receipt_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                r.*,
                c.cashbox_name
            FROM cashier_receipts r
            LEFT JOIN cashboxes c ON c.cashbox_code = r.cashbox_code
            WHERE r.id = ?
            """,
            (int(receipt_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def list_recent_receipts(*, limit: int = 20, unallocated_only: bool = False) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        where = []
        params: list[Any] = []
        if unallocated_only:
            where.extend([
                "r.receipt_kind = 'CASH_RECEIVED'",
                "r.entry_status = 'CONFIRMED'",
                "COALESCE(r.allocation_status, 'UNALLOCATED') IN ('UNALLOCATED', 'PARTIAL')",
            ])
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cur.execute(
            f"""
            SELECT
                r.id,
                r.receipt_number,
                r.receipt_kind,
                r.entry_status,
                r.cashbox_code,
                r.receipt_date,
                r.apartment_number,
                r.service_hint,
                r.period_code,
                r.amount,
                r.allocation_status,
                r.source_text,
                r.payment_id
            FROM cashier_receipts r
            {where_sql}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            tuple(params + [int(limit)]),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def receipt_allocation_total(cur: sqlite3.Cursor, payment_id: int) -> float:
    if not table_exists(cur, "payment_allocations"):
        return 0.0
    cols = table_columns(cur, "payment_allocations")
    amount_col = "amount" if "amount" in cols else (
        "allocated_amount" if "allocated_amount" in cols else None
    )
    if not amount_col:
        return 0.0
    cur.execute(
        f"SELECT COALESCE(SUM({q(amount_col)}), 0) AS total "
        f"FROM payment_allocations WHERE payment_id = ?",
        (int(payment_id),),
    )
    return float(cur.fetchone()["total"] or 0)


def payment_amount(cur: sqlite3.Cursor, payment_id: int) -> float:
    cur.execute("SELECT amount FROM payments WHERE id = ?", (int(payment_id),))
    row = cur.fetchone()
    if not row:
        raise ValueError("Платёж не найден.")
    return float(row["amount"] or 0)


def receipt_unallocated_amount(receipt: dict) -> float:
    if not receipt.get("payment_id"):
        return 0.0
    conn = get_conn()
    try:
        cur = conn.cursor()
        return max(
            0.0,
            payment_amount(cur, int(receipt["payment_id"]))
            - receipt_allocation_total(cur, int(receipt["payment_id"])),
        )
    finally:
        conn.close()


def list_open_charges_for_receipt(receipt_id: int) -> list[dict]:
    receipt = get_receipt(receipt_id)
    if not receipt:
        raise ValueError("Квитанция не найдена.")
    if not receipt.get("payment_id"):
        return []

    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "charges") or not table_exists(cur, "payment_allocations"):
            return []

        ccols = table_columns(cur, "charges")
        acols = table_columns(cur, "payment_allocations")
        acol = "amount" if "amount" in acols else (
            "allocated_amount" if "allocated_amount" in acols else None
        )
        if not acol or "amount" not in ccols:
            return []

        filters = []
        params: list[Any] = []

        if "apartment_id" in ccols and receipt.get("apartment_id"):
            filters.append("c.apartment_id = ?")
            params.append(int(receipt["apartment_id"]))
        if "apartment_number" in ccols and receipt.get("apartment_number"):
            filters.append("c.apartment_number = ?")
            params.append(text(receipt["apartment_number"]))

        if not filters:
            return []

        if receipt.get("period_code") and "period_code" in ccols:
            filters.append("c.period_code = ?")
            params.append(text(receipt["period_code"]))

        service_hint = text(receipt.get("service_hint"))
        if (
            service_hint in {"PARKING_DAY", "PARKING_NIGHT"}
            and "service_code" in ccols
        ):
            filters.append("c.service_code = ?")
            params.append(service_hint)

        status_filter = (
            "AND COALESCE(c.charge_status, '') <> 'cancelled'"
            if "charge_status" in ccols
            else (
                "AND COALESCE(c.status, '') <> 'cancelled'"
                if "status" in ccols else ""
            )
        )
        period_expr = "c.period_code" if "period_code" in ccols else "NULL"
        service_expr = "c.service_code" if "service_code" in ccols else "NULL"

        cur.execute(
            f"""
            SELECT
                c.id AS charge_id,
                {period_expr} AS period_code,
                {service_expr} AS service_code,
                c.amount AS charge_amount,
                COALESCE(SUM(pa.{q(acol)}), 0) AS allocated_amount
            FROM charges c
            LEFT JOIN payment_allocations pa ON pa.charge_id = c.id
            WHERE ({' OR '.join(filters)})
            {status_filter}
            GROUP BY c.id
            HAVING c.amount - COALESCE(SUM(pa.{q(acol)}), 0) > 0.00001
            ORDER BY COALESCE({period_expr}, '') DESC, c.id
            """,
            tuple(params),
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            item["charge_amount"] = float(item["charge_amount"] or 0)
            item["allocated_amount"] = float(item["allocated_amount"] or 0)
            item["outstanding_amount"] = max(
                0.0,
                item["charge_amount"] - item["allocated_amount"],
            )
            rows.append(item)
        return rows
    finally:
        conn.close()


def allocate_receipt_to_charge(
    receipt_id: int,
    charge_id: int,
    amount: float,
    *,
    operator_id: int,
) -> dict:
    amount = parse_amount(str(amount))
    conn = get_conn()
    try:
        cur = conn.cursor()
        receipt = get_receipt(receipt_id, conn=conn)
        if not receipt:
            raise ValueError("Квитанция не найдена.")
        if receipt.get("receipt_kind") != "CASH_RECEIVED":
            raise ValueError("Бумажка без денег не может быть разнесена.")
        if receipt.get("entry_status") != "CONFIRMED":
            raise ValueError("Разнести можно только подтверждённую квитанцию.")
        if not receipt.get("payment_id"):
            raise ValueError("У квитанции нет платежа.")

        payment_id = int(receipt["payment_id"])
        unallocated = max(
            0.0,
            payment_amount(cur, payment_id)
            - receipt_allocation_total(cur, payment_id),
        )

        charges = {item["charge_id"]: item for item in list_open_charges_for_receipt(receipt_id)}
        charge = charges.get(int(charge_id))
        if not charge:
            raise ValueError(
                "Это начисление недоступно для данной квитанции: "
                "проверьте квартиру, период и услугу."
            )

        maximum = min(unallocated, float(charge["outstanding_amount"]))
        if amount > maximum + 0.00001:
            raise ValueError(
                f"Можно разнести не более {money(maximum)} грн. "
                f"(остаток платежа {money(unallocated)}, "
                f"остаток начисления {money(charge['outstanding_amount'])})."
            )

        allocation_id = insert_dynamic(
            cur,
            "payment_allocations",
            {
                "payment_id": payment_id,
                "charge_id": int(charge_id),
                "amount": amount,
                "allocated_amount": amount,
                "created_at": now_db(),
            },
        )

        remaining = max(
            0.0,
            payment_amount(cur, payment_id)
            - receipt_allocation_total(cur, payment_id),
        )
        new_status = "ALLOCATED" if remaining < 0.00001 else "PARTIAL"
        update_dynamic(
            cur,
            "cashier_receipts",
            int(receipt_id),
            {
                "allocation_status": new_status,
                "updated_at": now_db(),
            },
        )

        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashier_receipt_manual_allocation",
            table_name="payment_allocations",
            row_id=allocation_id,
            field_name="payment_id,charge_id,amount",
            old_value="",
            new_value=f"{payment_id},{charge_id},{amount}",
            source_context="cashier_operator",
            comment=(
                "Оператор вручную разнёс оплату на выбранное начисление. "
                "Автоматического выбора не было."
            ),
            extra={
                "receipt_id": receipt_id,
                "receipt_number": receipt.get("receipt_number"),
                "receipt_remaining": remaining,
            },
            commit=False,
        )
        conn.commit()
        return {
            "allocation_id": allocation_id,
            "allocation_status": new_status,
            "remaining": remaining,
            "amount": amount,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_unallocated_receipt(receipt_id: int, *, operator_id: int, reason: str) -> dict:
    """
    Аннулирует только неразнесённую квитанцию.

    Создаёт:
    - корректирующий платёж с отрицательной суммой;
    - расходную операцию из той же кассы;
    - статус VOID у исходной квитанции.

    Если уже есть allocation — сначала надо отменить разнесение отдельной
    корректировкой; автоматическое удаление распределений запрещено.
    """
    reason = text(reason)
    if not reason:
        raise ValueError("Для аннулирования укажите причину.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        receipt = get_receipt(receipt_id, conn=conn)
        if not receipt:
            raise ValueError("Квитанция не найдена.")
        if receipt.get("receipt_kind") != "CASH_RECEIVED":
            raise ValueError("Аннулировать этим способом можно только денежную квитанцию.")
        if receipt.get("entry_status") != "CONFIRMED":
            raise ValueError("Аннулировать можно только подтверждённую квитанцию.")
        if not receipt.get("payment_id") or not receipt.get("cashbox_operation_id"):
            raise ValueError("У квитанции отсутствует связанный платёж или кассовая операция.")

        payment_id = int(receipt["payment_id"])
        allocated = receipt_allocation_total(cur, payment_id)
        if allocated > 0.00001:
            raise ValueError(
                "Эта квитанция уже разнесена. Сначала нужна отдельная "
                "корректировка распределения, удаление запрещено."
            )

        amount = float(receipt["amount"] or 0)
        cashbox_code = text(receipt["cashbox_code"])
        receipt_number = text(receipt["receipt_number"])
        base_service = (
            text(receipt.get("service_hint"))
            if text(receipt.get("service_hint")) in {"PARKING_DAY", "PARKING_NIGHT"}
            else "PARKING_UNSPECIFIED"
        )

        void_payment_id = insert_dynamic(
            cur,
            "payments",
            {
                "payment_date": today(),
                "period_code": receipt.get("period_code") or None,
                "apartment_id": receipt.get("apartment_id") or None,
                "apartment_number": receipt.get("apartment_number") or None,
                "service_code": base_service,
                "base_service_code": base_service,
                "service_type": "CORRECTION",
                "amount": -amount,
                "currency": "UAH",
                "payment_method": "cash_correction",
                "source": "cashier_operator_void",
                "source_ref": f"{receipt_number}:void-payment",
                "comment": f"Аннулирование квитанции {receipt_number}. Причина: {reason}",
                "cashbox_code": cashbox_code,
                "cashier_receipt_id": receipt_id,
                "cashier_entry_status": "VOID",
                "operator_id": str(operator_id),
                "created_at": now_db(),
            },
        )

        void_operation_id = insert_dynamic(
            cur,
            "cashbox_operations",
            {
                "operation_date": today(),
                "cashbox_code": cashbox_code,
                "operation_type": "cash_receipt_void",
                "direction": "out",
                "amount": amount,
                "currency": "UAH",
                "period_code": receipt.get("period_code") or None,
                "apartment_number": receipt.get("apartment_number") or None,
                "service_code": base_service,
                "base_service_code": base_service,
                "service_type": "CORRECTION",
                "payment_id": void_payment_id,
                "source_type": "cashier_operator_void",
                "source_ref": f"{receipt_number}:void-cashbox",
                "operator_id": str(operator_id),
                "actor_type": "operator",
                "cashier_receipt_id": receipt_id,
                "comment": f"Аннулирование квитанции {receipt_number}. Причина: {reason}",
                "created_at": now_db(),
            },
        )

        update_dynamic(
            cur,
            "payments",
            void_payment_id,
            {"cashbox_operation_id": void_operation_id},
        )
        update_dynamic(
            cur,
            "cashier_receipts",
            receipt_id,
            {
                "entry_status": "VOID",
                "void_payment_id": void_payment_id,
                "void_cashbox_operation_id": void_operation_id,
                "voided_by": str(operator_id),
                "voided_at": now_db(),
                "void_reason": reason,
                "updated_at": now_db(),
            },
        )
        balance = recalc_and_store_cashbox_balance(cur, cashbox_code)

        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashier_receipt_voided",
            table_name="cashier_receipts",
            row_id=receipt_id,
            field_name="entry_status,amount",
            old_value=f"CONFIRMED,{amount}",
            new_value="VOID,0 net",
            source_context="cashier_operator",
            comment=f"Квитанция аннулирована без удаления. Причина: {reason}",
            extra={
                "receipt_number": receipt_number,
                "void_payment_id": void_payment_id,
                "void_cashbox_operation_id": void_operation_id,
                "cashbox_balance_after": balance,
            },
            commit=False,
        )
        conn.commit()
        return {
            "receipt_number": receipt_number,
            "void_payment_id": void_payment_id,
            "void_operation_id": void_operation_id,
            "cashbox_balance": balance,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_cashbox_transfer(
    *,
    from_code: str,
    to_code: str,
    amount: float,
    transfer_date: str,
    comment: str,
    operator_id: int,
) -> dict:
    from_code = text(from_code).upper()
    to_code = text(to_code).upper()
    amount = parse_amount(str(amount))
    transfer_date = normalize_date(transfer_date)
    comment = text(comment)

    if from_code == to_code:
        raise ValueError("Источник и получатель передачи совпадают.")
    if from_code not in TRANSFER_CASHBOX_CODES or to_code not in TRANSFER_CASHBOX_CODES:
        raise ValueError("Для передачи доступны O, K1–K6 и C.")
    if not comment:
        raise ValueError("Опишите основание передачи: кто передал и что именно.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        transfer_ref = f"TR-{uuid4().hex.upper()}"

        out_id = insert_dynamic(
            cur,
            "cashbox_operations",
            {
                "operation_date": transfer_date,
                "cashbox_code": from_code,
                "operation_type": "cash_transfer_out",
                "direction": "out",
                "amount": amount,
                "currency": "UAH",
                "service_code": "CASH_TRANSFER",
                "base_service_code": "CASH_TRANSFER",
                "service_type": "INTERNAL",
                "source_type": "cashier_operator_transfer",
                "source_ref": f"{transfer_ref}:out",
                "transfer_group_ref": transfer_ref,
                "operator_id": str(operator_id),
                "actor_type": "operator",
                "comment": f"Передача {from_code} → {to_code}. {comment}",
                "created_at": now_db(),
            },
        )
        in_id = insert_dynamic(
            cur,
            "cashbox_operations",
            {
                "operation_date": transfer_date,
                "cashbox_code": to_code,
                "operation_type": "cash_transfer_in",
                "direction": "in",
                "amount": amount,
                "currency": "UAH",
                "service_code": "CASH_TRANSFER",
                "base_service_code": "CASH_TRANSFER",
                "service_type": "INTERNAL",
                "source_type": "cashier_operator_transfer",
                "source_ref": f"{transfer_ref}:in",
                "transfer_group_ref": transfer_ref,
                "operator_id": str(operator_id),
                "actor_type": "operator",
                "comment": f"Получено из {from_code}. {comment}",
                "created_at": now_db(),
            },
        )
        from_balance = recalc_and_store_cashbox_balance(cur, from_code)
        to_balance = recalc_and_store_cashbox_balance(cur, to_code)

        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashbox_transfer_created",
            table_name="cashbox_operations",
            row_id=f"{out_id},{in_id}",
            field_name="from_cashbox,to_cashbox,amount",
            old_value="",
            new_value=f"{from_code},{to_code},{amount}",
            source_context="cashier_operator",
            comment=comment,
            extra={
                "transfer_ref": transfer_ref,
                "out_operation_id": out_id,
                "in_operation_id": in_id,
                "from_balance_after": from_balance,
                "to_balance_after": to_balance,
            },
            commit=False,
        )
        conn.commit()
        return {
            "out_id": out_id,
            "in_id": in_id,
            "from_balance": from_balance,
            "to_balance": to_balance,
            "transfer_ref": transfer_ref,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_receipt(receipt: dict) -> str:
    unallocated = receipt_unallocated_amount(receipt)
    lines = [
        f"🧾 Квитанция {receipt.get('receipt_number')}",
        "",
        f"Тип: {receipt_kind_label(receipt.get('receipt_kind'))}",
        f"Статус: {receipt_status_label(receipt.get('entry_status'))}",
        f"Дата приёма / записи: {receipt.get('receipt_date') or '-'}",
        f"Касса: {cashbox_label(text(receipt.get('cashbox_code'))) if receipt.get('cashbox_code') else '—'}",
        f"Источник: {origin_label(receipt.get('origin_kind'))}",
        f"Квартира: {receipt.get('apartment_number') or '-'}",
        f"Период: {receipt.get('period_code') or 'не указан'}",
        f"Назначение: {service_hint_label(receipt.get('service_hint'))}",
        f"Сумма: {money(receipt.get('amount'))} грн.",
        f"Разнесение: {allocation_status_label(receipt.get('allocation_status'))}",
    ]

    if receipt.get("payment_id"):
        lines.append(f"Остаток неразнесённой оплаты: {money(unallocated)} грн.")

    if receipt.get("paper_ref"):
        lines.append(f"Бумага / квитанция: {receipt.get('paper_ref')}")
    lines.extend([
        "",
        f"Основание: {receipt.get('source_text') or '—'}",
    ])
    if receipt.get("void_reason"):
        lines.append(f"Причина аннулирования: {receipt.get('void_reason')}")
    lines.extend([
        "",
        "Автоматического разнесения на начисления не было.",
    ])
    return "\n".join(lines)


def format_receipt_list(receipts: list[dict], *, title: str) -> str:
    lines = [title, ""]
    if not receipts:
        lines.append("Записей нет.")
        return "\n".join(lines)

    for item in receipts:
        lines.append(
            f"#{item['id']} | {item.get('receipt_number') or '-'} | "
            f"кв. {item.get('apartment_number') or '-'} | "
            f"{money(item.get('amount'))} грн.\n"
            f"{receipt_status_label(item.get('entry_status'))} | "
            f"{allocation_status_label(item.get('allocation_status'))} | "
            f"{item.get('period_code') or 'без периода'}"
        )
    return "\n\n".join(lines)


def format_charge_list(receipt: dict, charges: list[dict]) -> str:
    lines = [
        f"🎯 Разнести квитанцию {receipt.get('receipt_number')}",
        "",
        f"Не разнесено: {money(receipt_unallocated_amount(receipt))} грн.",
        f"Квартира: {receipt.get('apartment_number') or '-'}",
        f"Период: {receipt.get('period_code') or 'не указан'}",
        f"Назначение квитанции: {service_hint_label(receipt.get('service_hint'))}",
        "",
    ]
    if not charges:
        lines.append(
            "Подходящих открытых начислений не найдено.\n\n"
            "Не создавайте начисление задним числом наугад. "
            "Оставьте оплату неразнесённой до отдельной проверки."
        )
        return "\n".join(lines)

    for item in charges:
        lines.append(
            f"#{item['charge_id']} | {item.get('period_code') or '-'} | "
            f"{service_hint_label(item.get('service_code'))}\n"
            f"Начислено {money(item['charge_amount'])} | "
            f"остаток {money(item['outstanding_amount'])} грн."
        )
    return "\n\n".join(lines)


def format_cashier_home() -> str:
    return (
        "💰 Касса\n\n"
        "O — основная касса охраны.\n"
        "K1–K6 — отдельные точки приёма консьержей.\n"
        "C — центральная касса.\n\n"
        "Правило: наличные и бумажка — разные сущности.\n"
        "Если деньги физически не получены, вводится только бумажка и "
        "остаток кассы не меняется."
    )


# ---------------------------------------------------------------------------
# Telegram UI
# ---------------------------------------------------------------------------

def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == "cashier_operator":
        return value
    if create:
        value = {"_module": "cashier_operator", "mode": "cashier_home"}
        user_states[user_id] = value
        return value
    return None


def _legacy_state_active(user_states: dict, user_id: int) -> bool:
    value = user_states.get(user_id)
    return value is not None and not (
        isinstance(value, dict) and value.get("_module") == "cashier_operator"
    )


def receipt_cashbox_buttons() -> list[list[str]]:
    rows = []
    boxes = list_active_cashboxes(receipt_only=True)
    label_map = {cashbox_label(box["cashbox_code"]): box["cashbox_code"] for box in boxes}
    labels = list(label_map)
    for index in range(0, len(labels), 2):
        rows.append(labels[index:index + 2])
    rows.append(["⬅️ К кассе"])
    return rows


def transfer_cashbox_buttons(codes: list[str], *, back: str = "⬅️ К кассе") -> list[list[str]]:
    rows = []
    labels = [cashbox_label(code) for code in codes]
    for index in range(0, len(labels), 2):
        rows.append(labels[index:index + 2])
    rows.append([back])
    return rows


def code_from_cashbox_label(label: str) -> str | None:
    label = text(label)
    for code in CASHBOX_ORDER + ["BANK", "K"]:
        if label == cashbox_label(code):
            return code
    return None


async def show_cashier_home(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({"_module": "cashier_operator", "mode": "cashier_home"})
    await update.message.reply_text(
        format_cashier_home(),
        reply_markup=kb(CASHIER_MENU),
    )


async def start_receipt(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    kind: str,
) -> None:
    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({
        "_module": "cashier_operator",
        "mode": "cashier_receipt_origin",
        "receipt_kind": kind,
        "receipt_date": today(),
        "period_code": None,
        "service_hint": "UNSPECIFIED",
    })

    if kind == "CASH_RECEIVED":
        state["mode"] = "cashier_receipt_cashbox"
        await update.message.reply_text(
            "💵 Новая наличная оплата\n\n"
            "Куда физически поступили деньги?\n\n"
            "O выбирайте, когда деньги приняты охраной из рук в руки.\n"
            "K1–K6 выбирайте, когда деньги физически остаются у конкретного "
            "консьержа до передачи.",
            reply_markup=kb(receipt_cashbox_buttons()),
        )
    else:
        await update.message.reply_text(
            "🗒️ Бумажка без денег\n\n"
            "Эта запись не является оплатой и не меняет остаток кассы.\n"
            "Выберите происхождение бумажки.",
            reply_markup=kb(RECEIPT_ORIGIN_MENU),
        )


async def show_receipt_card(
    update: Update,
    user_states: dict,
    user_id: int,
    receipt_id: int,
) -> None:
    receipt = get_receipt(receipt_id)
    if not receipt:
        await update.message.reply_text("Квитанция не найдена.")
        return

    state = _state(user_states, user_id, create=True)
    state["mode"] = "cashier_receipt_card"
    state["cashier_receipt_id"] = int(receipt_id)

    rows = []
    if (
        receipt.get("receipt_kind") == "CASH_RECEIVED"
        and receipt.get("entry_status") == "CONFIRMED"
        and receipt.get("payment_id")
    ):
        rows.append(["🎯 Разнести на начисление"])
        if receipt.get("allocation_status") == "UNALLOCATED":
            rows.append(["🗑 Аннулировать неразнесённую"])

    rows.extend([
        ["📋 К неразнесённым", "💰 Касса"],
        ["🏠 Главное меню"],
    ])

    await update.message.reply_text(
        format_receipt(receipt),
        reply_markup=kb(rows),
    )


async def show_receipt_list(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    unallocated_only: bool,
) -> None:
    receipts = list_recent_receipts(limit=30, unallocated_only=unallocated_only)
    title = (
        "🎯 Неразнесённые оплаты"
        if unallocated_only
        else "📋 Последние кассовые записи"
    )

    state = _state(user_states, user_id, create=True)
    state["mode"] = "cashier_receipt_list"
    state["cashier_receipt_list_unallocated"] = bool(unallocated_only)

    mapping: dict[str, int] = {}
    buttons: list[list[str]] = []
    for item in receipts:
        label = (
            f"🧾 {item.get('receipt_number') or '#' + str(item['id'])} | "
            f"кв.{item.get('apartment_number') or '-'} | "
            f"{money(item.get('amount'))}"
        )
        if label in mapping:
            label += f" [{item['id']}]"
        mapping[label] = int(item["id"])
        buttons.append([label])

    state["cashier_receipt_buttons"] = mapping
    buttons.extend([
        ["💰 Касса"],
        ["🏠 Главное меню"],
    ])

    await update.message.reply_text(
        format_receipt_list(receipts, title=title),
        reply_markup=kb(buttons),
    )


async def start_transfer(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({
        "_module": "cashier_operator",
        "mode": "cashier_transfer_from",
        "transfer_date": today(),
    })
    await update.message.reply_text(
        "💸 Передача между кассами\n\n"
        "Выберите, откуда физически передаются деньги.",
        reply_markup=kb(transfer_cashbox_buttons(TRANSFER_CASHBOX_CODES)),
    )


async def show_open_charges(
    update: Update,
    user_states: dict,
    user_id: int,
    receipt_id: int,
) -> None:
    receipt = get_receipt(receipt_id)
    if not receipt:
        await update.message.reply_text("Квитанция не найдена.")
        return

    charges = list_open_charges_for_receipt(receipt_id)
    state = _state(user_states, user_id, create=True)
    state["mode"] = "cashier_allocation_charge_list"
    state["cashier_receipt_id"] = int(receipt_id)

    mapping: dict[str, int] = {}
    buttons: list[list[str]] = []
    for charge in charges:
        label = (
            f"🧾 #{charge['charge_id']} | {charge.get('period_code') or '-'} | "
            f"{service_hint_label(charge.get('service_code'))} | "
            f"{money(charge['outstanding_amount'])}"
        )
        mapping[label] = int(charge["charge_id"])
        buttons.append([label])

    state["cashier_charge_buttons"] = mapping
    buttons.extend([
        ["⬅️ К квитанции", "💰 Касса"],
        ["🏠 Главное меню"],
    ])

    await update.message.reply_text(
        format_charge_list(receipt, charges),
        reply_markup=kb(buttons),
    )


async def preview_receipt(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    state = _state(user_states, user_id, create=True)
    unit = state.get("receipt_unit")
    if not unit:
        await update.message.reply_text("Квартира не выбрана.")
        return

    kind = state["receipt_kind"]
    label = (
        "💵 Предпросмотр наличной оплаты"
        if kind == "CASH_RECEIVED"
        else "🗒️ Предпросмотр бумажки"
    )
    lines = [
        label,
        "",
        f"Тип: {receipt_kind_label(kind)}",
        f"Дата: {state.get('receipt_date')}",
        f"Касса: {cashbox_label(state.get('cashbox_code')) if state.get('cashbox_code') else '—'}",
        f"Источник: {origin_label(state.get('origin_kind'))}",
        f"Квартира: {unit.get('apartment_number')}",
        f"Период: {state.get('period_code') or 'не указан'}",
        f"Назначение: {service_hint_label(state.get('service_hint'))}",
        f"Сумма: {money(state.get('amount'))} грн.",
        f"Основание: {state.get('source_text')}",
        "",
    ]
    if kind == "CASH_RECEIVED":
        lines.extend([
            "Будет создана квитанция, платёж и приход в кассу.",
            "Разнесение на начисления НЕ будет выполнено автоматически.",
        ])
        menu = CONFIRM_RECEIPT_MENU
    else:
        lines.extend([
            "Будет создана только запись бумажки.",
            "Платёж и остаток кассы не изменятся.",
        ])
        menu = PAPER_NOTE_MENU

    state["mode"] = "cashier_receipt_confirm"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(menu))


async def preview_transfer(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    state = _state(user_states, user_id, create=True)
    lines = [
        "💸 Предпросмотр передачи",
        "",
        f"Дата: {state.get('transfer_date')}",
        f"Из: {cashbox_label(state.get('transfer_from'))}",
        f"В: {cashbox_label(state.get('transfer_to'))}",
        f"Сумма: {money(state.get('transfer_amount'))} грн.",
        f"Основание: {state.get('transfer_comment')}",
        "",
        "Будут созданы две кассовые операции: расход из источника и приход в получатель.",
    ]
    state["mode"] = "cashier_transfer_confirm"
    await update.message.reply_text("\n".join(lines), reply_markup=kb(TRANSFER_CONFIRM_MENU))


# ---------------------------------------------------------------------------
# State router
# ---------------------------------------------------------------------------

async def handle_cashier_operator_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    is_admin: bool = False,
) -> bool:
    message_text = text(message_text)
    state = _state(user_states, user_id, create=False)
    mode = text(state.get("mode")) if state else ""

    # Do not steal existing legacy state machines.
    if _legacy_state_active(user_states, user_id):
        return False

    entry_buttons = {"💰 Касса", "💰 Cashier"}

    if message_text in entry_buttons:
        if not user_can_manage_cashier(user_id, is_admin):
            await update.message.reply_text("Нет доступа к кассовому редактору.")
            return True

        ready, reason = cashier_schema_ready()
        if not ready:
            await update.message.reply_text("⚠️ " + reason)
            return True

        await show_cashier_home(update, user_states, user_id)
        return True

    if not mode.startswith("cashier_"):
        return False

    if message_text == "🏠 Главное меню":
        user_states.pop(user_id, None)
        return False

    # Generic back to cashier.
    if message_text in {"💰 Касса", "⬅️ К кассе"}:
        await show_cashier_home(update, user_states, user_id)
        return True

    # --------------------- home ---------------------
    if mode == "cashier_home":
        if message_text == "➕ Принять наличные":
            await start_receipt(update, user_states, user_id, kind="CASH_RECEIVED")
            return True
        if message_text == "🗒️ Внести бумажку":
            await start_receipt(update, user_states, user_id, kind="PAPER_NOTE")
            return True
        if message_text == "🎯 Неразнесённые оплаты":
            await show_receipt_list(update, user_states, user_id, unallocated_only=True)
            return True
        if message_text == "📋 Последние записи":
            await show_receipt_list(update, user_states, user_id, unallocated_only=False)
            return True
        if message_text == "🏦 Остатки касс":
            await update.message.reply_text(
                format_cashbox_balances(),
                reply_markup=kb(CASHIER_MENU),
            )
            return True
        if message_text == "💸 Передача между кассами":
            await start_transfer(update, user_states, user_id)
            return True

        await update.message.reply_text("Выберите действие кнопкой.", reply_markup=kb(CASHIER_MENU))
        return True

    # --------------------- new receipt ---------------------
    if mode == "cashier_receipt_cashbox":
        code = code_from_cashbox_label(message_text)
        if code not in RECEIPT_CASHBOX_CODES:
            await update.message.reply_text(
                "Выберите точку приёма кнопкой.",
                reply_markup=kb(receipt_cashbox_buttons()),
            )
            return True
        state["cashbox_code"] = code
        state["mode"] = "cashier_receipt_origin"
        await update.message.reply_text(
            "Как зафиксировано происхождение денег?",
            reply_markup=kb(RECEIPT_ORIGIN_MENU),
        )
        return True

    if mode == "cashier_receipt_origin":
        origins = {
            "🤝 Из рук в руки": "HAND_TO_HAND",
            "🗒️ Бумажка от консьержа": "CONCIERGE_PAPER",
            "📌 Другое": "OTHER",
        }
        origin = origins.get(message_text)
        if not origin:
            await update.message.reply_text(
                "Выберите происхождение кнопкой.",
                reply_markup=kb(RECEIPT_ORIGIN_MENU),
            )
            return True

        state["origin_kind"] = origin
        state["mode"] = "cashier_receipt_date_choice"
        await update.message.reply_text(
            "Дата фактического приёма денег или регистрации бумажки:",
            reply_markup=kb([
                [f"📅 Сегодня: {today()}"],
                ["✏️ Ввести другую дату"],
                ["⬅️ К кассе"],
            ]),
        )
        return True

    if mode == "cashier_receipt_date_choice":
        if message_text.startswith("📅 Сегодня:"):
            state["receipt_date"] = today()
            state["mode"] = "cashier_receipt_unit"
            await update.message.reply_text(
                "Введите номер квартиры.\n\n"
                "Для составной группы нужно будет выбрать конкретную физическую квартиру.",
                reply_markup=kb([["⬅️ К кассе"]]),
            )
            return True
        if message_text == "✏️ Ввести другую дату":
            state["mode"] = "cashier_receipt_date_manual"
            await update.message.reply_text(
                "Введите дату в формате ГГГГ-ММ-ДД.",
                reply_markup=kb([["⬅️ К кассе"]]),
            )
            return True
        await update.message.reply_text("Выберите дату кнопкой.")
        return True

    if mode == "cashier_receipt_date_manual":
        try:
            state["receipt_date"] = normalize_date(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = "cashier_receipt_unit"
        await update.message.reply_text(
            "Введите номер квартиры.",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_receipt_unit":
        kind, units, explanation = resolve_cashier_unit(message_text)
        if kind == "UNIT" and units:
            unit = units[0]
            if text(unit.get("unit_type")) and text(unit.get("unit_type")) == "TECHNICAL":
                await update.message.reply_text("Техническое помещение нельзя выбрать для этой операции.")
                return True
            state["receipt_unit"] = unit
            state["mode"] = "cashier_receipt_period_choice"
            await update.message.reply_text(
                "За какой период указано на бумажке/со слов?",
                reply_markup=kb([
                    [f"📅 Следующий месяц: {next_month()}"],
                    ["✏️ Ввести месяц", "❔ Период не указан"],
                    ["⬅️ К кассе"],
                ]),
            )
            return True

        if kind == "GROUP" and units:
            mapping = {}
            buttons = []
            for unit in units:
                label = f"🏠 кв. {unit.get('apartment_number')}"
                mapping[label] = unit
                buttons.append([label])
            state["cashier_unit_member_map"] = mapping
            state["mode"] = "cashier_receipt_unit_member"
            await update.message.reply_text(
                explanation,
                reply_markup=kb(buttons + [["⬅️ К кассе"]]),
            )
            return True

        await update.message.reply_text(
            f"Не удалось выбрать квартиру: {explanation}",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_receipt_unit_member":
        unit = (state.get("cashier_unit_member_map") or {}).get(message_text)
        if not unit:
            await update.message.reply_text("Выберите конкретную квартиру кнопкой.")
            return True
        state["receipt_unit"] = unit
        state.pop("cashier_unit_member_map", None)
        state["mode"] = "cashier_receipt_period_choice"
        await update.message.reply_text(
            "За какой период указано на бумажке/со слов?",
            reply_markup=kb([
                [f"📅 Следующий месяц: {next_month()}"],
                ["✏️ Ввести месяц", "❔ Период не указан"],
                ["⬅️ К кассе"],
            ]),
        )
        return True

    if mode == "cashier_receipt_period_choice":
        if message_text.startswith("📅 Следующий месяц:"):
            state["period_code"] = next_month()
        elif message_text == "❔ Период не указан":
            state["period_code"] = None
        elif message_text == "✏️ Ввести месяц":
            state["mode"] = "cashier_receipt_period_manual"
            await update.message.reply_text(
                "Введите период в формате ГГГГ-ММ, например 2026-07.",
                reply_markup=kb([["⬅️ К кассе"]]),
            )
            return True
        else:
            await update.message.reply_text("Выберите период кнопкой.")
            return True

        state["mode"] = "cashier_receipt_service"
        await update.message.reply_text(
            "Что указано как назначение оплаты?",
            reply_markup=kb(SERVICE_HINT_MENU),
        )
        return True

    if mode == "cashier_receipt_period_manual":
        try:
            state["period_code"] = normalize_period(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = "cashier_receipt_service"
        await update.message.reply_text(
            "Что указано как назначение оплаты?",
            reply_markup=kb(SERVICE_HINT_MENU),
        )
        return True

    if mode == "cashier_receipt_service":
        hints = {
            "☀️ Парковка Day": "PARKING_DAY",
            "🌙 Парковка Night": "PARKING_NIGHT",
            "🚗 Парковка: вид не указан": "PARKING_UNSPECIFIED",
            "❔ Назначение не указано": "UNSPECIFIED",
        }
        hint = hints.get(message_text)
        if not hint:
            await update.message.reply_text(
                "Выберите назначение кнопкой.",
                reply_markup=kb(SERVICE_HINT_MENU),
            )
            return True
        state["service_hint"] = hint
        state["mode"] = "cashier_receipt_amount"
        await update.message.reply_text(
            "Введите сумму наличных.",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_receipt_amount":
        try:
            state["amount"] = parse_amount(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True

        state["mode"] = "cashier_receipt_source_text"
        await update.message.reply_text(
            "Введите дословную отметку с бумажки или пояснение.\n\n"
            "Пример: «кв. 174, за июль, передано охране».\n"
            "Это обязательное поле: по нему потом проверяется происхождение денег.",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_receipt_source_text":
        if message_text == "-":
            await update.message.reply_text(
                "Нельзя оставить основание пустым. Укажите хотя бы: "
                "«передано из рук в руки, без бумажки».",
            )
            return True
        state["source_text"] = message_text
        await preview_receipt(update, user_states, user_id)
        return True

    if mode == "cashier_receipt_confirm":
        if message_text == "❌ Отменить запись":
            await show_cashier_home(update, user_states, user_id)
            return True
        if message_text != (
            "✅ Сохранить запись"
            if state.get("receipt_kind") == "CASH_RECEIVED"
            else "✅ Сохранить бумажку"
        ):
            await update.message.reply_text("Подтвердите или отмените запись кнопкой.")
            return True

        try:
            receipt = create_cashier_receipt(
                {
                    "receipt_kind": state.get("receipt_kind"),
                    "cashbox_code": state.get("cashbox_code"),
                    "receipt_date": state.get("receipt_date"),
                    "origin_kind": state.get("origin_kind"),
                    "unit": state.get("receipt_unit"),
                    "period_code": state.get("period_code"),
                    "service_hint": state.get("service_hint"),
                    "amount": state.get("amount"),
                    "source_text": state.get("source_text"),
                },
                operator_id=user_id,
            )
        except Exception as exc:
            await update.message.reply_text(f"Не удалось сохранить: {exc}")
            return True

        await update.message.reply_text(
            "✅ Запись сохранена.\n\n"
            f"Квитанция: {receipt.get('receipt_number')}\n"
            f"Статус: {receipt_status_label(receipt.get('entry_status'))}\n"
            f"Разнесение: {allocation_status_label(receipt.get('allocation_status'))}"
        )
        await show_receipt_card(update, user_states, user_id, int(receipt["id"]))
        return True

    # --------------------- receipt lists/cards ---------------------
    if mode == "cashier_receipt_list":
        selected = (state.get("cashier_receipt_buttons") or {}).get(message_text)
        if selected:
            await show_receipt_card(update, user_states, user_id, int(selected))
            return True
        await update.message.reply_text("Выберите квитанцию кнопкой.")
        return True

    if mode == "cashier_receipt_card":
        receipt_id = int(state.get("cashier_receipt_id"))
        if message_text == "🎯 Разнести на начисление":
            await show_open_charges(update, user_states, user_id, receipt_id)
            return True
        if message_text == "📋 К неразнесённым":
            await show_receipt_list(update, user_states, user_id, unallocated_only=True)
            return True
        if message_text == "🗑 Аннулировать неразнесённую":
            state["mode"] = "cashier_receipt_void_reason"
            await update.message.reply_text(
                "Введите причину аннулирования.\n\n"
                "Запись не удалится: будут созданы обратные операции.",
                reply_markup=kb([["⬅️ К квитанции"], ["🏠 Главное меню"]]),
            )
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "cashier_receipt_void_reason":
        if message_text == "⬅️ К квитанции":
            await show_receipt_card(
                update, user_states, user_id, int(state.get("cashier_receipt_id"))
            )
            return True
        try:
            result = void_unallocated_receipt(
                int(state.get("cashier_receipt_id")),
                operator_id=user_id,
                reason=message_text,
            )
        except Exception as exc:
            await update.message.reply_text(f"Аннулирование не выполнено: {exc}")
            return True
        await update.message.reply_text(
            "✅ Квитанция аннулирована без удаления.\n\n"
            f"Касса: {money(result['cashbox_balance'])} грн.\n"
            f"Корректирующий платёж: #{result['void_payment_id']}\n"
            f"Обратная кассовая операция: #{result['void_operation_id']}"
        )
        await show_receipt_card(
            update, user_states, user_id, int(state.get("cashier_receipt_id"))
        )
        return True

    # --------------------- manual allocation ---------------------
    if mode == "cashier_allocation_charge_list":
        if message_text == "⬅️ К квитанции":
            await show_receipt_card(
                update, user_states, user_id, int(state.get("cashier_receipt_id"))
            )
            return True

        charge_id = (state.get("cashier_charge_buttons") or {}).get(message_text)
        if not charge_id:
            await update.message.reply_text("Выберите начисление кнопкой.")
            return True

        receipt = get_receipt(int(state.get("cashier_receipt_id")))
        charges = {
            item["charge_id"]: item
            for item in list_open_charges_for_receipt(int(state.get("cashier_receipt_id")))
        }
        charge = charges.get(int(charge_id))
        if not receipt or not charge:
            await update.message.reply_text("Начисление недоступно. Откройте список заново.")
            return True

        maximum = min(
            receipt_unallocated_amount(receipt),
            float(charge["outstanding_amount"]),
        )
        state["cashier_selected_charge_id"] = int(charge_id)
        state["mode"] = "cashier_allocation_amount"
        await update.message.reply_text(
            "Введите сумму ручного разнесения.\n\n"
            f"Квитанция: {money(receipt_unallocated_amount(receipt))} грн. не разнесено.\n"
            f"Начисление: {money(charge['outstanding_amount'])} грн. остаток.\n"
            f"Максимум сейчас: {money(maximum)} грн.",
            reply_markup=kb([["⬅️ К начислениям"], ["🏠 Главное меню"]]),
        )
        return True

    if mode == "cashier_allocation_amount":
        if message_text == "⬅️ К начислениям":
            await show_open_charges(
                update, user_states, user_id, int(state.get("cashier_receipt_id"))
            )
            return True
        try:
            result = allocate_receipt_to_charge(
                int(state.get("cashier_receipt_id")),
                int(state.get("cashier_selected_charge_id")),
                parse_amount(message_text),
                operator_id=user_id,
            )
        except Exception as exc:
            await update.message.reply_text(f"Разнесение не выполнено: {exc}")
            return True

        await update.message.reply_text(
            "✅ Оплата разнесена вручную.\n\n"
            f"Сумма: {money(result['amount'])} грн.\n"
            f"Статус квитанции: {allocation_status_label(result['allocation_status'])}\n"
            f"Остаток квитанции: {money(result['remaining'])} грн."
        )
        await show_receipt_card(
            update, user_states, user_id, int(state.get("cashier_receipt_id"))
        )
        return True

    # --------------------- transfer ---------------------
    if mode == "cashier_transfer_from":
        from_code = code_from_cashbox_label(message_text)
        if from_code not in TRANSFER_CASHBOX_CODES:
            await update.message.reply_text("Выберите кассу-источник кнопкой.")
            return True
        state["transfer_from"] = from_code
        state["mode"] = "cashier_transfer_to"
        target_codes = [code for code in TRANSFER_CASHBOX_CODES if code != from_code]
        await update.message.reply_text(
            "Выберите, куда физически передаются деньги.",
            reply_markup=kb(transfer_cashbox_buttons(target_codes)),
        )
        return True

    if mode == "cashier_transfer_to":
        to_code = code_from_cashbox_label(message_text)
        if to_code not in TRANSFER_CASHBOX_CODES or to_code == state.get("transfer_from"):
            await update.message.reply_text("Выберите другую кассу-получатель.")
            return True
        state["transfer_to"] = to_code
        state["mode"] = "cashier_transfer_date_choice"
        await update.message.reply_text(
            "Дата фактической передачи:",
            reply_markup=kb([
                [f"📅 Сегодня: {today()}"],
                ["✏️ Ввести другую дату"],
                ["⬅️ К кассе"],
            ]),
        )
        return True

    if mode == "cashier_transfer_date_choice":
        if message_text.startswith("📅 Сегодня:"):
            state["transfer_date"] = today()
            state["mode"] = "cashier_transfer_amount"
            await update.message.reply_text(
                "Введите сумму передачи.",
                reply_markup=kb([["⬅️ К кассе"]]),
            )
            return True
        if message_text == "✏️ Ввести другую дату":
            state["mode"] = "cashier_transfer_date_manual"
            await update.message.reply_text(
                "Введите дату в формате ГГГГ-ММ-ДД.",
                reply_markup=kb([["⬅️ К кассе"]]),
            )
            return True
        await update.message.reply_text("Выберите дату кнопкой.")
        return True

    if mode == "cashier_transfer_date_manual":
        try:
            state["transfer_date"] = normalize_date(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = "cashier_transfer_amount"
        await update.message.reply_text(
            "Введите сумму передачи.",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_transfer_amount":
        try:
            state["transfer_amount"] = parse_amount(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True
        state["mode"] = "cashier_transfer_comment"
        await update.message.reply_text(
            "Укажите основание передачи.\n\n"
            "Пример: «Консьерж 4 передал охране наличные по журналу за 25.06».",
            reply_markup=kb([["⬅️ К кассе"]]),
        )
        return True

    if mode == "cashier_transfer_comment":
        if message_text == "-":
            await update.message.reply_text("Основание передачи обязательно.")
            return True
        state["transfer_comment"] = message_text
        await preview_transfer(update, user_states, user_id)
        return True

    if mode == "cashier_transfer_confirm":
        if message_text == "❌ Отменить передачу":
            await show_cashier_home(update, user_states, user_id)
            return True
        if message_text != "✅ Подтвердить передачу":
            await update.message.reply_text("Подтвердите или отмените передачу кнопкой.")
            return True
        try:
            result = create_cashbox_transfer(
                from_code=state["transfer_from"],
                to_code=state["transfer_to"],
                amount=state["transfer_amount"],
                transfer_date=state["transfer_date"],
                comment=state["transfer_comment"],
                operator_id=user_id,
            )
        except Exception as exc:
            await update.message.reply_text(f"Передача не сохранена: {exc}")
            return True
        await update.message.reply_text(
            "✅ Передача сохранена.\n\n"
            f"Операции: #{result['out_id']} и #{result['in_id']}\n"
            f"{state['transfer_from']}: {money(result['from_balance'])} грн.\n"
            f"{state['transfer_to']}: {money(result['to_balance'])} грн."
        )
        await show_cashier_home(update, user_states, user_id)
        return True

    # Generic nested screen fallback.
    await update.message.reply_text(
        "Выберите действие кнопкой.",
        reply_markup=kb([["💰 Касса"], ["🏠 Главное меню"]]),
    )
    return True
