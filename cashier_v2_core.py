"""
Общее ядро «Касса и платежи v2».

Используется:
- операторским редактором cashier_operator_v2.py;
- клиентским кабинетом client_portal_v2.py.

Все операции ведутся в одной БД из config.py.
Уведомления жителей не являются платежами.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BOTS) not in sys.path:
    sys.path.insert(0, str(BOTS))

from config import paths, USE_TEST_DB
from audit_logger import audit_log
from unit_resolver import resolve_unit_ref

# Reuse battle-tested dynamic schema helpers from v1.  This does not route UI
# through v1; it only avoids duplicating compatibility SQL.
from handlers import cashier_operator as v1


CASH_CODES = ("O", "K1", "K2", "K3", "K4", "K5", "K6", "C")
BANK_CODE = "BANK"


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return date.today().strftime("%Y-%m-%d")


def next_month() -> str:
    value = date.today()
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return f"{year:04d}-{month:02d}"


def money(value: Any) -> str:
    amount = float(value or 0)
    return (
        f"{int(amount):,}".replace(",", " ")
        if amount.is_integer()
        else f"{amount:,.2f}".replace(",", " ")
    )


def parse_amount(value: Any) -> float:
    raw = text(value).replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError as exc:
        raise ValueError("Введите сумму числом, например 400 или 1250.50.") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    if amount > 1_000_000:
        raise ValueError("Сумма слишком велика — проверьте ввод.")
    return round(amount, 2)


def normalize_date(value: Any) -> str:
    raw = text(value)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Дата: используйте ГГГГ-ММ-ДД, например 2026-07-01.") from exc



def normalize_period(value: Any, *, required: bool = False) -> str | None:
    """
    Accepts a normal month (2026-07) and a compound accounting period
    (2026-05_2026-06). Compound historical periods must be preserved exactly.
    """
    raw = text(value)
    if raw in {"", "-", "не указан"}:
        if required:
            raise ValueError(
                "Укажите период: ГГГГ-ММ или ГГГГ-ММ_ГГГГ-ММ."
            )
        return None

    raw = raw.replace("–", "_").replace("—", "_").replace("/", "_")
    raw = re.sub(r"\s+", "", raw)

    single = re.fullmatch(r"(\d{4})[.-](\d{1,2})", raw)
    if single:
        year, month = int(single.group(1)), int(single.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"

    period_range = re.fullmatch(
        r"(\d{4})[.-](\d{1,2})_(\d{4})[.-](\d{1,2})",
        raw,
    )
    if period_range:
        y1, m1, y2, m2 = map(int, period_range.groups())
        if 1 <= m1 <= 12 and 1 <= m2 <= 12:
            return f"{y1:04d}-{m1:02d}_{y2:04d}-{m2:02d}"

    raise ValueError(
        "Период: ГГГГ-ММ или ГГГГ-ММ_ГГГГ-ММ, "
        "например 2026-05_2026-06."
    )

def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    return v1.table_exists(cur, name)


def table_columns(cur: sqlite3.Cursor, name: str) -> set[str]:
    return v1.table_columns(cur, name)


def insert_dynamic(cur: sqlite3.Cursor, table: str, values: dict[str, Any]) -> int:
    return v1.insert_dynamic(cur, table, values)


def update_dynamic(
    cur: sqlite3.Cursor,
    table: str,
    row_id: int,
    values: dict[str, Any],
) -> bool:
    return v1.update_dynamic(cur, table, row_id, values)


def schema_ready() -> tuple[bool, str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        required = {
            "cashier_receipts",
            "cashboxes",
            "cashbox_operations",
            "payments",
            "charges",
            "payment_allocations",
            "service_catalog",
            "payment_notices",
            "bank_transactions",
            "cashier_batches",
            "cashier_batch_items",
            "cashier_reconciliation_cases",
        }
        missing = [name for name in sorted(required) if not table_exists(cur, name)]
        if missing:
            return False, "Не завершена миграция v2. Отсутствуют: " + ", ".join(missing)
        return True, ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Units and service catalog
# ---------------------------------------------------------------------------

def apartment_select_fields(cur: sqlite3.Cursor) -> str:
    cols = table_columns(cur, "apartments")
    def field(name: str) -> str:
        return name if name in cols else f"NULL AS {name}"
    return ", ".join([
        "id",
        field("apartment_number"),
        field("unit_code"),
        field("unit_type"),
        field("entrance_number"),
        field("entrance"),
        field("display_name"),
    ])


def get_unit_by_id(cur: sqlite3.Cursor, unit_id: int) -> dict | None:
    cur.execute(
        f"SELECT {apartment_select_fields(cur)} FROM apartments WHERE id = ?",
        (int(unit_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def resolve_physical_unit(unit_ref: str) -> tuple[str, list[dict], str]:
    conn = get_conn()
    try:
        resolution = resolve_unit_ref(conn, unit_ref)
        if resolution.kind == "UNIT":
            unit = get_unit_by_id(conn.cursor(), resolution.members[0].apartment_id)
            return "UNIT", [unit] if unit else [], resolution.message or ""
        if resolution.kind == "GROUP":
            units = [
                get_unit_by_id(conn.cursor(), member.apartment_id)
                for member in resolution.members
            ]
            return "GROUP", [unit for unit in units if unit], (
                "Составной номер: выберите физическую квартиру."
            )
        return resolution.kind, [], resolution.message or "Квартира не найдена."
    finally:
        conn.close()


def service_options(period_code: str | None = None) -> list[dict]:
    """
    Сначала варианты из service_items для периода, затем общие service_catalog.
    Оператор выбирает услугу кнопкой, вручную service code не вводит.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        result: list[dict] = []
        seen: set[tuple[str, str | None]] = set()

        if table_exists(cur, "service_items"):
            cols = table_columns(cur, "service_items")
            where = ["COALESCE(is_active, 1) = 1"]
            params: list[Any] = []
            if "status" in cols:
                where.append("COALESCE(status, 'active') = 'active'")
            if period_code and "period_code" in cols:
                where.append("(period_code = ? OR period_code IS NULL OR period_code = '')")
                params.append(period_code)
            cur.execute(
                f"""
                SELECT
                    service_code,
                    service_item_code,
                    service_item_name,
                    service_type,
                    amount_default
                FROM service_items
                WHERE {' AND '.join(where)}
                ORDER BY service_code, sequence_no, id
                """,
                tuple(params),
            )
            for row in cur.fetchall():
                item = dict(row)
                key = (text(item.get("service_code")), text(item.get("service_item_code")) or None)
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                result.append({
                    "service_code": key[0],
                    "service_item_code": key[1],
                    "service_name": text(item.get("service_item_name")) or key[0],
                    "service_type": text(item.get("service_type")) or "GENERAL",
                    "amount_default": item.get("amount_default"),
                })

        cols = table_columns(cur, "service_catalog")
        if cols:
            cash_filter = (
                "AND COALESCE(is_cash_collectable, 1) = 1"
                if "is_cash_collectable" in cols else ""
            )
            cur.execute(
                f"""
                SELECT service_code, service_name, service_type
                FROM service_catalog
                WHERE COALESCE(is_active, 1) = 1
                {cash_filter}
                ORDER BY service_group, service_name, service_code
                """
            )
            for row in cur.fetchall():
                item = dict(row)
                key = (text(item.get("service_code")), None)
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                result.append({
                    "service_code": key[0],
                    "service_item_code": None,
                    "service_name": text(item.get("service_name")) or key[0],
                    "service_type": text(item.get("service_type")) or "GENERAL",
                    "amount_default": None,
                })
        return result
    finally:
        conn.close()


