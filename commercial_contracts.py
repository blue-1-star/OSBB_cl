"""
Договорная логика коммерческих помещений.

Этот модуль НЕ отправляет Telegram-сообщения сам.
Он:
- считает задолженность по общему charges/payment_allocations ledger;
- формирует очередь Telegram-уведомлений;
- формирует кандидатов на ручное отключение телефонного доступа;
- не отправляет SMS и не выполняет GSM-команды контроллеру.

Фактическая доставка Telegram-сообщений находится в
commercial_notification_delivery.py и должна вызываться ботом/планировщиком.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse
import json
import sqlite3
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_log


NOTIFICATION_TYPES = {
    "REMINDER",
    "WARNING",
    "ACCESS_SUSPENSION_NOTICE",
    "ACCESS_RESTORED",
}

NOTIFICATION_STATUSES = {
    "DRAFT",
    "READY",
    "SENT",
    "FAILED",
    "CANCELLED",
}

PHONE_STATUSES = {
    "ACTIVE",
    "WARNING",
    "SUSPENSION_CANDIDATE",
    "SUSPENDED_DEBT",
    "MANUAL_BLOCK",
    "CLOSED",
}

ACTION_TYPES = {
    "SUSPEND_RECOMMENDED",
    "SUSPENDED_MANUALLY",
    "RESTORE_RECOMMENDED",
    "RESTORED_MANUALLY",
    "NO_ACTION_OTHER_RIGHTS",
    "DEFERRED",
}

ACTION_STATUSES = {"OPEN", "DONE", "CANCELLED"}


@dataclass
class ContractDebt:
    contract_id: int
    unit_code: str
    contract_number: str | None
    counterparty_name: str | None
    contract_status: str
    outstanding_amount: float
    access_blocking_outstanding_amount: float
    first_open_due_date: str | None
    days_overdue: int
    overdue_charge_ids: list[int]
    access_blocking_charge_ids: list[int]

    @property
    def has_debt(self) -> bool:
        return self.outstanding_amount > 0.00001

    @property
    def has_access_blocking_debt(self) -> bool:
        return self.access_blocking_outstanding_amount > 0.00001


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def money(value: float | int | None) -> str:
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def parse_iso_date(value: str | None) -> date | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def as_of_date(value: str | date | None = None) -> date:
    if isinstance(value, date):
        return value
    parsed = parse_iso_date(text(value))
    return parsed or date.today()


def get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else get_db_file()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    # Для schema_ready одинаково допустимы обычные таблицы и SQL VIEW
    # v_commercial_contract_charge_debt / v_commercial_contract_debt_summary.
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def schema_ready(conn: sqlite3.Connection) -> tuple[bool, str]:
    cur = conn.cursor()
    required = {
        "commercial_contracts",
        "commercial_contract_items",
        "commercial_contract_recipients",
        "commercial_access_phones",
        "commercial_notifications",
        "commercial_access_actions",
        "v_commercial_contract_charge_debt",
        "v_commercial_contract_debt_summary",
    }
    missing = [name for name in sorted(required) if not table_exists(cur, name)]
    if missing:
        return (
            False,
            "Не выполнена миграция договорного ядра КП. Отсутствует: "
            + ", ".join(missing),
        )
    return True, ""


def get_contract(conn: sqlite3.Connection, contract_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("""
        SELECT
            c.id,
            c.unit_id,
            a.unit_code,
            a.apartment_number,
            a.display_name AS unit_display_name,
            c.contract_number,
            c.counterparty_type,
            c.counterparty_name,
            c.status,
            c.valid_from,
            c.valid_to,
            c.payment_due_day,
            c.grace_days,
            c.reminder_days_before_due,
            c.warning_days_overdue,
            c.suspension_candidate_days_overdue,
            c.internal_note
        FROM commercial_contracts c
        JOIN apartments a ON a.id = c.unit_id
        WHERE c.id = ?
    """, (int(contract_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def list_active_contracts(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT
            c.id,
            c.unit_id,
            a.unit_code,
            a.apartment_number,
            a.display_name AS unit_display_name,
            c.contract_number,
            c.counterparty_name,
            c.status,
            c.valid_from,
            c.valid_to,
            c.payment_due_day,
            c.grace_days,
            c.reminder_days_before_due,
            c.warning_days_overdue,
            c.suspension_candidate_days_overdue
        FROM commercial_contracts c
        JOIN apartments a ON a.id = c.unit_id
        WHERE c.status = 'ACTIVE'
        ORDER BY a.entrance_number, a.unit_code, c.id
    """)
    return [dict(row) for row in cur.fetchall()]


