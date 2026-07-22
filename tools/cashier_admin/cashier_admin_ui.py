#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = PROJECT_ROOT / "Data" / "backups" / "db"

BTN_BILLING_SUBJECTS = "🔎 Субъекты расчётов"
BTN_RESIDENT_SEARCH = "🏠 Жильцы / 🚗 Авто"
BTN_COMMERCIAL_SEARCH = "🏢 Коммерческие фирмы"
BTN_PAYMENT_ADMIN = "🧾 Админ платежей"
BTN_LAST_PAYMENTS = "📜 Последние платежи"
BTN_FIND_PAYMENT = "🔎 Найти платёж"
BTN_DELETE_SELECTED = "🩺 Удалить со шлейфом"
BTN_DELETE_BATCH = "🩺 Удалить выбранные"
BTN_OPEN_SELECTED = "👁 Открыть выбранный"
BTN_REASON_OPERATOR = "Ошибка оператора"
BTN_REASON_TECH = "Технический сбой"
BTN_REASON_SOFTWARE = "Программная недоработка"
BTN_REASON_DUPLICATE = "Дублирование операции"
BTN_REASON_TEST = "Ошибочная тестовая запись"
BTN_REASON_OTHER = "Другое..."
BTN_CONFIRM_DELETE = "✅ Выполнить удаление"
BTN_CANCEL = "❌ Отмена"
BTN_BACK = "⬅️ Назад"
BTN_MAIN = "🏠 Главное меню"

REASONS = {
    BTN_REASON_OPERATOR: "OPERATOR_ERROR",
    BTN_REASON_TECH: "TECHNICAL_FAILURE",
    BTN_REASON_SOFTWARE: "SOFTWARE_DEFECT",
    BTN_REASON_DUPLICATE: "DUPLICATE_OPERATION",
    BTN_REASON_TEST: "TEST_RECORD",
}


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DEFAULT_DB))
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def subject_menu() -> ReplyKeyboardMarkup:
    return kb([[BTN_RESIDENT_SEARCH], [BTN_COMMERCIAL_SEARCH], [BTN_BACK, BTN_MAIN]])


def payment_admin_menu() -> ReplyKeyboardMarkup:
    return kb([[BTN_LAST_PAYMENTS], [BTN_FIND_PAYMENT], [BTN_BACK, BTN_MAIN]])


def reason_menu() -> ReplyKeyboardMarkup:
    return kb([
        [BTN_REASON_OPERATOR],
        [BTN_REASON_TECH],
        [BTN_REASON_SOFTWARE],
        [BTN_REASON_DUPLICATE],
        [BTN_REASON_TEST],
        [BTN_REASON_OTHER],
        [BTN_CANCEL],
    ])


def payment_list_kb(rows: list[sqlite3.Row], selected_ids: set[int] | None = None) -> ReplyKeyboardMarkup:
    selected_ids = selected_ids or set()
    buttons = []
    for row in rows[:12]:
        pid = int(row["id"])
        mark = "☑" if pid in selected_ids else "☐"
        label = (
            f"{mark} #{pid} · {row['apartment_number'] or '—'} · "
            f"{float(row['amount'] or 0):.2f} грн"
        )
        buttons.append([label])
    if selected_ids:
        if len(selected_ids) == 1:
            buttons.append([BTN_OPEN_SELECTED])
        buttons.append([BTN_DELETE_BATCH])
    buttons.append([BTN_BACK, BTN_MAIN])
    return kb(buttons)


def commercial_list_kb(rows: list[dict[str, Any]]) -> ReplyKeyboardMarkup:
    buttons = []
    for i, row in enumerate(rows[:12], 1):
        name = row.get("counterparty_name") or "Без названия"
        unit = row.get("apartment_number") or "—"
        buttons.append([f"{i}. 🏢 {name} · пом. {unit}"])
    buttons.append([BTN_BACK, BTN_MAIN])
    return kb(buttons)