def service_label(choice: dict | None) -> str:
    if not choice:
        return "Не выбрана"
    name = text(choice.get("service_name")) or text(choice.get("service_code"))
    return name


# ---------------------------------------------------------------------------
# Charges and default values
# ---------------------------------------------------------------------------

def allocation_amount_column(cur: sqlite3.Cursor) -> str | None:
    cols = table_columns(cur, "payment_allocations")
    if "amount" in cols:
        return "amount"
    if "allocated_amount" in cols:
        return "allocated_amount"
    return None




def open_charges(
    *,
    apartment_id: int | None = None,
    apartment_number: str | None = None,
    entrance_number: str | None = None,
    period_code: str | None = None,
    service_code: str | None = None,
    service_item_code: str | None = None,
) -> list[dict]:
    """
    Return open charges using the schema actually present in the database.

    Older OSBB billing tables identify a charge only by apartment_number;
    newer ones may also have apartment_id. This function supports both.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "charges") or not table_exists(cur, "payment_allocations"):
            return []

        ccols = table_columns(cur, "charges")
        acols = table_columns(cur, "apartments")
        allocation_col = allocation_amount_column(cur)
        if not allocation_col or "amount" not in ccols:
            return []

        has_charge_apartment_id = "apartment_id" in ccols
        has_charge_apartment_number = "apartment_number" in ccols
        has_apartment_number = "apartment_number" in acols

        join_sql = ""
        joined_apartments = False
        if has_charge_apartment_id:
            if entrance_number:
                join_sql = "LEFT JOIN apartments a ON a.id = c.apartment_id"
                joined_apartments = True
        elif has_charge_apartment_number and has_apartment_number:
            join_sql = (
                "LEFT JOIN apartments a "
                "ON CAST(a.apartment_number AS TEXT) = "
                "CAST(c.apartment_number AS TEXT)"
            )
            joined_apartments = True

        filters: list[str] = []
        params: list[Any] = []
        unit_filter_added = False

        if apartment_id is not None:
            if has_charge_apartment_id:
                filters.append("c.apartment_id = ?")
                params.append(int(apartment_id))
                unit_filter_added = True
            elif joined_apartments:
                filters.append("a.id = ?")
                params.append(int(apartment_id))
                unit_filter_added = True

        if not unit_filter_added and apartment_number:
            if has_charge_apartment_number:
                filters.append("CAST(c.apartment_number AS TEXT) = ?")
                params.append(text(apartment_number))
                unit_filter_added = True
            elif joined_apartments and has_apartment_number:
                filters.append("CAST(a.apartment_number AS TEXT) = ?")
                params.append(text(apartment_number))
                unit_filter_added = True

        if entrance_number:
            entrance_col = (
                "entrance_number" if "entrance_number" in acols
                else "entrance" if "entrance" in acols
                else None
            )
            if not joined_apartments or not entrance_col:
                return []
            filters.append(f"CAST(a.{entrance_col} AS TEXT) = ?")
            params.append(text(entrance_number))
            unit_filter_added = True

        if not unit_filter_added:
            return []

        if period_code and "period_code" in ccols:
            filters.append("CAST(c.period_code AS TEXT) = ?")
            params.append(text(period_code))
        if service_code and "service_code" in ccols:
            filters.append("c.service_code = ?")
            params.append(service_code)
        if service_item_code and "service_item_code" in ccols:
            filters.append("c.service_item_code = ?")
            params.append(service_item_code)

        status_sql = (
            "AND COALESCE(c.charge_status, '') <> 'cancelled'"
            if "charge_status" in ccols
            else (
                "AND COALESCE(c.status, '') <> 'cancelled'"
                if "status" in ccols else ""
            )
        )

        apartment_id_expr = (
            "c.apartment_id"
            if has_charge_apartment_id
            else "a.id"
            if joined_apartments
            else "NULL"
        )
        apartment_number_expr = (
            "COALESCE(a.apartment_number, c.apartment_number)"
            if joined_apartments and has_charge_apartment_number
            else "c.apartment_number"
            if has_charge_apartment_number
            else "a.apartment_number"
            if joined_apartments and has_apartment_number
            else "NULL"
        )
        period_expr = "c.period_code" if "period_code" in ccols else "NULL"
        service_expr = "c.service_code" if "service_code" in ccols else "NULL"
        item_expr = "c.service_item_code" if "service_item_code" in ccols else "NULL"

        cur.execute(
            f"""
            SELECT
                c.id AS charge_id,
                {apartment_id_expr} AS apartment_id,
                {apartment_number_expr} AS apartment_number,
                {period_expr} AS period_code,
                {service_expr} AS service_code,
                {item_expr} AS service_item_code,
                c.amount AS charge_amount,
                COALESCE(SUM(pa.{allocation_col}), 0) AS allocated_amount
            FROM charges c
            {join_sql}
            LEFT JOIN payment_allocations pa ON pa.charge_id = c.id
            WHERE {' AND '.join(filters)}
            {status_sql}
            GROUP BY c.id
            HAVING c.amount - COALESCE(SUM(pa.{allocation_col}), 0) > 0.00001
            ORDER BY COALESCE({apartment_number_expr}, ''), c.id
            """,
            tuple(params),
        )

        result = []
        for row in cur.fetchall():
            item = dict(row)
            item["charge_amount"] = float(item["charge_amount"] or 0)
            item["allocated_amount"] = float(item["allocated_amount"] or 0)
            item["outstanding_amount"] = round(
                max(0.0, item["charge_amount"] - item["allocated_amount"]),
                2,
            )
            result.append(item)
        return result
    finally:
        conn.close()

def suggested_charge(
    unit: dict,
    *,
    period_code: str,
    service_code: str,
    service_item_code: str | None,
) -> dict | None:
    rows = open_charges(
        apartment_id=int(unit["id"]),
        apartment_number=text(unit.get("apartment_number")),
        period_code=period_code,
        service_code=service_code,
        service_item_code=service_item_code,
    )
    return rows[0] if len(rows) == 1 else None


# ---------------------------------------------------------------------------
# Generic row operations
# ---------------------------------------------------------------------------

def receipt_number(receipt_date: str, receipt_id: int) -> str:
    return f"R2-{receipt_date.replace('-', '')}-{receipt_id:06d}"


def notice_number(declared_at: str, notice_id: int) -> str:
    return f"N-{declared_at.replace('-', '').replace(':', '').replace(' ', '')}-{notice_id:06d}"


def batch_number(receipt_date: str, batch_id: int) -> str:
    return f"B-{receipt_date.replace('-', '')}-{batch_id:05d}"


def bank_ref_exists(cur: sqlite3.Cursor, ref: str) -> bool:
    ref = text(ref)
    if not ref:
        return False
    cur.execute("SELECT 1 FROM bank_transactions WHERE transaction_ref = ?", (ref,))
    return cur.fetchone() is not None


def calc_cashbox_balance(cur: sqlite3.Cursor, cashbox_code: str) -> float:
    return v1.recalc_and_store_cashbox_balance(cur, cashbox_code)


def create_cash_receipt(
    cur: sqlite3.Cursor,
    *,
    apartment: dict,
    cashbox_code: str,
    receipt_date: str,
    period_code: str | None,
    service: dict,
    amount: float,
    source_text: str,
    operator_id: int,
    origin_kind: str = "HAND_TO_HAND",
    notice_id: int | None = None,
    batch_id: int | None = None,
    auto_allocate_charge_id: int | None = None,
    commercial_contract_id: int | None = None,
    commercial_unit_id: int | None = None,
    commercial_contract_item_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Writes receipt + payment + cashbox income.  It does not commit.

    If auto_allocate_charge_id is supplied, caller has already selected
    an exact charge in a reviewed batch; that is the only allowed automatic
    allocation path.
    """
    if cashbox_code not in CASH_CODES:
        raise ValueError("Для наличных доступны O, K1–K6 или C.")
    if not text(source_text):
        raise ValueError("Нужно указать основание: отметку, комментарий или номер листа.")

    cur.execute(
        "SELECT is_active FROM cashboxes WHERE cashbox_code = ?",
        (cashbox_code,),
    )
    box = cur.fetchone()
    if not box or not int(box["is_active"] or 0):
        raise ValueError(f"Касса {cashbox_code} недоступна.")

    apartment_id = int(apartment["id"]) if apartment and apartment.get("id") is not None else None
    apartment_number = text(apartment.get("apartment_number")) if apartment else None

    temp = "TMP-" + uuid4().hex.upper()
    receipt_id = insert_dynamic(
        cur,
        "cashier_receipts",
        {
            "receipt_number": temp,
            "receipt_kind": "CASH_RECEIVED",
            "entry_status": "CONFIRMED",
            "cashbox_code": cashbox_code,
            "receipt_date": receipt_date,
            "origin_kind": origin_kind,
            "apartment_id": apartment_id,
            "apartment_number": apartment_number,
            "vehicle_id": vehicle_id,
            "service_hint": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "period_code": period_code,
            "amount": amount,
            "currency": "UAH",
            "evidence_type": "TELEGRAM" if notice_id else "OPERATOR",
            "source_text": source_text,
            "designation_status": "EXACT",
            "allocation_status": "UNALLOCATED",
            "payment_notice_id": notice_id,
            "cashier_batch_id": batch_id,
            "operator_id": str(operator_id),
            "commercial_contract_id": commercial_contract_id,
            "commercial_unit_id": commercial_unit_id,
            "commercial_contract_item_id": commercial_contract_item_id,
            "confirmed_by": str(operator_id),
            "confirmed_at": now_db(),
            "created_at": now_db(),
            "updated_at": now_db(),
        },
    )
    number = receipt_number(receipt_date, receipt_id)
    update_dynamic(cur, "cashier_receipts", receipt_id, {"receipt_number": number})

    payment_id = insert_dynamic(
        cur,
        "payments",
        {
            "payment_date": receipt_date,
            "period_code": period_code,
            "apartment_id": apartment_id,
            "apartment_number": apartment_number,
            "vehicle_id": vehicle_id,
            "service_code": service["service_code"],
            "base_service_code": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "service_type": service.get("service_type") or "GENERAL",
            "amount": amount,
            "currency": "UAH",
            "payment_method": "cash",
            "payment_channel": "CASH",
            "source": "cashier_v2",
            "source_ref": number,
            "cashbox_code": cashbox_code,
            "cashier_receipt_id": receipt_id,
            "cashier_batch_id": batch_id,
            "payment_notice_id": notice_id,
            "cashier_entry_status": "CONFIRMED",
            "operator_id": str(operator_id),
            "commercial_contract_id": commercial_contract_id,
            "commercial_unit_id": commercial_unit_id,
            "commercial_contract_item_id": commercial_contract_item_id,
            "comment": source_text,
            "created_at": now_db(),
        },
    )

    op_id = insert_dynamic(
        cur,
        "cashbox_operations",
        {
            "operation_date": receipt_date,
            "cashbox_code": cashbox_code,
            "operation_type": "cash_receipt",
            "direction": "in",
            "amount": amount,
            "currency": "UAH",
            "period_code": period_code,
            "apartment_number": apartment_number,
            "vehicle_id": vehicle_id,
            "service_code": service["service_code"],
            "base_service_code": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "service_type": service.get("service_type") or "GENERAL",
            "payment_id": payment_id,
            "source_type": "cashier_v2",
            "source_ref": number,
            "operator_id": str(operator_id),
            "commercial_contract_id": commercial_contract_id,
            "commercial_unit_id": commercial_unit_id,
            "commercial_contract_item_id": commercial_contract_item_id,
            "actor_type": "operator",
            "cashier_receipt_id": receipt_id,
            "cashier_batch_id": batch_id,
            "payment_notice_id": notice_id,
            "comment": source_text,
            "created_at": now_db(),
        },
    )
    update_dynamic(
        cur,
        "payments",
        payment_id,
        {"cashbox_operation_id": op_id},
    )
    update_dynamic(
        cur,
        "cashier_receipts",
        receipt_id,
        {
            "payment_id": payment_id,
            "cashbox_operation_id": op_id,
        },
    )

    allocation_id = None
    if auto_allocate_charge_id is not None:
        allocation_id = insert_dynamic(
            cur,
            "payment_allocations",
            {
                "payment_id": payment_id,
                "charge_id": int(auto_allocate_charge_id),
                "amount": amount,
                "allocated_amount": amount,
                "created_at": now_db(),
            },
        )
        update_dynamic(
            cur,
            "cashier_receipts",
            receipt_id,
            {"allocation_status": "ALLOCATED"},
        )

    balance = calc_cashbox_balance(cur, cashbox_code)

    audit_log(
        conn=cur.connection,
        operator_id=str(operator_id),
        user_id=str(operator_id),
        actor_type="operator",
        action_type="cashier_v2_cash_receipt_confirmed",
        table_name="cashier_receipts",
        row_id=receipt_id,
        field_name="cashbox_code,amount,period_code,service_code",
        old_value="",
        new_value=f"{cashbox_code},{amount},{period_code or ''},{service['service_code']}",
        source_context="cashier_v2",
        comment="Наличные приняты. Авторазнесение только при подтверждённой строке пакета.",
        extra={
            "receipt_number": number,
            "payment_id": payment_id,
            "cashbox_operation_id": op_id,
            "allocation_id": allocation_id,
            "notice_id": notice_id,
            "batch_id": batch_id,
            "cashbox_balance_after": balance,
        },
        commit=False,
    )
    return {
        "receipt_id": receipt_id,
        "receipt_number": number,
        "payment_id": payment_id,
        "cashbox_operation_id": op_id,
        "allocation_id": allocation_id,
        "cashbox_balance": balance,
    }