def get_contract_items(
    conn: sqlite3.Connection,
    contract_id: int,
) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            contract_id,
            item_code,
            item_name,
            reference_service_code,
            calculation_mode,
            fixed_amount,
            rate_amount,
            quantity_default,
            currency,
            blocks_phone_access_on_debt,
            is_active,
            valid_from,
            valid_to,
            note
        FROM commercial_contract_items
        WHERE contract_id = ?
        ORDER BY id
    """, (int(contract_id),))
    return [dict(row) for row in cur.fetchall()]


def get_contract_recipients(
    conn: sqlite3.Connection,
    contract_id: int,
    *,
    notification_only: bool = False,
) -> list[dict]:
    cur = conn.cursor()
    sql = """
        SELECT
            r.id,
            r.contract_id,
            r.resident_account_id,
            r.telegram_user_id,
            r.recipient_name,
            r.recipient_role,
            r.is_primary,
            r.notification_enabled,
            r.status,
            r.note
        FROM commercial_contract_recipients r
        WHERE r.contract_id = ?
    """
    params: list[Any] = [int(contract_id)]

    if notification_only:
        sql += """
          AND r.notification_enabled = 1
          AND r.status = 'ACTIVE'
          AND COALESCE(TRIM(r.telegram_user_id), '') <> ''
        """

    sql += " ORDER BY r.is_primary DESC, r.id"
    cur.execute(sql, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def get_contract_access_phones(
    conn: sqlite3.Connection,
    contract_id: int,
) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            contract_id,
            phone_normalized,
            phone_display,
            access_purpose,
            status,
            status_reason,
            status_changed_at,
            note
        FROM commercial_access_phones
        WHERE contract_id = ?
        ORDER BY id
    """, (int(contract_id),))
    return [dict(row) for row in cur.fetchall()]


def calculate_contract_debt(
    conn: sqlite3.Connection,
    contract_id: int,
    *,
    on_date: str | date | None = None,
) -> ContractDebt:
    current_date = as_of_date(on_date)
    contract = get_contract(conn, contract_id)
    if not contract:
        raise ValueError(f"Договор id={contract_id} не найден.")

    cur = conn.cursor()
    cur.execute("""
        SELECT
            charge_id,
            commercial_contract_item_id,
            commercial_due_date,
            charge_amount,
            allocated_amount,
            outstanding_amount,
            blocks_phone_access_on_debt
        FROM v_commercial_contract_charge_debt
        WHERE commercial_contract_id = ?
          AND outstanding_amount > 0.00001
        ORDER BY commercial_due_date, charge_id
    """, (int(contract_id),))
    rows = [dict(row) for row in cur.fetchall()]

    outstanding = sum(float(row["outstanding_amount"] or 0) for row in rows)
    blocking_rows = [
        row for row in rows
        if int(row["blocks_phone_access_on_debt"] or 0) == 1
    ]
    blocking_outstanding = sum(
        float(row["outstanding_amount"] or 0)
        for row in blocking_rows
    )

    due_dates = [
        parse_iso_date(row.get("commercial_due_date"))
        for row in rows
        if parse_iso_date(row.get("commercial_due_date"))
    ]
    first_due = min(due_dates) if due_dates else None
    days_overdue = max((current_date - first_due).days, 0) if first_due else 0

    overdue_ids = []
    access_blocking_ids = []
    for row in rows:
        due = parse_iso_date(row.get("commercial_due_date"))
        if due and due < current_date:
            overdue_ids.append(int(row["charge_id"]))
            if int(row["blocks_phone_access_on_debt"] or 0) == 1:
                access_blocking_ids.append(int(row["charge_id"]))

    return ContractDebt(
        contract_id=int(contract_id),
        unit_code=text(contract.get("unit_code") or contract.get("apartment_number")),
        contract_number=text(contract.get("contract_number")) or None,
        counterparty_name=text(contract.get("counterparty_name")) or None,
        contract_status=text(contract.get("status")),
        outstanding_amount=round(outstanding, 2),
        access_blocking_outstanding_amount=round(blocking_outstanding, 2),
        first_open_due_date=first_due.isoformat() if first_due else None,
        days_overdue=days_overdue,
        overdue_charge_ids=overdue_ids,
        access_blocking_charge_ids=access_blocking_ids,
    )