def commercial_search(query: str) -> list[dict[str, Any]]:
    con = connect()
    try:
        if not table_exists(con, "commercial_contracts"):
            return []
        q = query.strip()
        like = f"%{q}%"
        rows = con.execute(
            """
            SELECT c.*, a.apartment_number,
                   COALESCE(SUM(CASE WHEN i.is_active=1 THEN
                       CASE WHEN i.calculation_mode='FIXED_MONTHLY' THEN COALESCE(i.fixed_amount,0)
                            ELSE COALESCE(i.rate_amount,0) * COALESCE(i.quantity_default,1) END
                   ELSE 0 END),0) AS expected_amount,
                   GROUP_CONCAT(CASE WHEN i.is_active=1 THEN i.item_name END, '; ') AS item_names
            FROM commercial_contracts c
            JOIN apartments a ON a.id=c.unit_id
            LEFT JOIN commercial_contract_items i ON i.contract_id=c.id
            WHERE COALESCE(c.counterparty_name,'') LIKE ?
               OR COALESCE(c.contract_number,'') LIKE ?
               OR COALESCE(c.internal_note,'') LIKE ?
               OR CAST(a.apartment_number AS TEXT)=?
            GROUP BY c.id
            ORDER BY c.counterparty_name, a.apartment_number
            LIMIT 20
            """,
            (like, like, like, q),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def commercial_card(row: dict[str, Any]) -> str:
    valid = f"{row.get('valid_from') or '—'} — {row.get('valid_to') or 'без срока'}"
    return (
        f"🏢 {row.get('counterparty_name') or 'Без названия'}\n\n"
        f"Помещение: {row.get('apartment_number') or '—'}\n"
        f"Договор: {row.get('contract_number') or '—'}\n"
        f"Статус: {row.get('status') or '—'}\n"
        f"Срок: {valid}\n"
        f"Платёж до: {row.get('payment_due_day') or '—'} числа\n\n"
        f"Позиции договора:\n{row.get('item_names') or '—'}\n\n"
        f"Ожидаемая сумма: {float(row.get('expected_amount') or 0):.2f} грн\n"
        f"Примечание: {row.get('internal_note') or '—'}"
    )


def recent_payments(limit: int = 12) -> list[sqlite3.Row]:
    con = connect()
    try:
        return con.execute(
            """
            SELECT p.*, r.receipt_number
            FROM payments p
            LEFT JOIN cashier_receipts r ON r.id=p.cashier_receipt_id
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        con.close()



def search_payments_human(query: str, limit: int = 30) -> list[sqlite3.Row]:
    q = str(query or '').strip()
    if not q:
        return []
    con = connect()
    try:
        pcols = columns(con, 'payments')
        clauses = []
        params: list[Any] = []

        def add(expr: str, value: Any) -> None:
            clauses.append(expr); params.append(value)

        like = f"%{q}%"
        for col in ['apartment_number','service_item_code','base_service_code','comment','created_by','operator_id','period_code']:
            if col in pcols:
                add(f"CAST(p.{col} AS TEXT) LIKE ?", like)
        if 'amount' in pcols:
            try:
                amount = float(q.replace(',', '.'))
                add('ABS(COALESCE(p.amount,0)-?) < 0.001', amount)
            except Exception:
                pass
        # Date fragments: 10.07.2026, 2026-07-10, or 10.07
        date_candidates = [q]
        try:
            dt = datetime.strptime(q, '%d.%m.%Y'); date_candidates.append(dt.strftime('%Y-%m-%d'))
        except Exception:
            pass
        if 'created_at' in pcols:
            for d in date_candidates:
                add('CAST(p.created_at AS TEXT) LIKE ?', f"%{d}%")
        if 'payment_date' in pcols:
            for d in date_candidates:
                add('CAST(p.payment_date AS TEXT) LIKE ?', f"%{d}%")

        extra_joins = []
        if table_exists(con, 'cashier_receipts'):
            extra_joins.append('LEFT JOIN cashier_receipts r ON r.id=p.cashier_receipt_id')
            if 'receipt_number' in columns(con, 'cashier_receipts'):
                add('COALESCE(r.receipt_number,\'\') LIKE ?', like)
        if table_exists(con, 'vehicles') and table_exists(con, 'apartments'):
            extra_joins.append('LEFT JOIN apartments a ON CAST(a.apartment_number AS TEXT)=CAST(p.apartment_number AS TEXT)')
            extra_joins.append('LEFT JOIN vehicles v ON v.apartment_id=a.id')
            vcols = columns(con, 'vehicles')
            plate_expr = []
            for c in ['license_plate','license_plate_normalized']:
                if c in vcols:
                    plate_expr.append(f"UPPER(REPLACE(REPLACE(COALESCE(v.{c},''),' ',''),'-','')) LIKE ?")
                    params.append('%' + ''.join(ch for ch in q.upper() if ch.isalnum()) + '%')
            if plate_expr:
                clauses.append('(' + ' OR '.join(plate_expr) + ')')
        if table_exists(con, 'resident_accounts'):
            racols = columns(con, 'resident_accounts')
            if 'apartment_number' in racols:
                extra_joins.append('LEFT JOIN resident_accounts ra ON CAST(ra.apartment_number AS TEXT)=CAST(p.apartment_number AS TEXT)')
                name_cols = [c for c in ['full_name','first_name','last_name','username'] if c in racols]
                for c in name_cols:
                    add(f"COALESCE(ra.{c},'') LIKE ?", like)

        if not clauses:
            return []
        sql = f"""
            SELECT DISTINCT p.*, r.receipt_number
            FROM payments p
            {' '.join(extra_joins)}
            WHERE {' OR '.join(clauses)}
            ORDER BY p.id DESC
            LIMIT ?
        """
        params.append(int(limit))
        return con.execute(sql, params).fetchall()
    finally:
        con.close()

def get_payment(payment_id: int) -> sqlite3.Row | None:
    con = connect()
    try:
        return con.execute(
            """
            SELECT p.*, r.receipt_number, r.id AS receipt_id,
                   r.entry_status AS receipt_status
            FROM payments p
            LEFT JOIN cashier_receipts r ON r.id=p.cashier_receipt_id
            WHERE p.id=?
            """,
            (payment_id,),
        ).fetchone()
    finally:
        con.close()


def payment_card(row: sqlite3.Row) -> str:
    return (
        f"💰 Платёж #{row['id']}\n\n"
        f"Чек: {row['receipt_number'] if row['receipt_number'] else '—'}\n"
        f"Квартира/помещение: {row['apartment_number'] or '—'}\n"
        f"Период: {row['period_code'] or '—'}\n"
        f"Сумма: {float(row['amount'] or 0):.2f} {row['currency'] or 'UAH'}\n"
        f"Услуга: {row['service_item_code'] or row['base_service_code'] or '—'}\n"
        f"Касса: {row['cashbox_code'] or '—'}\n"
        f"Оператор: {row['operator_id'] or row['created_by'] or '—'}\n"
        f"Статус: {row['cashier_entry_status'] or '—'}\n"
        f"Дата: {row['created_at'] or row['payment_date'] or '—'}\n"
        f"Комментарий: {row['comment'] or '—'}"
    )


def _add_exact_reference(
    con: sqlite3.Connection,
    plan: dict[str, list[int]],
    table: str,
    table_col: str,
    id_col: str,
    refs: list[tuple[str, int]],
) -> None:
    if not refs or not table_exists(con, table):
        return
    cols = columns(con, table)
    if table_col not in cols or id_col not in cols:
        return
    clauses = []
    params: list[Any] = []
    for ref_table, ref_id in refs:
        clauses.append(f"({table_col}=? AND CAST({id_col} AS TEXT)=?)")
        params.extend([ref_table, str(ref_id)])
    rows = con.execute(
        f"SELECT id FROM {table} WHERE " + " OR ".join(clauses),
        params,
    ).fetchall()
    if rows:
        plan.setdefault(table, [])
        plan[table].extend(int(r[0]) for r in rows)
        plan[table] = sorted(set(plan[table]))


def collect_chain(payment_id: int) -> dict[str, list[int]]:
    con = connect()
    try:
        p = con.execute(
            """
            SELECT p.*, r.id AS receipt_id,
                   COALESCE(p.cashbox_operation_id, r.cashbox_operation_id) AS linked_cashbox_operation_id
            FROM payments p
            LEFT JOIN cashier_receipts r ON r.id=p.cashier_receipt_id
            WHERE p.id=?
            """,
            (payment_id,),
        ).fetchone()
        if not p:
            return {}

        receipt_ids = [int(p["receipt_id"])] if p["receipt_id"] is not None else []
        operation_ids = (
            [int(p["linked_cashbox_operation_id"])]
            if p["linked_cashbox_operation_id"] is not None else []
        )
        notice_ids = [int(p["payment_notice_id"])] if p["payment_notice_id"] is not None else []

        plan: dict[str, list[int]] = {"payments": [payment_id]}
        if receipt_ids:
            plan["cashier_receipts"] = receipt_ids
        if operation_ids:
            plan["cashbox_operations"] = operation_ids
        if notice_ids and table_exists(con, "payment_notices"):
            plan["payment_notices"] = notice_ids.copy()

        def add(table: str, col: str, values: list[int]) -> None:
            if not values or not table_exists(con, table) or col not in columns(con, table):
                return
            marks = ",".join(["?"] * len(values))
            rows = con.execute(
                f"SELECT id FROM {table} WHERE {col} IN ({marks})", values
            ).fetchall()
            if rows:
                plan.setdefault(table, [])
                plan[table].extend(int(r[0]) for r in rows)
                plan[table] = sorted(set(plan[table]))

        add("payment_allocations", "payment_id", [payment_id])
        add("payment_notices", "matched_payment_id", [payment_id])
        add("payment_notices", "matched_cashier_receipt_id", receipt_ids)
        add("resident_payment_notices", "payment_id", [payment_id])
        add("resident_payment_notices", "receipt_id", receipt_ids)
        add("cashier_reconciliation_cases", "payment_id", [payment_id])
        add("cashier_reconciliation_cases", "receipt_id", receipt_ids)
        add("service_orders", "payment_id", [payment_id])
        add("service_orders", "receipt_id", receipt_ids)
        add("service_order_charge_links", "payment_id", [payment_id])
        add("service_order_charge_links", "receipt_id", receipt_ids)

        refs: list[tuple[str, int]] = [("payments", payment_id)]
        refs.extend(("cashier_receipts", rid) for rid in receipt_ids)
        refs.extend(("cashbox_operations", oid) for oid in operation_ids)
        refs.extend(("payment_notices", nid) for nid in notice_ids)
        refs.extend(("service_orders", sid) for sid in plan.get("service_orders", []))

        # Exact table + row matching. Numeric id alone is unsafe because ids repeat across tables.
        _add_exact_reference(con, plan, "audit_log", "table_name", "record_id", refs)
        _add_exact_reference(con, plan, "operator_audit_log", "table_name", "row_id", refs)
        _add_exact_reference(con, plan, "access_audit_log", "target_table", "target_id", refs)

        return plan
    finally:
        con.close()


def collect_batch_chain(payment_ids: list[int]) -> dict[str, list[int]]:
    merged: dict[str, set[int]] = {}
    for payment_id in sorted(set(payment_ids)):
        for table, ids in collect_chain(payment_id).items():
            merged.setdefault(table, set()).update(ids)
    return {table: sorted(ids) for table, ids in merged.items() if ids}


def chain_summary(plan: dict[str, list[int]]) -> str:
    labels = {
        "payments": "Платёж",
        "cashier_receipts": "Чек",
        "cashbox_operations": "Операции кассы",
        "payment_allocations": "Распределения",
        "payment_notices": "Уведомления",
        "resident_payment_notices": "Уведомления жителей",
        "service_orders": "Заказы/услуги",
        "service_order_charge_links": "Связи заказов",
        "cashier_reconciliation_cases": "Сверки",
        "audit_log": "Аудит",
        "operator_audit_log": "Аудит оператора",
        "access_audit_log": "Аудит доступа",
        "verification_log": "Журнал проверок",
    }
    lines = ["🩺 Сводка хирургического удаления", ""]
    for table, ids in plan.items():
        lines.append(f"• {labels.get(table, table)}: {len(ids)}")
    lines.extend(["", f"Всего записей: {sum(len(v) for v in plan.values())}"])
    return "\n".join(lines)


def ensure_cleanup_log(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_cleanup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            performed_by TEXT,
            payment_id INTEGER,
            receipt_id INTEGER,
            reason_code TEXT NOT NULL,
            reason_text TEXT,
            backup_path TEXT,
            deleted_summary TEXT
        )
        """
    )


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / (
        "before_payment_cleanup_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".db"
    )
    shutil.copy2(DEFAULT_DB, target)
    return target


def apply_cleanup_batch(
    payment_ids: list[int],
    plan: dict[str, list[int]],
    reason_code: str,
    reason_text: str,
    user_id: int,
) -> Path:
    backup = backup_db()
    con = connect()
    try:
        ensure_cleanup_log(con)
        delete_order = [
            "payment_allocations",
            "service_order_charge_links",
            "cashier_reconciliation_cases",
            "payment_notices",
            "resident_payment_notices",
            "service_orders",
            "audit_log",
            "operator_audit_log",
            "access_audit_log",
            "cashbox_operations",
            "cashier_receipts",
            "payments",
        ]
        for table in delete_order:
            ids = plan.get(table) or []
            if not ids or not table_exists(con, table):
                continue
            marks = ",".join(["?"] * len(ids))
            con.execute(f"DELETE FROM {table} WHERE id IN ({marks})", ids)

        receipt_ids = plan.get("cashier_receipts") or []
        for index, payment_id in enumerate(sorted(set(payment_ids))):
            con.execute(
                """
                INSERT INTO developer_cleanup_log(
                    performed_by, payment_id, receipt_id, reason_code,
                    reason_text, backup_path, deleted_summary
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    str(user_id),
                    payment_id,
                    receipt_ids[index] if index < len(receipt_ids) else None,
                    reason_code,
                    reason_text,
                    str(backup),
                    str(plan),
                ),
            )
        con.commit()
        return backup
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def apply_cleanup(
    payment_id: int,
    plan: dict[str, list[int]],
    reason_code: str,
    reason_text: str,
    user_id: int,
) -> Path:
    return apply_cleanup_batch([payment_id], plan, reason_code, reason_text, user_id)


async def show_subjects(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    user_states[user_id] = {"mode": "cashier_admin", "screen": "subjects"}
    await update.message.reply_text(
        "🔎 Субъекты расчётов\n\nВыберите отдельную группу поиска:",
        reply_markup=subject_menu(),
    )


async def show_payment_admin(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    user_states[user_id] = {"mode": "cashier_admin", "screen": "payment_admin"}
    await update.message.reply_text("🧾 Админ платежей", reply_markup=payment_admin_menu())


async def handle_cashier_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_states: dict[int, Any],
    user_id: int,
    *,
    is_super_admin: bool,
) -> bool:
    text = (update.message.text or "").strip()
    state = user_states.get(user_id, {})

    if text == BTN_BILLING_SUBJECTS:
        await show_subjects(update, user_states, user_id)
        return True
    if text == BTN_PAYMENT_ADMIN:
        if not is_super_admin:
            await update.message.reply_text("⛔ Этот раздел доступен только суперадминистратору.")
            return True
        await show_payment_admin(update, user_states, user_id)
        return True

    if state.get("mode") != "cashier_admin":
        return False

    if text in {BTN_BACK, BTN_CANCEL}:
        if state.get("screen") in {"commercial_query", "commercial_select", "commercial_card"}:
            await show_subjects(update, user_states, user_id)
        else:
            await show_payment_admin(update, user_states, user_id)
        return True

    if text == BTN_RESIDENT_SEARCH:
        user_states[user_id] = {"mode": "cashier_admin", "screen": "resident_handoff"}
        await update.message.reply_text(
            "🏠 Жильцы / 🚗 Авто\n\nВведите номер квартиры или несколько цифр госномера.\n"
            "Этот поиск используется основной кассой.",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True

    if text == BTN_COMMERCIAL_SEARCH:
        user_states[user_id] = {"mode": "cashier_admin", "screen": "commercial_query"}
        await update.message.reply_text(
            "🏢 Коммерческие фирмы\n\nВведите часть названия, номер помещения или номер договора.",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True

    if state.get("screen") == "commercial_query":
        rows = commercial_search(text)
        if not rows:
            await update.message.reply_text(
                "⚠ Коммерческая фирма не найдена.",
                reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
            )
            return True
        if len(rows) == 1:
            user_states[user_id] = {
                "mode": "cashier_admin",
                "screen": "commercial_card",
                "commercial": rows[0],
            }
            await update.message.reply_text(
                commercial_card(rows[0]), reply_markup=kb([[BTN_BACK, BTN_MAIN]])
            )
            return True
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "commercial_select",
            "commercial_options": rows,
        }
        await update.message.reply_text(
            "Найдено несколько фирм:", reply_markup=commercial_list_kb(rows)
        )
        return True

    if state.get("screen") == "commercial_select":
        try:
            idx = int(text.split(".", 1)[0]) - 1
            row = (state.get("commercial_options") or [])[idx]
        except Exception:
            await update.message.reply_text(
                "Выберите фирму кнопкой.",
                reply_markup=commercial_list_kb(state.get("commercial_options") or []),
            )
            return True
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "commercial_card",
            "commercial": row,
        }
        await update.message.reply_text(
            commercial_card(row), reply_markup=kb([[BTN_BACK, BTN_MAIN]])
        )
        return True

    if text == BTN_LAST_PAYMENTS:
        rows = recent_payments()
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "payment_list",
        }
        await update.message.reply_text(
            "📜 Последние платежи\n\nВыберите запись:",
            reply_markup=payment_list_kb(rows),
        )
        return True

    if text == BTN_FIND_PAYMENT:
        user_states[user_id] = {"mode": "cashier_admin", "screen": "payment_find"}
        await update.message.reply_text(
            "Введите любой понятный признак:\n\n• квартиру;\n• цифры или полный номер авто;\n• ФИО;\n• дату;\n• сумму;\n• код услуги;\n• номер чека.", reply_markup=kb([[BTN_BACK, BTN_MAIN]])
        )
        return True

    if state.get("screen") == "payment_find":
        rows = search_payments_human(text)
        if not rows:
            await update.message.reply_text("⚠ Платежи не найдены. Попробуйте другой признак.")
            return True
        if len(rows) == 1:
            row = rows[0]; pid = int(row['id'])
            user_states[user_id] = {"mode":"cashier_admin","screen":"payment_card","payment_id":pid}
            await update.message.reply_text(payment_card(row), reply_markup=kb([[BTN_DELETE_SELECTED],[BTN_BACK,BTN_MAIN]]))
            return True
        user_states[user_id] = {"mode":"cashier_admin","screen":"payment_list","payment_rows":rows,"selected_payment_ids":set()}
        await update.message.reply_text(f"Найдено платежей: {len(rows)}\n\nОтметьте нужные:", reply_markup=payment_list_kb(rows))
        return True

    if state.get("screen") == "payment_list" and (text.startswith("☐ #") or text.startswith("☑ #")):
        try:
            pid = int(text.split("#", 1)[1].split()[0])
        except Exception:
            return True
        selected = set(state.get("selected_payment_ids") or set())
        if pid in selected:
            selected.remove(pid)
        else:
            selected.add(pid)
        rows = state.get("payment_rows") or recent_payments()
        user_states[user_id] = {**state, "selected_payment_ids": selected, "payment_rows": rows}
        await update.message.reply_text(
            f"Выбрано платежей: {len(selected)}",
            reply_markup=payment_list_kb(rows, selected),
        )
        return True

    if text == BTN_OPEN_SELECTED and state.get("screen") == "payment_list":
        selected = sorted(set(state.get("selected_payment_ids") or set()))
        if len(selected) != 1:
            await update.message.reply_text("Для открытия выберите ровно один платёж.")
            return True
        pid = selected[0]
        row = get_payment(pid)
        if not row:
            await update.message.reply_text("⚠ Платёж не найден.")
            return True
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "payment_card",
            "payment_id": pid,
        }
        await update.message.reply_text(
            payment_card(row),
            reply_markup=kb([[BTN_DELETE_SELECTED], [BTN_BACK, BTN_MAIN]]),
        )
        return True

    if text == BTN_DELETE_BATCH and state.get("screen") == "payment_list":
        if not is_super_admin:
            await update.message.reply_text("⛔ Удаление доступно только суперадминистратору.")
            return True
        selected = sorted(set(state.get("selected_payment_ids") or set()))
        if not selected:
            await update.message.reply_text("Сначала отметьте платежи.")
            return True
        plan = collect_batch_chain(selected)
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "cleanup_reason",
            "payment_ids": selected,
            "cleanup_plan": plan,
        }
        await update.message.reply_text(
            f"Выбрано платежей: {len(selected)}\n\n" + chain_summary(plan) + "\n\nВыберите причину:",
            reply_markup=reason_menu(),
        )
        return True

    if text == BTN_DELETE_SELECTED and state.get("payment_id"):
        if not is_super_admin:
            await update.message.reply_text("⛔ Удаление доступно только суперадминистратору.")
            return True
        pid = int(state["payment_id"])
        plan = collect_chain(pid)
        user_states[user_id] = {
            "mode": "cashier_admin",
            "screen": "cleanup_reason",
            "payment_id": pid,
            "payment_ids": [pid],
            "cleanup_plan": plan,
        }
        await update.message.reply_text(
            chain_summary(plan) + "\n\nВыберите причину:", reply_markup=reason_menu()
        )
        return True

    if state.get("screen") == "cleanup_reason":
        if text == BTN_REASON_OTHER:
            user_states[user_id] = {**state, "screen": "cleanup_reason_other"}
            await update.message.reply_text(
                "Введите короткую причину:", reply_markup=kb([[BTN_CANCEL]])
            )
            return True
        if text not in REASONS:
            await update.message.reply_text(
                "Выберите причину кнопкой.", reply_markup=reason_menu()
            )
            return True
        user_states[user_id] = {
            **state,
            "screen": "cleanup_confirm",
            "reason_code": REASONS[text],
            "reason_text": text,
        }
        await update.message.reply_text(
            chain_summary(state.get("cleanup_plan") or {}) + f"\n\nПричина: {text}",
            reply_markup=kb([[BTN_CONFIRM_DELETE], [BTN_CANCEL]]),
        )
        return True

    if state.get("screen") == "cleanup_reason_other":
        user_states[user_id] = {
            **state,
            "screen": "cleanup_confirm",
            "reason_code": "OTHER",
            "reason_text": text,
        }
        await update.message.reply_text(
            chain_summary(state.get("cleanup_plan") or {}) + f"\n\nПричина: {text}",
            reply_markup=kb([[BTN_CONFIRM_DELETE], [BTN_CANCEL]]),
        )
        return True

    if text == BTN_CONFIRM_DELETE and state.get("screen") == "cleanup_confirm":
        payment_ids = [int(x) for x in (state.get("payment_ids") or ([state.get("payment_id")] if state.get("payment_id") else []))]
        backup = apply_cleanup_batch(
            payment_ids,
            state.get("cleanup_plan") or {},
            state.get("reason_code") or "UNKNOWN",
            state.get("reason_text") or "",
            user_id,
        )
        await update.message.reply_text(
            "✅ Платёж и связанный шлейф удалены.\n\nРезервная копия:\n"
            + str(backup),
            reply_markup=payment_admin_menu(),
        )
        user_states[user_id] = {"mode": "cashier_admin", "screen": "payment_admin"}
        return True

    return False