def create_bank_payment(
    cur: sqlite3.Cursor,
    *,
    apartment: dict,
    transaction_ref: str,
    transaction_date: str,
    period_code: str | None,
    service: dict,
    amount: float,
    payer_text: str,
    operator_id: int,
    bank_name: str | None = None,
    notice_id: int | None = None,
    batch_id: int | None = None,
    auto_allocate_charge_id: int | None = None,
    commercial_contract_id: int | None = None,
    commercial_unit_id: int | None = None,
    commercial_contract_item_id: int | None = None,
) -> dict:
    ref = text(transaction_ref)
    if not ref:
        raise ValueError("Для банковской операции обязателен идентификатор из выписки.")
    if bank_ref_exists(cur, ref):
        raise ValueError("Банковская операция с таким идентификатором уже есть.")

    bank_id = insert_dynamic(
        cur,
        "bank_transactions",
        {
            "transaction_ref": ref,
            "entry_status": "CONFIRMED",
            "transaction_date": transaction_date,
            "value_date": transaction_date,
            "apartment_id": apartment_id,
            "apartment_number": apartment_number,
            "commercial_unit_id": commercial_unit_id,
            "commercial_contract_id": commercial_contract_id,
            "commercial_contract_item_id": commercial_contract_item_id,
            "period_code": period_code,
            "service_code": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "amount": amount,
            "currency": "UAH",
            "payer_text": payer_text or None,
            "bank_name": bank_name or None,
            "source_type": "manual_operator",
            "payment_notice_id": notice_id,
            "cashier_batch_id": batch_id,
            "operator_id": str(operator_id),
            "operator_note": payer_text or None,
            "created_at": now_db(),
            "updated_at": now_db(),
        },
    )
    payment_id = insert_dynamic(
        cur,
        "payments",
        {
            "payment_date": transaction_date,
            "period_code": period_code,
            "apartment_id": apartment_id,
            "apartment_number": apartment_number,
            "commercial_unit_id": commercial_unit_id,
            "commercial_contract_id": commercial_contract_id,
            "commercial_contract_item_id": commercial_contract_item_id,
            "service_code": service["service_code"],
            "base_service_code": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "service_type": service.get("service_type") or "GENERAL",
            "amount": amount,
            "currency": "UAH",
            "payment_method": "bank",
            "payment_channel": "BANK",
            "source": "bank_v2",
            "source_ref": ref,
            "cashbox_code": BANK_CODE,
            "bank_transaction_id": bank_id,
            "cashier_batch_id": batch_id,
            "payment_notice_id": notice_id,
            "cashier_entry_status": "CONFIRMED",
            "operator_id": str(operator_id),
            "comment": payer_text or f"Банк. Идентификатор {ref}",
            "created_at": now_db(),
        },
    )
    update_dynamic(cur, "bank_transactions", bank_id, {"payment_id": payment_id})

    allocation_id = None
    if auto_allocate_charge_id is not None:
        allocation_id = insert_dynamic(
            cur,
            "payment_allocations",
            {
                "payment_id": payment_id,
                "charge_id": int(auto_allocate_charge_id),
                "amount": amount,
                "allocated_amount": amount,
                "created_at": now_db(),
            },
        )

    audit_log(
        conn=cur.connection,
        operator_id=str(operator_id),
        user_id=str(operator_id),
        actor_type="operator",
        action_type="cashier_v2_bank_payment_confirmed",
        table_name="bank_transactions",
        row_id=bank_id,
        field_name="transaction_ref,amount,apartment_number",
        old_value="",
        new_value=f"{ref},{amount},{apartment.get('apartment_number')}",
        source_context="cashier_v2",
        comment="Банковская операция введена вручную по идентификатору выписки.",
        extra={
            "payment_id": payment_id,
            "notice_id": notice_id,
            "batch_id": batch_id,
            "allocation_id": allocation_id,
        },
        commit=False,
    )
    return {
        "bank_transaction_id": bank_id,
        "payment_id": payment_id,
        "allocation_id": allocation_id,
    }