def contract_notification_stage(
    conn: sqlite3.Connection,
    contract_id: int,
    *,
    on_date: str | date | None = None,
) -> str | None:
    """
    Возвращает один приоритетный тип уведомления или None.

    Приоритет:
      ACCESS_SUSPENSION_NOTICE
      WARNING
      REMINDER
    """
    contract = get_contract(conn, contract_id)
    if not contract or contract["status"] != "ACTIVE":
        return None

    debt = calculate_contract_debt(conn, contract_id, on_date=on_date)
    today = as_of_date(on_date)

    if not debt.has_debt:
        return None

    due = parse_iso_date(debt.first_open_due_date)
    if not due:
        return None

    grace_days = int(contract.get("grace_days") or 0)
    effective_due = due + timedelta(days=grace_days)
    days_after_effective_due = (today - effective_due).days

    suspension_days = int(contract.get("suspension_candidate_days_overdue") or 0)
    warning_days = int(contract.get("warning_days_overdue") or 0)
    reminder_days = int(contract.get("reminder_days_before_due") or 0)

    if (
        debt.has_access_blocking_debt
        and days_after_effective_due >= suspension_days
    ):
        return "ACCESS_SUSPENSION_NOTICE"

    if days_after_effective_due >= warning_days:
        return "WARNING"

    if 0 <= (effective_due - today).days <= reminder_days:
        return "REMINDER"

    return None


def build_notification_text(
    contract: dict,
    debt: ContractDebt,
    notification_type: str,
) -> str:
    unit = contract.get("unit_code") or contract.get("apartment_number") or "КП"
    number = f" №{contract['contract_number']}" if contract.get("contract_number") else ""
    person = contract.get("counterparty_name") or "представитель"

    header = {
        "REMINDER": "🔔 Напоминание об оплате",
        "WARNING": "⚠️ Предупреждение о задолженности",
        "ACCESS_SUSPENSION_NOTICE": "⛔ Предупреждение о возможном отключении доступа",
        "ACCESS_RESTORED": "✅ Задолженность погашена",
    }.get(notification_type, "ℹ️ Уведомление")

    lines = [
        header,
        "",
        f"КП: {unit}",
        f"Договор{number}",
        f"Получатель: {person}",
        "",
    ]

    if notification_type == "ACCESS_RESTORED":
        lines.extend([
            "Задолженность по договору погашена.",
            "Если телефонный доступ ранее был отключён вручную, "
            "оператор обработает его восстановление отдельно.",
        ])
        return "\n".join(lines)

    lines.extend([
        f"Текущая задолженность: {money(debt.outstanding_amount)} грн.",
    ])

    if debt.first_open_due_date:
        lines.append(f"Самый ранний срок оплаты: {debt.first_open_due_date}.")

    if debt.days_overdue > 0:
        lines.append(f"Просрочка: {debt.days_overdue} дн.")

    if notification_type == "REMINDER":
        lines.extend([
            "",
            "Просим оплатить сумму в срок согласно условиям договора.",
        ])
    elif notification_type == "WARNING":
        lines.extend([
            "",
            "Просим погасить задолженность. "
            "При дальнейшем непогашении может быть рассмотрено "
            "ручное отключение телефонного доступа к шлагбауму.",
        ])
    elif notification_type == "ACCESS_SUSPENSION_NOTICE":
        lines.extend([
            "",
            f"Задолженность по условиям, связанным с доступом: "
            f"{money(debt.access_blocking_outstanding_amount)} грн.",
            "Доступ пока не отключён автоматически. "
            "При отсутствии оплаты оператор может вручную отключить "
            "номера телефонного доступа, указанные в договоре.",
        ])

    return "\n".join(lines)


def notification_dedupe_key(
    *,
    contract_id: int,
    recipient_id: int,
    notification_type: str,
    first_due_date: str | None,
) -> str:
    """
    Один тип сообщения одному получателю один раз на один долг/срок.
    Изменение суммы платежа не создаёт ежедневный спам.
    """
    return (
        f"contract:{contract_id}"
        f"|recipient:{recipient_id}"
        f"|type:{notification_type}"
        f"|due:{first_due_date or '-'}"
    )


