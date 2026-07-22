"""
Отдельный рабочий кабинет охранника поста / кассы O.

Охранник не использует общий админ-раздел. Этот обработчик:
- проверяет гранулярные разрешения перед каждым View/Create/Confirm;
- ограничивает кассу O и пост O;
- не показывает банк, пачки, разнесение, корректировки и общую сверку;
- регистрирует физический приём/выдачу пультов отдельными событиями;
- пишет действия в access_audit_log и operator_audit_log.

Первый рабочий кабинет рассчитан на роль GUARD_O.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import sqlite3
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from access_control import (
    has_guard_workspace_access,
    has_permission,
    schema_ready as access_schema_ready,
    text,
    write_business_access_audit,
)
from cashier_v2_core import (
    create_cash_receipt,
    get_conn,
    get_notice,
    get_unit_by_id,
    money,
    next_month,
    parse_amount,
    resolve_physical_unit,
    service_label,
    service_options,
    table_columns,
    table_exists,
    today,
)


POST_CODE = "O"
CASHBOX_CODE = "O"


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


MENU = [
    ["🔔 Поступления O", "💵 Принять наличные"],
    ["📥 Пульт принят", "📤 Пульт выдан"],
    ["📋 Мои операции"],
    ["🏠 Главное меню"],
]
BACK = "⬅️ К посту O"
HOME = "🏠 Главное меню"


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == "guard_workspace_o":
        return value
    if create:
        value = {"_module": "guard_workspace_o", "mode": "home"}
        user_states[user_id] = value
        return value
    return None


def _global_switch_text(message_text: str) -> bool:
    return message_text in {
        "👤 Клиентский режим", "👤 Режим мешканця", "👤 User mode",
        "🔐 Админ-режим", "🔐 Адмін-режим", "🔐 Admin mode",
        "🛡 Пост охраны O",
    }


def _is_allowed(
    user_id: int,
    resource: str,
    action: str,
    *,
    scope_type: str,
    scope_value: str,
) -> bool:
    return has_permission(
        user_id,
        resource,
        action,
        scope_type=scope_type,
        scope_value=scope_value,
    )


async def _denied(update: Update) -> None:
    await update.message.reply_text(
        "⛔ Нет права на это действие.\n"
        "Доступ проверяется по вашей роли, действию и области поста O."
    )


def _guard_ready() -> tuple[bool, str]:
    access_ok, access_reason = access_schema_ready()
    if not access_ok:
        return False, access_reason

    conn = get_conn()
    try:
        cur = conn.cursor()
        required = {
            "cashier_receipts",
            "cashbox_operations",
            "payments",
            "payment_notices",
            "remote_handover_events",
        }
        missing = sorted(name for name in required if not table_exists(cur, name))
        if missing:
            return False, "Не завершены рабочие таблицы: " + ", ".join(missing)
        return True, ""
    finally:
        conn.close()


async def show_guard_workspace(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    if not has_guard_workspace_access(user_id, cashbox_code=CASHBOX_CODE):
        await _denied(update)
        return

    ready, reason = _guard_ready()
    if not ready:
        await update.message.reply_text("⚠️ " + reason)
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({"_module": "guard_workspace_o", "mode": "home"})

    await update.message.reply_text(
        "🛡 Пост охраны — касса O\n\n"
        "Здесь доступны только действия поста O:\n"
        "• подтвердить фактически полученные наличные;\n"
        "• зарегистрировать новый приём наличных в O;\n"
        "• отметить физический приём или выдачу пульта.\n\n"
        "Банк, массовые пачки, разнесение и корректировки здесь недоступны.",
        reply_markup=kb(MENU),
    )


# ---------------------------------------------------------------------------
# Cash notices for O
# ---------------------------------------------------------------------------

def _cash_notices_o() -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id, notice_number, apartment_number,
                declared_period_code, declared_service_code,
                declared_amount, resident_comment, declared_at
            FROM payment_notices
            WHERE notice_status = 'NEW'
              AND notice_type = 'CASH_HANDOVER'
              AND COALESCE(declared_cashbox_code, 'O') = 'O'
            ORDER BY declared_at, id
            LIMIT 40
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


async def _show_cash_notices(update: Update, state: dict, user_id: int) -> None:
    if not _is_allowed(
        user_id, "payment_notices", "VIEW",
        scope_type="CASHBOX", scope_value=CASHBOX_CODE,
    ):
        await _denied(update)
        return

    rows = _cash_notices_o()
    state["mode"] = "notice_list"
    state["notice_buttons"] = {}
    buttons: list[list[str]] = []
    lines = ["🔔 Ожидают подтверждения — касса O", ""]

    for row in rows:
        label = (
            f"💵 {row['notice_number']} | кв.{row.get('apartment_number') or '-'} | "
            f"{money(row.get('declared_amount'))}"
        )
        state["notice_buttons"][label] = int(row["id"])
        buttons.append([label])
        lines.append(
            f"{row['notice_number']} | кв. {row.get('apartment_number') or '-'} | "
            f"{money(row.get('declared_amount'))} грн. | "
            f"{row.get('declared_period_code') or '-'}"
        )

    if not rows:
        lines.append("Нет уведомлений о наличных для кассы O.")

    buttons.extend([[BACK], [HOME]])
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def _show_notice_card(update: Update, state: dict, notice_id: int) -> None:
    notice = get_notice(notice_id)
    if not notice:
        await update.message.reply_text("Уведомление не найдено.")
        return
    if (
        notice.get("notice_status") != "NEW"
        or notice.get("notice_type") != "CASH_HANDOVER"
        or text(notice.get("declared_cashbox_code") or "O") != CASHBOX_CODE
    ):
        await update.message.reply_text(
            "Это уведомление уже обработано либо относится не к кассе O."
        )
        return

    state["mode"] = "notice_card"
    state["notice_id"] = int(notice_id)
    await update.message.reply_text(
        "\n".join([
            f"🔔 {notice.get('notice_number')}",
            "",
            f"Квартира: {notice.get('apartment_number') or '-'}",
            f"Касса: O — охрана",
            f"Период: {notice.get('declared_period_code') or 'не указан'}",
            f"Услуга: {notice.get('declared_service_code') or '-'}",
            f"Сумма: {money(notice.get('declared_amount'))} грн.",
            f"Комментарий жителя: {notice.get('resident_comment') or '—'}",
            "",
            "Подтверждайте только когда деньги фактически приняты постом O.",
        ]),
        reply_markup=kb([
            ["✅ Деньги приняты в O"],
            ["⬅️ К поступлениям", HOME],
        ]),
    )


async def _confirm_notice(
    update: Update,
    state: dict,
    user_id: int,
    note: str,
) -> None:
    if not _is_allowed(
        user_id, "payment_notices", "CONFIRM",
        scope_type="CASHBOX", scope_value=CASHBOX_CODE,
    ):
        await _denied(update)
        return

    notice = get_notice(int(state["notice_id"]))
    if not notice:
        await update.message.reply_text("Уведомление не найдено.")
        return
    if text(notice.get("declared_cashbox_code") or "O") != CASHBOX_CODE:
        await update.message.reply_text("Уведомление относится не к кассе O.")
        return

    # Import here to keep module import safe if cashier v2 is not yet migrated.
    from cashier_v2_core import confirm_cash_notice

    try:
        result = confirm_cash_notice(
            int(state["notice_id"]),
            operator_id=user_id,
            operator_note="" if note == "-" else note,
        )
    except Exception as exc:
        await update.message.reply_text(f"Не удалось подтвердить поступление: {exc}")
        return

    conn = get_conn()
    try:
        write_business_access_audit(
            conn,
            actor_user_id=user_id,
            action_type="guard_o_cash_notice_confirmed",
            resource="payment_notices",
            action="CONFIRM",
            scope_type="CASHBOX",
            scope_value=CASHBOX_CODE,
            target_table="payment_notices",
            target_id=state["notice_id"],
            details=(
                f"Пост O подтвердил наличные. "
                f"receipt={result['receipt_id']}; payment={result['payment_id']}."
            ),
        )
        conn.commit()
    finally:
        conn.close()

    await update.message.reply_text(
        "✅ Поступление подтверждено.\n\n"
        f"Квитанция: {result['receipt_number']}\n"
        f"Касса O после операции: {money(result['cashbox_balance'])} грн.\n\n"
        "Оплата создана без самостоятельного разнесения по начислениям."
    )
    await _show_cash_notices(update, state, user_id)


# ---------------------------------------------------------------------------
# Manual cash O
# ---------------------------------------------------------------------------

def _service_buttons(state: dict) -> list[list[str]]:
    choices = service_options(state.get("period_code"))
    choices = sorted(
        choices,
        key=lambda item: (
            0 if text(item.get("service_code")).startswith("PARKING") else 1,
            text(item.get("service_name") or item.get("service_code")),
        ),
    )

    mapping: dict[str, dict] = {}
    buttons: list[list[str]] = []
    labels: list[str] = []
    for item in choices:
        base = "📌 " + service_label(item)
        label = base
        suffix = 2
        while label in mapping:
            label = f"{base} [{suffix}]"
            suffix += 1
        mapping[label] = item
        labels.append(label)

    for index in range(0, len(labels), 2):
        buttons.append(labels[index:index + 2])
    buttons.append([BACK, HOME])
    state["service_buttons"] = mapping
    return buttons


async def _ask_manual_unit(update: Update, state: dict) -> None:
    state["mode"] = "manual_cash_unit"
    await update.message.reply_text(
        f"💵 Новый приём наличных — касса O\n\n"
        f"Период по умолчанию: {state['period_code']}\n"
        "Введите номер физической квартиры.",
        reply_markup=kb([[BACK], [HOME]]),
    )


async def _select_cash_unit(update: Update, state: dict, raw: str) -> None:
    kind, units, message = resolve_physical_unit(raw)
    if kind == "UNIT" and units:
        state["unit"] = units[0]
        state["mode"] = "manual_cash_service"
        await update.message.reply_text(
            "Выберите услугу.",
            reply_markup=kb(_service_buttons(state)),
        )
        return

    if kind == "GROUP" and units:
        state["mode"] = "manual_cash_unit_member"
        state["unit_member_buttons"] = {}
        buttons = []
        for unit in units:
            label = f"🏠 кв. {unit.get('apartment_number')}"
            state["unit_member_buttons"][label] = unit
            buttons.append([label])
        buttons.extend([[BACK], [HOME]])
        await update.message.reply_text(
            message,
            reply_markup=kb(buttons),
        )
        return

    await update.message.reply_text("Квартира не найдена. Введите номер ещё раз.")


async def _show_manual_cash_preview(update: Update, state: dict) -> None:
    state["mode"] = "manual_cash_review"
    unit = state["unit"]
    service = state["service"]
    note = text(state.get("note"))
    note_display = note or "Стандартная запись: принято наличными на посту O."
    await update.message.reply_text(
        "\n".join([
            "💵 Предпросмотр приёма наличных",
            "",
            f"Касса: O — охрана",
            f"Квартира: {unit.get('apartment_number')}",
            f"Период: {state.get('period_code') or 'не указан'}",
            f"Услуга: {service_label(service)}",
            f"Сумма: {money(state['amount'])} грн.",
            f"Примечание: {note_display}",
            "",
            "Оплата будет создана как неразнесённая.",
            "Охранник не может самостоятельно менять начисления или распределять платёж.",
        ]),
        reply_markup=kb([
            ["✅ Принять наличные в O"],
            ["✏️ Сумма", "📝 Примечание"],
            ["❌ Отменить", HOME],
        ]),
    )


async def _save_manual_cash(
    update: Update,
    user_states: dict,
    state: dict,
    user_id: int,
) -> None:
    if not _is_allowed(
        user_id, "cashier_receipts", "CREATE",
        scope_type="CASHBOX", scope_value=CASHBOX_CODE,
    ):
        await _denied(update)
        return

    note = text(state.get("note")) or "Принято наличными на посту O."

    conn = get_conn()
    try:
        cur = conn.cursor()
        result = create_cash_receipt(
            cur,
            apartment=state["unit"],
            cashbox_code=CASHBOX_CODE,
            receipt_date=today(),
            period_code=state.get("period_code"),
            service=state["service"],
            amount=float(state["amount"]),
            source_text=f"[Пост O] {note}",
            operator_id=user_id,
            origin_kind="GUARD_POST_O",
            auto_allocate_charge_id=None,
        )
        write_business_access_audit(
            conn,
            actor_user_id=user_id,
            action_type="guard_o_cash_receipt_created",
            resource="cashier_receipts",
            action="CREATE",
            scope_type="CASHBOX",
            scope_value=CASHBOX_CODE,
            target_table="cashier_receipts",
            target_id=result["receipt_id"],
            details=(
                f"Пост O принял наличные: кв.{state['unit'].get('apartment_number')}, "
                f"{money(state['amount'])} грн., receipt={result['receipt_number']}."
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"Не удалось сохранить приём наличных: {exc}")
        return
    finally:
        conn.close()

    await update.message.reply_text(
        "✅ Наличные приняты в кассу O.\n\n"
        f"Квитанция: {result['receipt_number']}\n"
        f"Остаток O: {money(result['cashbox_balance'])} грн.\n"
        "Платёж пока неразнесён по начислениям."
    )
    await show_guard_workspace(update, user_states, user_id)


# ---------------------------------------------------------------------------
# Remote physical handovers
# ---------------------------------------------------------------------------


# OSBB_REMOTE_DEBT_GATE_V1_GUARD_HELPERS
def _remote_gate_allocation_amount_column(columns) -> str | None:
    if "amount" in columns:
        return "amount"
    if "allocated_amount" in columns:
        return "allocated_amount"
    return None


def _remote_gate_service_is_blocking(service_code: object) -> bool:
    service = text(service_code).upper()
    if not service:
        return False
    return (
        service.startswith("PARKING")
        or service.startswith("BARRIER")
        or "PARK" in service
        or "ШЛАГ" in service
        or "SHLAG" in service
    )


def _remote_gate_block_message(apartment_number: object, amount: float, reason: str = "") -> str:
    apt = text(apartment_number) or "-"
    if reason:
        return (
            f"⚠️ По кв.{apt} невозможно автоматически проверить задолженность. "
            "Выдача пульта через пост O временно недоступна. "
            "Направьте жильца к оператору ОСББ для сверки."
        )
    return (
        f"⚠️ По кв.{apt} есть задолженность за парковку / доступ к шлагбауму: "
        f"{amount:.2f} грн. "
        "Пульт нельзя выдавать до оплаты или сверки с оператором."
    )


def _remote_gate_debt_for_apartment(cur, apartment_number: object) -> dict:
    """
    Read-only debt gate for physical remote issue.

    Checks charges minus payment_allocations for PARKING/BARRIER services.
    Does not write anything to DB.
    """
    apt = text(apartment_number)
    if not apt:
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message("-", 0.0, "missing apartment_number"),
        }

    if not table_exists(cur, "charges"):
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, "charges table missing"),
        }

    charge_cols = table_columns(cur, "charges")
    if "apartment_number" not in charge_cols or "amount" not in charge_cols:
        return {
            "allowed": False,
            "outstanding_total": 0.0,
            "message": _remote_gate_block_message(apt, 0.0, "charge link columns missing"),
        }

    service_expr = (
        "COALESCE(c.base_service_code, c.service_code, '')"
        if "base_service_code" in charge_cols and "service_code" in charge_cols
        else ("COALESCE(c.service_code, '')" if "service_code" in charge_cols else "''")
    )
    period_expr = "COALESCE(c.period_code, '')" if "period_code" in charge_cols else "''"
    status_filter = (
        "AND COALESCE(c.charge_status, '') <> 'cancelled'"
        if "charge_status" in charge_cols
        else (
            "AND COALESCE(c.status, '') <> 'cancelled'"
            if "status" in charge_cols else ""
        )
    )

    allocation_join = ""
    allocation_select = "0 AS allocated_amount"
    if table_exists(cur, "payment_allocations"):
        alloc_cols = table_columns(cur, "payment_allocations")
        amount_col = _remote_gate_allocation_amount_column(alloc_cols)
        if amount_col and "charge_id" in alloc_cols:
            allocation_join = (
                f'LEFT JOIN payment_allocations pa ON pa.charge_id = c.id'
            )
            allocation_select = (
                f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'
            )

    cur.execute(f"""
        SELECT
            c.id AS charge_id,
            {service_expr} AS service_code,
            {period_expr} AS period_code,
            c.amount AS amount,
            {allocation_select}
        FROM charges c
        {allocation_join}
        WHERE c.apartment_number = ?
        {status_filter}
        GROUP BY c.id
        ORDER BY {period_expr}, c.id
    """, (apt,))

    total = 0.0
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        if not _remote_gate_service_is_blocking(item.get("service_code")):
            continue
        amount = float(item.get("amount") or 0)
        allocated = float(item.get("allocated_amount") or 0)
        rest = max(0.0, amount - allocated)
        if rest > 0.01:
            total += rest
            item["outstanding_amount"] = rest
            rows.append(item)

    if total > 0.01:
        return {
            "allowed": False,
            "outstanding_total": round(total, 2),
            "rows": rows,
            "message": _remote_gate_block_message(apt, total),
        }

    return {
        "allowed": True,
        "outstanding_total": 0.0,
        "rows": [],
        "message": "",
    }

def _remote_rows_for_issue() -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "remote_requests"):
            return []
        cur.execute(
            """
            SELECT id, apartment_id, apartment_number, request_kind,
                   quantity, resident_comment, status, created_at
            FROM remote_requests
            WHERE status IN ('NEW', 'IN_REVIEW')
            ORDER BY CASE status WHEN 'NEW' THEN 1 ELSE 2 END, id
            LIMIT 40
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _insert_remote_event(
    conn: sqlite3.Connection,
    *,
    event_kind: str,
    operator_id: int,
    apartment: dict | None = None,
    remote_request: dict | None = None,
    quantity: int,
    note: str,
) -> int:
    cur = conn.cursor()
    unit = apartment or {}
    request = remote_request or {}
    cur.execute(
        """
        INSERT INTO remote_handover_events (
            event_kind, event_status, post_code, remote_request_id,
            apartment_id, apartment_number, quantity, operator_id,
            note, created_at, updated_at
        )
        VALUES (?, 'CONFIRMED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_kind,
            POST_CODE,
            request.get("id"),
            unit.get("id") or request.get("apartment_id"),
            text(unit.get("apartment_number") or request.get("apartment_number")),
            int(quantity),
            str(operator_id),
            note or None,
            now_db(),
            now_db(),
        ),
    )
    return int(cur.lastrowid)


async def _start_remote_received(update: Update, state: dict, user_id: int) -> None:
    if not _is_allowed(
        user_id, "remote_handover_events", "CREATE",
        scope_type="POST", scope_value=POST_CODE,
    ):
        await _denied(update)
        return

    state.clear()
    state.update({"_module": "guard_workspace_o", "mode": "remote_received_unit"})
    await update.message.reply_text(
        "📥 Пульт принят на пост O\n\n"
        "Введите номер квартиры, от которой физически принят пульт.",
        reply_markup=kb([[BACK], [HOME]]),
    )


async def _select_remote_received_unit(update: Update, state: dict, raw: str) -> None:
    kind, units, message = resolve_physical_unit(raw)
    if kind == "UNIT" and units:
        state["unit"] = units[0]
        state["mode"] = "remote_received_quantity"
        await update.message.reply_text(
            "Введите количество принятых пультов.",
            reply_markup=kb([["1"], [BACK, HOME]]),
        )
        return

    if kind == "GROUP" and units:
        state["mode"] = "remote_received_unit_member"
        state["unit_member_buttons"] = {}
        buttons = []
        for unit in units:
            label = f"🏠 кв. {unit.get('apartment_number')}"
            state["unit_member_buttons"][label] = unit
            buttons.append([label])
        buttons.extend([[BACK], [HOME]])
        await update.message.reply_text(message, reply_markup=kb(buttons))
        return

    await update.message.reply_text("Квартира не найдена. Введите номер ещё раз.")


async def _save_remote_received(
    update: Update,
    user_states: dict,
    state: dict,
    user_id: int,
    note: str,
) -> None:
    if not _is_allowed(
        user_id, "remote_handover_events", "CREATE",
        scope_type="POST", scope_value=POST_CODE,
    ):
        await _denied(update)
        return

    conn = get_conn()
    try:
        event_id = _insert_remote_event(
            conn,
            event_kind="RECEIVED_AT_POST",
            operator_id=user_id,
            apartment=state["unit"],
            quantity=int(state["quantity"]),
            note=note,
        )
        write_business_access_audit(
            conn,
            actor_user_id=user_id,
            action_type="guard_o_remote_received",
            resource="remote_handover_events",
            action="CREATE",
            scope_type="POST",
            scope_value=POST_CODE,
            target_table="remote_handover_events",
            target_id=event_id,
            details=(
                f"Пульт принят на пост O: кв.{state['unit'].get('apartment_number')}, "
                f"количество={state['quantity']}."
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"Не удалось сохранить приём пульта: {exc}")
        return
    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ Пульт принят на пост O.\nСобытие: #{event_id}\n"
        "Это физическая отметка; складской остаток будет вести отдельный модуль."
    )
    await show_guard_workspace(update, user_states, user_id)


async def _show_remote_issue_list(update: Update, state: dict, user_id: int) -> None:
    if not _is_allowed(
        user_id, "remote_requests", "VIEW",
        scope_type="POST", scope_value=POST_CODE,
    ):
        await _denied(update)
        return

    rows = _remote_rows_for_issue()
    state["mode"] = "remote_issue_list"
    state["remote_issue_buttons"] = {}
    buttons = []
    lines = ["📤 Выдать пульт по заявке", ""]

    for item in rows:
        label = f"🔑 #{item['id']} | кв.{item.get('apartment_number') or '-'} | {item.get('quantity') or 1} шт."
        state["remote_issue_buttons"][label] = int(item["id"])
        buttons.append([label])
        lines.append(
            f"#{item['id']} | кв.{item.get('apartment_number') or '-'} | "
            f"{item.get('request_kind') or '-'} × {item.get('quantity') or 1} | "
            f"{item.get('status')}"
        )

    if not rows:
        lines.append("Нет заявок NEW / IN_REVIEW для выдачи.")

    buttons.extend([[BACK], [HOME]])
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


def _remote_request(request_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM remote_requests WHERE id = ?", (int(request_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def _show_remote_issue_card(update: Update, state: dict, request_id: int) -> None:
    row = _remote_request(request_id)
    if not row or row.get("status") not in {"NEW", "IN_REVIEW"}:
        await update.message.reply_text("Заявка не найдена или уже закрыта.")
        return

    conn = get_conn()
    try:
        gate = _remote_gate_debt_for_apartment(conn.cursor(), row.get("apartment_number"))
    finally:
        conn.close()

    state["mode"] = "remote_issue_card"
    state["remote_request_id"] = int(request_id)

    lines = [
        f"🔑 Заявка #{row['id']}",
        "",
        f"Квартира: {row.get('apartment_number') or '-'}",
        f"Вид: {row.get('request_kind') or '-'}",
        f"Количество: {row.get('quantity') or 1}",
        f"Комментарий жителя: {row.get('resident_comment') or '—'}",
        f"Статус: {row.get('status')}",
        "",
    ]

    if not gate.get("allowed"):
        lines.extend([
            gate.get("message") or "⚠️ Выдача пульта временно недоступна.",
            "",
            "Действие доступно только после оплаты или ручной сверки оператором.",
        ])
        buttons = [["⬅️ К заявкам", HOME]]
    else:
        lines.append("Подтверждайте только после фактической выдачи пульта.")
        buttons = [
            ["✅ Пульт выдан"],
            ["⬅️ К заявкам", HOME],
        ]

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=kb(buttons),
    )


async def _save_remote_issued(update: Update, state: dict, user_id: int, note: str) -> None:
    if not (
        _is_allowed(
            user_id, "remote_requests", "ISSUE",
            scope_type="POST", scope_value=POST_CODE,
        )
        and _is_allowed(
            user_id, "remote_handover_events", "CREATE",
            scope_type="POST", scope_value=POST_CODE,
        )
    ):
        await _denied(update)
        return

    request_id = int(state["remote_request_id"])
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM remote_requests WHERE id = ?",
            (request_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Заявка не найдена.")
        request = dict(row)
        if request.get("status") not in {"NEW", "IN_REVIEW"}:
            raise ValueError("Заявка уже обработана другим сотрудником.")

        gate = _remote_gate_debt_for_apartment(cur, request.get("apartment_number"))
        if not gate.get("allowed"):
            raise ValueError(gate.get("message") or "Выдача пульта временно недоступна из-за задолженности.")

        event_id = _insert_remote_event(
            conn,
            event_kind="ISSUED_FROM_POST",
            operator_id=user_id,
            remote_request=request,
            quantity=int(request.get("quantity") or 1),
            note=note,
        )

        rcols = table_columns(cur, "remote_requests")
        updates = {
            "status": "ISSUED",
            "operator_id": str(user_id),
            "operator_note": note or None,
            "updated_at": now_db(),
            "issued_at": now_db(),
            "closed_at": now_db(),
        }
        actual = {key: value for key, value in updates.items() if key in rcols}
        assignments = ", ".join(f"{key} = ?" for key in actual)
        cur.execute(
            f"UPDATE remote_requests SET {assignments} WHERE id = ?",
            tuple(actual.values()) + (request_id,),
        )

        write_business_access_audit(
            conn,
            actor_user_id=user_id,
            action_type="guard_o_remote_issued",
            resource="remote_requests",
            action="ISSUE",
            scope_type="POST",
            scope_value=POST_CODE,
            target_table="remote_requests",
            target_id=request_id,
            details=(
                f"Пульт выдан с поста O. Event={event_id}; "
                f"кв.{request.get('apartment_number')}; количество={request.get('quantity') or 1}."
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"Не удалось подтвердить выдачу: {exc}")
        return
    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ Пульт выдан.\nЗаявка #{request_id} закрыта как ISSUED.\n"
        f"Событие выдачи: #{event_id}."
    )
    await _show_remote_issue_list(update, state, user_id)


# ---------------------------------------------------------------------------
# Guard's own log
# ---------------------------------------------------------------------------

def _guard_operations(user_id: int) -> tuple[list[dict], list[dict]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT receipt_number, receipt_date, apartment_number, amount,
                   receipt_kind, source_text
            FROM cashier_receipts
            WHERE operator_id = ?
              AND cashbox_code = 'O'
            ORDER BY id DESC
            LIMIT 15
            """,
            (str(user_id),),
        )
        receipts = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT id, event_kind, apartment_number, quantity, note, created_at
            FROM remote_handover_events
            WHERE operator_id = ?
              AND post_code = 'O'
            ORDER BY id DESC
            LIMIT 15
            """,
            (str(user_id),),
        )
        remotes = [dict(row) for row in cur.fetchall()]
        return receipts, remotes
    finally:
        conn.close()


async def _show_my_operations(update: Update, state: dict, user_id: int) -> None:
    cash_ok = _is_allowed(
        user_id, "cashier_receipts", "VIEW",
        scope_type="CASHBOX", scope_value=CASHBOX_CODE,
    )
    remote_ok = _is_allowed(
        user_id, "remote_handover_events", "VIEW",
        scope_type="POST", scope_value=POST_CODE,
    )
    if not cash_ok and not remote_ok:
        await _denied(update)
        return

    receipts, remotes = _guard_operations(user_id)
    lines = ["📋 Мои операции — пост O", ""]

    if cash_ok:
        lines.append("Наличные:")
        if receipts:
            for item in receipts:
                lines.append(
                    f"• {item.get('receipt_date')} | {item.get('receipt_number')} | "
                    f"кв.{item.get('apartment_number') or '-'} | "
                    f"{money(item.get('amount'))} грн."
                )
        else:
            lines.append("• нет")

    if remote_ok:
        lines.extend(["", "Пульты:"])
        if remotes:
            for item in remotes:
                lines.append(
                    f"• #{item.get('id')} | {item.get('event_kind')} | "
                    f"кв.{item.get('apartment_number') or '-'} | "
                    f"{item.get('quantity') or 1} шт."
                )
        else:
            lines.append("• нет")

    state["mode"] = "my_operations"
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=kb([[BACK], [HOME]]),
    )


# ---------------------------------------------------------------------------
# Main state machine
# ---------------------------------------------------------------------------

async def handle_guard_workspace_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    lang: str = "ru",
) -> bool:
    message_text = text(message_text)

    # Allow parking_bot.py to process global mode choices.
    if _global_switch_text(message_text) and message_text != "🛡 Пост охраны O":
        return False

    state = _state(user_states, user_id, create=False)

    # Entry button from the main mode selector.
    if message_text == "🛡 Пост охраны O":
        await show_guard_workspace(update, user_states, user_id)
        return True

    if not state:
        return False

    if not has_guard_workspace_access(user_id, cashbox_code=CASHBOX_CODE):
        user_states.pop(user_id, None)
        await _denied(update)
        return True

    if message_text in {BACK, HOME}:
        await show_guard_workspace(update, user_states, user_id)
        return True

    mode = text(state.get("mode"))

    if mode == "home":
        if message_text == "🔔 Поступления O":
            await _show_cash_notices(update, state, user_id)
            return True
        if message_text == "💵 Принять наличные":
            if not _is_allowed(
                user_id, "cashier_receipts", "CREATE",
                scope_type="CASHBOX", scope_value=CASHBOX_CODE,
            ):
                await _denied(update)
                return True
            state.clear()
            state.update({
                "_module": "guard_workspace_o",
                "mode": "manual_cash_unit",
                "period_code": next_month(),
            })
            await _ask_manual_unit(update, state)
            return True
        if message_text == "📥 Пульт принят":
            await _start_remote_received(update, state, user_id)
            return True
        if message_text == "📤 Пульт выдан":
            await _show_remote_issue_list(update, state, user_id)
            return True
        if message_text == "📋 Мои операции":
            await _show_my_operations(update, state, user_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.", reply_markup=kb(MENU))
        return True

    # ---- notices
    if mode == "notice_list":
        notice_id = (state.get("notice_buttons") or {}).get(message_text)
        if notice_id:
            await _show_notice_card(update, state, int(notice_id))
        else:
            await update.message.reply_text("Выберите поступление кнопкой.")
        return True

    if mode == "notice_card":
        if message_text == "✅ Деньги приняты в O":
            # Для обычной операции отдельный текст не нужен:
            # факт подтверждения, охранник, касса, сумма и время записываются
            # системой. Исключения будут оформляться отдельным действием.
            await _confirm_notice(update, state, user_id, "")
            return True
        if message_text == "⬅️ К поступлениям":
            await _show_cash_notices(update, state, user_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    # Совместимость с незавершённым старым диалогом:
    # если бот был перезапущен именно на старом шаге note, «.» или «-»
    # завершит его без свободного текста.
    if mode == "notice_note":
        await _confirm_notice(
            update,
            state,
            user_id,
            "" if message_text in {".", "-"} else message_text,
        )
        return True

    # ---- manual cash
    if mode == "manual_cash_unit":
        await _select_cash_unit(update, state, message_text)
        return True

    if mode == "manual_cash_unit_member":
        unit = (state.get("unit_member_buttons") or {}).get(message_text)
        if not unit:
            await update.message.reply_text("Выберите физическую квартиру кнопкой.")
            return True
        state["unit"] = unit
        state["mode"] = "manual_cash_service"
        await update.message.reply_text(
            "Выберите услугу.",
            reply_markup=kb(_service_buttons(state)),
        )
        return True

    if mode == "manual_cash_service":
        service = (state.get("service_buttons") or {}).get(message_text)
        if not service:
            await update.message.reply_text("Выберите услугу кнопкой.")
            return True
        state["service"] = service
        state["mode"] = "manual_cash_amount"
        await update.message.reply_text(
            "Введите фактически принятую сумму.",
            reply_markup=kb([[BACK], [HOME]]),
        )
        return True

    if mode == "manual_cash_amount":
        try:
            state["amount"] = parse_amount(message_text)
        except Exception as exc:
            await update.message.reply_text(f"Ошибка: {exc}")
            return True

        # Обычная операция уже документирована структурированными полями:
        # касса, квартира, период, услуга, сумма, время и охранник.
        # Свободный текст нужен только для исключений.
        state.setdefault("note", "")
        await _show_manual_cash_preview(update, state)
        return True

    if mode == "manual_cash_note":
        # Точка или дефис: оставить системную стандартную отметку.
        state["note"] = "" if message_text in {".", "-"} else message_text
        await _show_manual_cash_preview(update, state)
        return True

    if mode == "manual_cash_review":
        if message_text == "✅ Принять наличные в O":
            await _save_manual_cash(update, user_states, state, user_id)
            return True
        if message_text == "✏️ Сумма":
            state["mode"] = "manual_cash_amount"
            await update.message.reply_text("Введите новую сумму.", reply_markup=kb([[BACK], [HOME]]))
            return True
        if message_text in {"📝 Примечание", "📝 Основание"}:
            state["mode"] = "manual_cash_note"
            await update.message.reply_text(
                "Введите короткое примечание только для исключения.\n"
                "Отправьте . или - — система оставит стандартную запись.",
                reply_markup=kb([["."], [BACK, HOME]]),
            )
            return True

        if message_text == "❌ Отменить":
            await show_guard_workspace(update, user_states, user_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    # ---- remote received
    if mode == "remote_received_unit":
        await _select_remote_received_unit(update, state, message_text)
        return True

    if mode == "remote_received_unit_member":
        unit = (state.get("unit_member_buttons") or {}).get(message_text)
        if not unit:
            await update.message.reply_text("Выберите физическую квартиру кнопкой.")
            return True
        state["unit"] = unit
        state["mode"] = "remote_received_quantity"
        await update.message.reply_text(
            "Введите количество принятых пультов.",
            reply_markup=kb([["1"], [BACK, HOME]]),
        )
        return True

    if mode == "remote_received_quantity":
        try:
            quantity = int(message_text)
            if quantity < 1 or quantity > 20:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введите целое количество от 1 до 20.")
            return True
        state["quantity"] = quantity
        state["mode"] = "remote_received_note"
        await update.message.reply_text(
            "Введите отметку охранника или «-».",
            reply_markup=kb([[BACK], [HOME]]),
        )
        return True

    if mode == "remote_received_note":
        await _save_remote_received(
            update, user_states, state, user_id,
            "" if message_text == "-" else message_text,
        )
        return True

    # ---- remote issue
    if mode == "remote_issue_list":
        request_id = (state.get("remote_issue_buttons") or {}).get(message_text)
        if request_id:
            await _show_remote_issue_card(update, state, int(request_id))
        else:
            await update.message.reply_text("Выберите заявку кнопкой.")
        return True

    if mode == "remote_issue_card":
        if message_text == "✅ Пульт выдан":
            state["mode"] = "remote_issue_note"
            await update.message.reply_text(
                "Введите отметку охранника или «-».",
                reply_markup=kb([["⬅️ К заявкам"], [HOME]]),
            )
            return True
        if message_text == "⬅️ К заявкам":
            await _show_remote_issue_list(update, state, user_id)
            return True
        await update.message.reply_text("Выберите действие кнопкой.")
        return True

    if mode == "remote_issue_note":
        await _save_remote_issued(
            update, state, user_id, "" if message_text == "-" else message_text
        )
        return True

    if mode == "my_operations":
        await show_guard_workspace(update, user_states, user_id)
        return True

    await show_guard_workspace(update, user_states, user_id)
    return True