# ---------------------------------------------------------------------------
# Resident notices
# ---------------------------------------------------------------------------

def create_payment_notice(
    *,
    account: dict,
    apartment: dict,
    notice_type: str,
    declared_cashbox_code: str | None,
    period_code: str | None,
    service: dict,
    amount: float,
    resident_comment: str,
) -> dict:
    if notice_type not in {"CASH_HANDOVER", "BANK_TRANSFER"}:
        raise ValueError("Недопустимый тип уведомления.")
    if notice_type == "CASH_HANDOVER" and declared_cashbox_code not in CASH_CODES:
        raise ValueError("Для наличных выберите O, K1–K6 или C.")

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Protect from repeated identical notice on the same day while status NEW.
        cur.execute(
            """
            SELECT id, notice_number
            FROM payment_notices
            WHERE resident_account_id = ?
              AND notice_type = ?
              AND COALESCE(declared_cashbox_code, '') = ?
              AND COALESCE(declared_period_code, '') = ?
              AND COALESCE(declared_service_code, '') = ?
              AND declared_amount = ?
              AND notice_status = 'NEW'
              AND substr(declared_at, 1, 10) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                int(account["id"]),
                notice_type,
                declared_cashbox_code or "",
                period_code or "",
                service["service_code"],
                amount,
                today(),
            ),
        )
        existing = cur.fetchone()
        if existing:
            return {
                "notice_id": int(existing["id"]),
                "notice_number": existing["notice_number"],
                "created": False,
            }

        temp = "TMP-" + uuid4().hex.upper()
        notice_id = insert_dynamic(
            cur,
            "payment_notices",
            {
                "notice_number": temp,
                "notice_type": notice_type,
                "notice_status": "NEW",
                "resident_account_id": int(account["id"]),
                "telegram_user_id": str(account["telegram_user_id"]),
                "apartment_id": apartment_id,
                "apartment_number": apartment_number,
                "declared_cashbox_code": declared_cashbox_code,
                "declared_period_code": period_code,
                "declared_service_code": service["service_code"],
                "declared_service_item_code": service.get("service_item_code"),
                "declared_amount": amount,
                "currency": "UAH",
                "declared_at": now_db(),
                "resident_comment": resident_comment or None,
                "evidence_text": resident_comment or None,
                "created_at": now_db(),
                "updated_at": now_db(),
            },
        )
        number = notice_number(now_db(), notice_id)
        update_dynamic(cur, "payment_notices", notice_id, {"notice_number": number})

        audit_log(
            conn=conn,
            operator_id=str(account["telegram_user_id"]),
            user_id=str(account["telegram_user_id"]),
            actor_type="resident",
            action_type="resident_payment_notice_created",
            table_name="payment_notices",
            row_id=notice_id,
            field_name="notice_type,amount,period,cashbox",
            old_value="",
            new_value=(
                f"{notice_type},{amount},{period_code or ''},"
                f"{declared_cashbox_code or BANK_CODE}"
            ),
            source_context="client_portal_v2",
            comment=(
                "Житель сообщил о передаче денег. "
                "Это уведомление не является подтверждённой оплатой."
            ),
            extra={"notice_number": number, "apartment_number": apartment.get("apartment_number")},
            commit=False,
        )
        conn.commit()
        return {"notice_id": notice_id, "notice_number": number, "created": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_resident_notices(account_id: int) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM payment_notices
            WHERE resident_account_id = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (int(account_id),),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_open_notices() -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                n.*,
                a.telegram_username,
                a.telegram_first_name,
                a.telegram_last_name
            FROM payment_notices n
            LEFT JOIN resident_accounts a ON a.id = n.resident_account_id
            WHERE n.notice_status = 'NEW'
            ORDER BY n.declared_at, n.id
            LIMIT 50
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_notice(notice_id: int, *, conn: sqlite3.Connection | None = None) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM payment_notices WHERE id = ?", (int(notice_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def confirm_cash_notice(notice_id: int, *, operator_id: int, operator_note: str) -> dict:
    conn = get_conn()
    try:
        cur = conn.cursor()
        notice = get_notice(notice_id, conn=conn)
        if not notice or notice["notice_status"] != "NEW":
            raise ValueError("Уведомление не найдено или уже обработано.")
        if notice["notice_type"] != "CASH_HANDOVER":
            raise ValueError("Это не уведомление о наличных.")

        apartment = get_unit_by_id(cur, int(notice["apartment_id"]))
        if not apartment:
            raise ValueError("Квартира уведомления не найдена.")

        service = {
            "service_code": text(notice["declared_service_code"]) or "UNSPECIFIED",
            "service_item_code": notice.get("declared_service_item_code"),
            "service_type": "GENERAL",
        }
        result = create_cash_receipt(
            cur,
            apartment=apartment,
            cashbox_code=text(notice["declared_cashbox_code"]) or "O",
            receipt_date=today(),
            period_code=notice.get("declared_period_code"),
            service=service,
            amount=float(notice["declared_amount"]),
            source_text=(
                f"Подтверждение уведомления {notice['notice_number']}. "
                f"{operator_note or notice.get('resident_comment') or ''}"
            ).strip(),
            operator_id=operator_id,
            origin_kind="RESIDENT_NOTICE",
            notice_id=int(notice_id),
        )
        update_dynamic(
            cur,
            "payment_notices",
            int(notice_id),
            {
                "notice_status": "CONFIRMED",
                "matched_cashier_receipt_id": result["receipt_id"],
                "matched_payment_id": result["payment_id"],
                "operator_id": str(operator_id),
                "operator_note": operator_note or None,
                "reviewed_at": now_db(),
                "confirmed_at": now_db(),
                "updated_at": now_db(),
            },
        )
        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="resident_cash_notice_confirmed",
            table_name="payment_notices",
            row_id=notice_id,
            field_name="notice_status,matched_cashier_receipt_id",
            old_value="NEW",
            new_value=f"CONFIRMED,{result['receipt_id']}",
            source_context="cashier_v2",
            comment="Оператор подтвердил фактическое получение наличных по уведомлению жителя.",
            extra=result,
            commit=False,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_notice(notice_id: int, *, operator_id: int, reason: str) -> None:
    reason = text(reason)
    if not reason:
        raise ValueError("Укажите причину отклонения.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        notice = get_notice(notice_id, conn=conn)
        if not notice or notice["notice_status"] != "NEW":
            raise ValueError("Уведомление не найдено или уже обработано.")
        update_dynamic(
            cur,
            "payment_notices",
            notice_id,
            {
                "notice_status": "REJECTED",
                "operator_id": str(operator_id),
                "operator_note": reason,
                "reviewed_at": now_db(),
                "rejected_at": now_db(),
                "updated_at": now_db(),
            },
        )
        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="resident_payment_notice_rejected",
            table_name="payment_notices",
            row_id=notice_id,
            field_name="notice_status",
            old_value="NEW",
            new_value="REJECTED",
            source_context="cashier_v2",
            comment=reason,
            commit=False,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mass batch and reconciliation
# ---------------------------------------------------------------------------

def create_cash_batch(
    *,
    entrance_number: str,
    period_code: str,
    service: dict,
    cashbox_code: str,
    receipt_date: str,
    charges: list[dict],
    exclusions: set[str],
    amount_overrides: dict[str, float],
    operator_id: int,
    source_text: str,
) -> dict:
    if cashbox_code not in CASH_CODES:
        raise ValueError("Массовый наличный приём доступен для O, K1–K6 или C.")
    if not charges:
        raise ValueError("Нет открытых начислений для выбранного набора.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        temp = "TMP-" + uuid4().hex.upper()
        batch_id = insert_dynamic(
            cur,
            "cashier_batches",
            {
                "batch_number": temp,
                "batch_kind": "CASH_ENTRANCE",
                "entry_status": "DRAFT",
                "entrance_number": entrance_number,
                "cashbox_code": cashbox_code,
                "period_code": period_code,
                "service_code": service["service_code"],
                "service_item_code": service.get("service_item_code"),
                "receipt_date": receipt_date,
                "operator_id": str(operator_id),
                "source_text": source_text,
                "created_at": now_db(),
                "updated_at": now_db(),
            },
        )
        number = batch_number(receipt_date, batch_id)
        update_dynamic(cur, "cashier_batches", batch_id, {"batch_number": number})

        included = 0
        excluded = 0
        total = 0.0
        item_results = []

        for charge in charges:
            apartment_no = text(charge.get("apartment_number"))
            if apartment_no in exclusions:
                insert_dynamic(
                    cur,
                    "cashier_batch_items",
                    {
                        "batch_id": batch_id,
                        "apartment_id": charge.get("apartment_id"),
                        "apartment_number": apartment_no,
                        "charge_id": charge["charge_id"],
                        "amount_expected": charge["outstanding_amount"],
                        "amount_received": 0,
                        "cashbox_code": cashbox_code,
                        "period_code": period_code,
                        "service_code": service["service_code"],
                        "service_item_code": service.get("service_item_code"),
                        "item_status": "EXCLUDED",
                        "exception_note": "Исключено оператором из массовой пачки.",
                        "created_at": now_db(),
                        "updated_at": now_db(),
                    },
                )
                excluded += 1
                continue

            amount = float(amount_overrides.get(apartment_no, charge["outstanding_amount"]))
            if amount <= 0 or amount > float(charge["outstanding_amount"]) + 0.00001:
                raise ValueError(
                    f"кв. {apartment_no}: сумма должна быть от 0.01 до "
                    f"{money(charge['outstanding_amount'])} грн."
                )

            apartment = get_unit_by_id(cur, int(charge["apartment_id"]))
            if not apartment:
                raise ValueError(f"Квартира {apartment_no} не найдена.")

            result = create_cash_receipt(
                cur,
                apartment=apartment,
                cashbox_code=cashbox_code,
                receipt_date=receipt_date,
                period_code=period_code,
                service=service,
                amount=amount,
                source_text=f"Массовая пачка {number}. {source_text}",
                operator_id=operator_id,
                origin_kind="MASS_BATCH",
                batch_id=batch_id,
                auto_allocate_charge_id=int(charge["charge_id"]),
            )
            insert_dynamic(
                cur,
                "cashier_batch_items",
                {
                    "batch_id": batch_id,
                    "apartment_id": int(charge["apartment_id"]),
                    "apartment_number": apartment_no,
                    "charge_id": charge["charge_id"],
                    "receipt_id": result["receipt_id"],
                    "payment_id": result["payment_id"],
                    "amount_expected": charge["outstanding_amount"],
                    "amount_received": amount,
                    "cashbox_code": cashbox_code,
                    "period_code": period_code,
                    "service_code": service["service_code"],
                    "service_item_code": service.get("service_item_code"),
                    "item_status": "CONFIRMED",
                    "created_at": now_db(),
                    "updated_at": now_db(),
                },
            )
            included += 1
            total += amount
            item_results.append(result)

        update_dynamic(
            cur,
            "cashier_batches",
            batch_id,
            {
                "entry_status": "CONFIRMED",
                "included_count": included,
                "excluded_count": excluded,
                "total_amount": total,
                "confirmed_at": now_db(),
                "updated_at": now_db(),
            },
        )
        balance = calc_cashbox_balance(cur, cashbox_code)

        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="cashier_v2_mass_batch_confirmed",
            table_name="cashier_batches",
            row_id=batch_id,
            field_name="entrance,period,cashbox,service,included,total",
            old_value="DRAFT",
            new_value=(
                f"{entrance_number},{period_code},{cashbox_code},"
                f"{service['service_code']},{included},{total}"
            ),
            source_context="cashier_v2",
            comment="Оператор подтвердил массовую пачку. Каждая строка имеет отдельный receipt/payment.",
            extra={
                "batch_number": number,
                "excluded": sorted(exclusions),
                "amount_overrides": amount_overrides,
                "cashbox_balance_after": balance,
            },
            commit=False,
        )
        conn.commit()
        return {
            "batch_id": batch_id,
            "batch_number": number,
            "included": included,
            "excluded": excluded,
            "total": total,
            "cashbox_balance": balance,
            "items": item_results,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def historical_imports(limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cols = table_columns(cur, "payments")
        receipt_condition = (
            "(cashier_receipt_id IS NULL OR cashier_receipt_id = '')"
            if "cashier_receipt_id" in cols else "1=1"
        )
        bank_condition = (
            "AND (bank_transaction_id IS NULL OR bank_transaction_id = '')"
            if "bank_transaction_id" in cols else ""
        )
        source_expr = "source" if "source" in cols else "NULL"
        ref_expr = "source_ref" if "source_ref" in cols else "NULL"
        period_expr = "period_code" if "period_code" in cols else "NULL"
        apt_expr = "apartment_number" if "apartment_number" in cols else "NULL"
        date_expr = "payment_date" if "payment_date" in cols else "NULL"
        cur.execute(
            f"""
            SELECT
                id AS payment_id,
                {date_expr} AS payment_date,
                {apt_expr} AS apartment_number,
                {period_expr} AS period_code,
                amount,
                {source_expr} AS source,
                {ref_expr} AS source_ref
            FROM payments
            WHERE {receipt_condition}
            {bank_condition}
              AND (
                    LOWER(COALESCE({source_expr}, '')) LIKE '%ohorona%'
                 OR LOWER(COALESCE({source_expr}, '')) LIKE '%import%'
                 OR LOWER(COALESCE({ref_expr}, '')) LIKE '%охорона%'
              )
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def reconciliation_summary() -> dict[str, Any]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        result = {
            "resident_notices": 0,
            "paper_notes": 0,
            "unallocated_cash": 0,
            "historical_imports": 0,
            "open_cases": 0,
        }
        cur.execute("SELECT COUNT(*) FROM payment_notices WHERE notice_status = 'NEW'")
        result["resident_notices"] = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM cashier_receipts WHERE receipt_kind = 'PAPER_NOTE' AND entry_status <> 'VOID'"
        )
        result["paper_notes"] = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT COUNT(*) FROM cashier_receipts
            WHERE receipt_kind = 'CASH_RECEIVED'
              AND entry_status = 'CONFIRMED'
              AND COALESCE(allocation_status, 'UNALLOCATED') IN ('UNALLOCATED', 'PARTIAL')
            """
        )
        result["unallocated_cash"] = int(cur.fetchone()[0] or 0)
        result["historical_imports"] = len(historical_imports(1000))
        cur.execute(
            "SELECT COUNT(*) FROM cashier_reconciliation_cases WHERE case_status = 'OPEN'"
        )
        result["open_cases"] = int(cur.fetchone()[0] or 0)
        return result
    finally:
        conn.close()


def create_paper_note(
    cur: sqlite3.Cursor,
    *,
    apartment: dict,
    period_code: str | None,
    service: dict,
    amount: float,
    source_text: str,
    operator_id: int,
    origin_kind: str = "CONCIERGE_PAPER",
) -> dict:
    if not text(source_text):
        raise ValueError("Для бумажки нужно записать её текст или номер.")
    receipt_id = insert_dynamic(
        cur,
        "cashier_receipts",
        {
            "receipt_number": "TMP-" + uuid4().hex.upper(),
            "receipt_kind": "PAPER_NOTE",
            "entry_status": "PAPER_NOTE",
            "receipt_date": today(),
            "origin_kind": origin_kind,
            "apartment_id": apartment_id,
            "apartment_number": apartment_number,
            "service_hint": service["service_code"],
            "service_item_code": service.get("service_item_code"),
            "period_code": period_code,
            "amount": amount,
            "currency": "UAH",
            "evidence_type": "PAPER",
            "source_text": source_text,
            "designation_status": "EXACT",
            "allocation_status": "NOT_APPLICABLE",
            "operator_id": str(operator_id),
            "confirmed_by": str(operator_id),
            "confirmed_at": now_db(),
            "created_at": now_db(),
            "updated_at": now_db(),
        },
    )
    number = receipt_number(today(), receipt_id)
    update_dynamic(cur, "cashier_receipts", receipt_id, {"receipt_number": number})
    audit_log(
        conn=cur.connection,
        operator_id=str(operator_id),
        user_id=str(operator_id),
        actor_type="operator",
        action_type="cashier_v2_paper_note_created",
        table_name="cashier_receipts",
        row_id=receipt_id,
        field_name="receipt_kind,amount,apartment_number",
        old_value="",
        new_value=f"PAPER_NOTE,{amount},{apartment.get('apartment_number')}",
        source_context="cashier_v2",
        comment="Бумажка сохранена без создания оплаты и движения денег.",
        extra={"receipt_number": number},
        commit=False,
    )
    return {"receipt_id": receipt_id, "receipt_number": number}


def confirm_bank_notice(
    notice_id: int,
    *,
    operator_id: int,
    transaction_ref: str,
    transaction_date: str,
    operator_note: str,
) -> dict:
    conn = get_conn()
    try:
        cur = conn.cursor()
        notice = get_notice(notice_id, conn=conn)
        if not notice or notice["notice_status"] != "NEW":
            raise ValueError("Уведомление не найдено или уже обработано.")
        if notice["notice_type"] != "BANK_TRANSFER":
            raise ValueError("Это не банковское уведомление.")
        apartment = get_unit_by_id(cur, int(notice["apartment_id"]))
        if not apartment:
            raise ValueError("Квартира уведомления не найдена.")
        service = {
            "service_code": text(notice["declared_service_code"]) or "UNSPECIFIED",
            "service_item_code": notice.get("declared_service_item_code"),
            "service_type": "GENERAL",
        }
        result = create_bank_payment(
            cur,
            apartment=apartment,
            transaction_ref=transaction_ref,
            transaction_date=transaction_date,
            period_code=notice.get("declared_period_code"),
            service=service,
            amount=float(notice["declared_amount"]),
            payer_text=operator_note or notice.get("resident_comment") or "",
            operator_id=operator_id,
            notice_id=notice_id,
        )
        update_dynamic(
            cur,
            "payment_notices",
            notice_id,
            {
                "notice_status": "CONFIRMED",
                "matched_bank_transaction_id": result["bank_transaction_id"],
                "matched_payment_id": result["payment_id"],
                "operator_id": str(operator_id),
                "operator_note": operator_note or None,
                "reviewed_at": now_db(),
                "confirmed_at": now_db(),
                "updated_at": now_db(),
            },
        )
        audit_log(
            conn=conn,
            operator_id=str(operator_id),
            user_id=str(operator_id),
            actor_type="operator",
            action_type="resident_bank_notice_confirmed",
            table_name="payment_notices",
            row_id=notice_id,
            field_name="notice_status,matched_bank_transaction_id",
            old_value="NEW",
            new_value=f"CONFIRMED,{result['bank_transaction_id']}",
            source_context="cashier_v2",
            comment="Оператор подтвердил банковскую оплату по уведомлению жителя.",
            extra=result,
            commit=False,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