def notification_exists(
    conn: sqlite3.Connection,
    dedupe_key: str,
) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM commercial_notifications WHERE dedupe_key=? LIMIT 1",
        (dedupe_key,),
    )
    return cur.fetchone() is not None


def open_access_action_exists(
    conn: sqlite3.Connection,
    contract_id: int,
    phone_id: int,
    action_type: str,
) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM commercial_access_actions
        WHERE contract_id = ?
          AND access_phone_id = ?
          AND action_type = ?
          AND action_status = 'OPEN'
        LIMIT 1
    """, (int(contract_id), int(phone_id), action_type))
    return cur.fetchone() is not None


def create_access_action(
    conn: sqlite3.Connection,
    *,
    contract_id: int,
    phone_id: int | None,
    notification_id: int | None,
    action_type: str,
    debt: ContractDebt,
    reason: str,
    operator_id: str = "system",
) -> int | None:
    if phone_id is not None and open_access_action_exists(
        conn,
        contract_id,
        phone_id,
        action_type,
    ):
        return None

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO commercial_access_actions (
            contract_id,
            access_phone_id,
            notification_id,
            action_type,
            action_status,
            debt_amount_snapshot,
            days_overdue_snapshot,
            reason,
            operator_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
    """, (
        int(contract_id),
        int(phone_id) if phone_id is not None else None,
        int(notification_id) if notification_id is not None else None,
        action_type,
        float(debt.outstanding_amount),
        int(debt.days_overdue),
        reason,
        str(operator_id),
        now_db(),
        now_db(),
    ))
    return int(cur.lastrowid)


def update_phone_status(
    conn: sqlite3.Connection,
    *,
    phone_id: int,
    status: str,
    reason: str,
) -> bool:
    if status not in PHONE_STATUSES:
        raise ValueError(f"Недопустимый статус телефона: {status}")

    cur = conn.cursor()
    cur.execute("""
        UPDATE commercial_access_phones
        SET
            status = ?,
            status_reason = ?,
            status_changed_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status <> ?
    """, (
        status,
        reason,
        now_db(),
        now_db(),
        int(phone_id),
        status,
    ))
    return cur.rowcount > 0


def queue_notification(
    conn: sqlite3.Connection,
    *,
    contract: dict,
    recipient: dict,
    debt: ContractDebt,
    notification_type: str,
    created_by: str = "system",
) -> tuple[bool, int | None, str]:
    recipient_id = int(recipient["id"])
    dedupe = notification_dedupe_key(
        contract_id=int(contract["id"]),
        recipient_id=recipient_id,
        notification_type=notification_type,
        first_due_date=debt.first_open_due_date,
    )
    if notification_exists(conn, dedupe):
        return False, None, "duplicate"

    message_text = build_notification_text(contract, debt, notification_type)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO commercial_notifications (
            contract_id,
            recipient_id,
            telegram_user_id,
            notification_type,
            status,
            dedupe_key,
            charge_ids_json,
            debt_amount_snapshot,
            days_overdue_snapshot,
            message_text,
            created_by,
            created_at
        )
        VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(contract["id"]),
        recipient_id,
        text(recipient.get("telegram_user_id")) or None,
        notification_type,
        dedupe,
        json.dumps(
            debt.access_blocking_charge_ids
            if notification_type == "ACCESS_SUSPENSION_NOTICE"
            else debt.overdue_charge_ids,
            ensure_ascii=False,
        ),
        float(debt.outstanding_amount),
        int(debt.days_overdue),
        message_text,
        created_by,
        now_db(),
    ))
    notification_id = int(cur.lastrowid)

    audit_log(
        conn=conn,
        operator_id=created_by,
        user_id=created_by,
        actor_type="system" if created_by == "system" else "operator",
        action_type="commercial_telegram_notification_queued",
        table_name="commercial_notifications",
        row_id=notification_id,
        field_name="notification_type,status,debt_amount_snapshot",
        old_value="",
        new_value=(
            f"{notification_type}, READY, "
            f"{money(debt.outstanding_amount)}"
        ),
        source_context="commercial_contracts.py",
        comment="Telegram-уведомление поставлено в очередь. SMS не используется.",
        extra={
            "contract_id": contract["id"],
            "unit_code": contract.get("unit_code"),
            "recipient_telegram_user_id": recipient.get("telegram_user_id"),
            "days_overdue": debt.days_overdue,
        },
        commit=False,
    )

    return True, notification_id, "queued"


def queue_contract_notifications(
    conn: sqlite3.Connection,
    contract_id: int,
    *,
    on_date: str | date | None = None,
    created_by: str = "system",
    apply: bool = False,
) -> dict:
    """
    Формирует Telegram-уведомления и кандидатов на ручное отключение доступа.

    При apply=False:
      только возвращает план. Изменений нет.

    При apply=True:
      создаёт READY уведомления,
      меняет логический статус телефонов WARNING / SUSPENSION_CANDIDATE,
      создаёт OPEN action SUSPEND_RECOMMENDED.
      Физически номер на GSM-контроллере не меняется.
    """
    ready, message = schema_ready(conn)
    if not ready:
        raise RuntimeError(message)

    contract = get_contract(conn, contract_id)
    if not contract:
        raise ValueError(f"Договор id={contract_id} не найден.")

    debt = calculate_contract_debt(conn, contract_id, on_date=on_date)
    recipients = get_contract_recipients(
        conn,
        contract_id,
        notification_only=True,
    )
    phones = get_contract_access_phones(conn, contract_id)
    stage = contract_notification_stage(conn, contract_id, on_date=on_date)

    plan = {
        "contract_id": int(contract_id),
        "unit_code": contract.get("unit_code") or contract.get("apartment_number"),
        "contract_status": contract.get("status"),
        "stage": stage,
        "debt": asdict(debt),
        "recipients_with_telegram": len(recipients),
        "ready_notifications": [],
        "duplicate_notifications": [],
        "access_candidates": [],
        "restore_candidates": [],
        "logical_phone_status_updates": [],
        "apply": bool(apply),
    }

    # Долг погашен: не надо автоматически «включать GSM».
    # Но ранее вручную отключённые номера становятся кандидатами на восстановление.
    if not debt.has_debt:
        for phone in phones:
            current_status = text(phone.get("status"))
            if current_status == "SUSPENDED_DEBT":
                plan["restore_candidates"].append(phone)
                if apply:
                    action_id = create_access_action(
                        conn,
                        contract_id=int(contract_id),
                        phone_id=int(phone["id"]),
                        notification_id=None,
                        action_type="RESTORE_RECOMMENDED",
                        debt=debt,
                        reason=(
                            "Задолженность погашена. "
                            "Требуется ручная проверка и восстановление доступа."
                        ),
                        operator_id=created_by,
                    )
                    if action_id:
                        audit_log(
                            conn=conn,
                            operator_id=created_by,
                            user_id=created_by,
                            actor_type="system" if created_by == "system" else "operator",
                            action_type="commercial_access_restore_recommended",
                            table_name="commercial_access_actions",
                            row_id=action_id,
                            field_name="action_type,action_status",
                            old_value="",
                            new_value="RESTORE_RECOMMENDED, OPEN",
                            source_context="commercial_contracts.py",
                            comment=(
                                "Долг погашен. Физическое восстановление "
                                "доступа требует ручного действия оператора."
                            ),
                            extra={"phone_id": phone["id"], "contract_id": contract_id},
                            commit=False,
                        )
            elif current_status in {"WARNING", "SUSPENSION_CANDIDATE"}:
                plan["logical_phone_status_updates"].append({
                    "phone_id": phone["id"],
                    "from": current_status,
                    "to": "ACTIVE",
                    "reason": "Задолженность погашена до ручного отключения.",
                })
                if apply:
                    update_phone_status(
                        conn,
                        phone_id=int(phone["id"]),
                        status="ACTIVE",
                        reason="Задолженность погашена до ручного отключения.",
                    )

        # Уведомление о погашении создаётся только если оператор ранее фиксировал
        # ручное отключение хотя бы одного номера.
        if plan["restore_candidates"] and recipients:
            for recipient in recipients:
                created, notification_id, state = queue_notification(
                    conn,
                    contract=contract,
                    recipient=recipient,
                    debt=debt,
                    notification_type="ACCESS_RESTORED",
                    created_by=created_by,
                ) if apply else (True, None, "planned")

                record = {
                    "recipient_id": recipient["id"],
                    "telegram_user_id": recipient.get("telegram_user_id"),
                    "notification_type": "ACCESS_RESTORED",
                    "notification_id": notification_id,
                    "state": state,
                }
                (plan["ready_notifications"] if created else plan["duplicate_notifications"]).append(record)

        return plan

    # Нет доступного получателя — не надо терять проблему.
    # Она будет видна оператору как долг без Telegram-привязки.
    if not recipients:
        return plan

    if stage is None:
        return plan

    for recipient in recipients:
        if apply:
            created, notification_id, state = queue_notification(
                conn,
                contract=contract,
                recipient=recipient,
                debt=debt,
                notification_type=stage,
                created_by=created_by,
            )
        else:
            created, notification_id, state = True, None, "planned"

        record = {
            "recipient_id": recipient["id"],
            "telegram_user_id": recipient.get("telegram_user_id"),
            "notification_type": stage,
            "notification_id": notification_id,
            "state": state,
        }
        (plan["ready_notifications"] if created else plan["duplicate_notifications"]).append(record)

        # Кандидаты на ручное отключение создаются только для доступа,
        # который договор явно разрешает блокировать при долге.
        if stage == "ACCESS_SUSPENSION_NOTICE" and debt.has_access_blocking_debt:
            for phone in phones:
                status = text(phone.get("status"))
                if status in {"CLOSED", "MANUAL_BLOCK", "SUSPENDED_DEBT"}:
                    continue

                candidate = {
                    "phone_id": phone["id"],
                    "phone": phone.get("phone_display") or phone.get("phone_normalized"),
                    "current_status": status,
                    "action": "SUSPEND_RECOMMENDED",
                }
                if candidate not in plan["access_candidates"]:
                    plan["access_candidates"].append(candidate)

                if apply:
                    update_phone_status(
                        conn,
                        phone_id=int(phone["id"]),
                        status="SUSPENSION_CANDIDATE",
                        reason=(
                            "Просроченная задолженность по условиям договора, "
                            "которые блокируют телефонный доступ."
                        ),
                    )
                    action_id = create_access_action(
                        conn,
                        contract_id=int(contract_id),
                        phone_id=int(phone["id"]),
                        notification_id=notification_id,
                        action_type="SUSPEND_RECOMMENDED",
                        debt=debt,
                        reason=(
                            "Кандидат на ручное отключение. "
                            "GSM-команда не отправлялась автоматически."
                        ),
                        operator_id=created_by,
                    )
                    if action_id:
                        audit_log(
                            conn=conn,
                            operator_id=created_by,
                            user_id=created_by,
                            actor_type="system" if created_by == "system" else "operator",
                            action_type="commercial_access_suspend_recommended",
                            table_name="commercial_access_actions",
                            row_id=action_id,
                            field_name="action_type,action_status",
                            old_value="",
                            new_value="SUSPEND_RECOMMENDED, OPEN",
                            source_context="commercial_contracts.py",
                            comment=(
                                "Создан кандидат на ручное отключение доступа. "
                                "Система не отправляет SMS-команды контроллеру."
                            ),
                            extra={
                                "phone_id": phone["id"],
                                "contract_id": contract_id,
                                "debt": debt.outstanding_amount,
                            },
                            commit=False,
                        )

        elif stage == "WARNING":
            for phone in phones:
                status = text(phone.get("status"))
                if status == "ACTIVE":
                    plan["logical_phone_status_updates"].append({
                        "phone_id": phone["id"],
                        "from": "ACTIVE",
                        "to": "WARNING",
                        "reason": "Просроченная задолженность: уведомление-предупреждение.",
                    })
                    if apply:
                        update_phone_status(
                            conn,
                            phone_id=int(phone["id"]),
                            status="WARNING",
                            reason="Просроченная задолженность: уведомление-предупреждение.",
                        )

    return plan


def queue_all_active_contract_notifications(
    conn: sqlite3.Connection,
    *,
    on_date: str | date | None = None,
    created_by: str = "system",
    apply: bool = False,
) -> list[dict]:
    plans = []
    for contract in list_active_contracts(conn):
        plans.append(
            queue_contract_notifications(
                conn,
                int(contract["id"]),
                on_date=on_date,
                created_by=created_by,
                apply=apply,
            )
        )
    return plans


def list_access_action_candidates(
    conn: sqlite3.Connection,
    *,
    only_open: bool = True,
) -> list[dict]:
    cur = conn.cursor()
    sql = """
        SELECT
            a.id,
            a.action_type,
            a.action_status,
            a.debt_amount_snapshot,
            a.days_overdue_snapshot,
            a.reason,
            a.created_at,
            c.id AS contract_id,
            u.unit_code,
            u.apartment_number,
            p.phone_display,
            p.phone_normalized,
            p.status AS phone_status
        FROM commercial_access_actions a
        JOIN commercial_contracts c ON c.id = a.contract_id
        JOIN apartments u ON u.id = c.unit_id
        LEFT JOIN commercial_access_phones p ON p.id = a.access_phone_id
    """
    if only_open:
        sql += " WHERE a.action_status = 'OPEN'"
    sql += " ORDER BY a.created_at, a.id"
    cur.execute(sql)
    return [dict(row) for row in cur.fetchall()]


def record_manual_access_action(
    conn: sqlite3.Connection,
    *,
    access_action_id: int,
    action: str,
    operator_id: str,
    comment: str = "",
) -> dict:
    """
    Фиксирует уже выполненное оператором ручное действие.

    Эта функция не общается с GEOS/ GSM. Оператор мог выполнить действие
    вручную SMS-командой с телефона управления; система только фиксирует факт.
    """
    action = text(action).upper()
    if action not in {"SUSPENDED_MANUALLY", "RESTORED_MANUALLY", "CANCELLED"}:
        raise ValueError("Допустимо: SUSPENDED_MANUALLY, RESTORED_MANUALLY, CANCELLED.")

    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            contract_id,
            access_phone_id,
            action_type,
            action_status,
            debt_amount_snapshot,
            days_overdue_snapshot
        FROM commercial_access_actions
        WHERE id = ?
    """, (int(access_action_id),))
    source = cur.fetchone()
    if not source:
        raise ValueError("Действие доступа не найдено.")

    source = dict(source)
    if text(source["action_status"]) != "OPEN":
        raise ValueError("Это действие уже закрыто.")

    if action == "CANCELLED":
        cur.execute("""
            UPDATE commercial_access_actions
            SET action_status='CANCELLED', operator_id=?, performed_at=?, comment=?, updated_at=?
            WHERE id=?
        """, (operator_id, now_db(), comment, now_db(), int(access_action_id)))

        audit_log(
            conn=conn,
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="commercial_access_action_cancelled",
            table_name="commercial_access_actions",
            row_id=access_action_id,
            field_name="action_status",
            old_value="OPEN",
            new_value="CANCELLED",
            source_context="commercial_contracts.py",
            comment=comment or "Оператор отменил рекомендованное действие доступа.",
            extra={"contract_id": source["contract_id"]},
            commit=False,
        )
        return {"action_id": access_action_id, "status": "CANCELLED"}

    if source["access_phone_id"] is None:
        raise ValueError("У действия нет конкретного номера доступа.")

    if action == "SUSPENDED_MANUALLY":
        new_phone_status = "SUSPENDED_DEBT"
        completed_action = "SUSPENDED_MANUALLY"
        description = (
            "Оператор подтвердил ручное отключение номера. "
            "Автоматическая SMS-команда системой не отправлялась."
        )
    else:
        new_phone_status = "ACTIVE"
        completed_action = "RESTORED_MANUALLY"
        description = (
            "Оператор подтвердил ручное восстановление номера. "
            "Автоматическая SMS-команда системой не отправлялась."
        )

    update_phone_status(
        conn,
        phone_id=int(source["access_phone_id"]),
        status=new_phone_status,
        reason=description,
    )

    cur.execute("""
        UPDATE commercial_access_actions
        SET
            action_status='DONE',
            operator_id=?,
            performed_at=?,
            comment=?,
            updated_at=?
        WHERE id=?
    """, (
        operator_id,
        now_db(),
        comment or description,
        now_db(),
        int(access_action_id),
    ))

    audit_log(
        conn=conn,
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="commercial_access_manual_action",
        table_name="commercial_access_actions",
        row_id=access_action_id,
        field_name="action_status,phone_status",
        old_value="OPEN",
        new_value=f"DONE, {new_phone_status}",
        source_context="commercial_contracts.py",
        comment=comment or description,
        extra={
            "contract_id": source["contract_id"],
            "access_phone_id": source["access_phone_id"],
            "manual_action": completed_action,
        },
        commit=False,
    )

    return {
        "action_id": int(access_action_id),
        "status": "DONE",
        "phone_status": new_phone_status,
    }


def format_plan(plan: dict) -> str:
    debt = plan["debt"]
    lines = [
        f"КП: {plan['unit_code']}",
        f"Договор: {plan['contract_id']}",
        f"Этап: {plan['stage'] or '-'}",
        f"Долг: {money(debt['outstanding_amount'])} грн",
        f"Долг, блокирующий доступ: {money(debt['access_blocking_outstanding_amount'])} грн",
        f"Просрочка: {debt['days_overdue']} дн.",
        f"Получателей Telegram: {plan['recipients_with_telegram']}",
        f"Уведомлений: {len(plan['ready_notifications'])}",
        f"Кандидатов на отключение: {len(plan['access_candidates'])}",
        f"Кандидатов на восстановление: {len(plan['restore_candidates'])}",
    ]
    return "\n".join(lines)


def write_queue_report(
    report_file: Path,
    plans: Iterable[dict],
    *,
    apply: bool,
    on_date: date,
) -> None:
    lines = [
        "=" * 104,
        "КОММЕРЧЕСКИЕ ДОГОВОРЫ — TELEGRAM УВЕДОМЛЕНИЯ И КАНДИДАТЫ НА ДОСТУП",
        "=" * 104,
        f"Дата расчёта: {on_date.isoformat()}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {apply}",
        "",
        "Важно:",
        "  • должникам формируются Telegram-уведомления;",
        "  • SMS должникам не формируются;",
        "  • GSM/GEOS не управляется автоматически;",
        "  • кандидат на отключение требует ручного решения оператора.",
        "",
    ]

    any_rows = False
    for plan in plans:
        any_rows = True
        lines.append("-" * 104)
        lines.append(format_plan(plan))

        for item in plan["ready_notifications"]:
            lines.append(
                "  уведомление: "
                f"{item['notification_type']} → Telegram {item['telegram_user_id']} "
                f"({item['state']})"
            )

        for item in plan["access_candidates"]:
            lines.append(
                "  кандидат на отключение: "
                f"{item['phone']} | текущий статус: {item['current_status']}"
            )

        for item in plan["restore_candidates"]:
            lines.append(
                "  кандидат на восстановление: "
                f"{item.get('phone_display') or item.get('phone_normalized')}"
            )

    if not any_rows:
        lines.append("Активных договоров КП пока нет.")

    lines.append("")
    lines.append("APPLIED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")

    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Поставить в очередь Telegram-уведомления по долгам КП."
    )
    parser.add_argument(
        "--contract",
        type=int,
        default=None,
        help="ID одного договора. По умолчанию все ACTIVE договоры.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Дата расчёта YYYY-MM-DD. По умолчанию сегодня.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Создать записи очереди и кандидатов. Без ключа — dry-run.",
    )
    args = parser.parse_args()

    today = as_of_date(args.date)
    conn = get_conn()
    try:
        ready, message = schema_ready(conn)
        if not ready:
            raise RuntimeError(message)

        if args.contract:
            plans = [
                queue_contract_notifications(
                    conn,
                    args.contract,
                    on_date=today,
                    apply=args.apply,
                )
            ]
        else:
            plans = queue_all_active_contract_notifications(
                conn,
                on_date=today,
                apply=args.apply,
            )

        report_file = (
            paths.OSBB_EXPORTS_DIR / "commercial" /
            f"commercial_notification_queue_{today.isoformat()}_"
            f"{'apply' if args.apply else 'dry_run'}_"
            f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
        )
        write_queue_report(
            report_file,
            plans,
            apply=args.apply,
            on_date=today,
        )

        if args.apply:
            conn.commit()
        else:
            conn.rollback()

        print("=" * 104)
        print("COMMERCIAL NOTIFICATION QUEUE")
        print("=" * 104)
        print("DB:", get_db_file())
        print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
        print("Date:", today.isoformat())
        print("Apply:", args.apply)
        print("Contracts checked:", len(plans))
        print("Report:", report_file)
        print()
        print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
