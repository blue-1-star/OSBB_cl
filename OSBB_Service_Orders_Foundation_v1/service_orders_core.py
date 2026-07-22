"""
Общее ядро заказов услуг ОСББ.

Здесь одна модель для:
- перепрошивки собственного пульта;
- нового пульта со склада;
- восстановленного пульта со склада;
- подключения телефонного доступа;
- будущих обычных разовых услуг.

Деньги, склад/пульт и выполнение услуги не подменяют друг друга:
они связаны сервисным заказом и подтверждаются отдельными шагами.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
for folder in (ROOT, BOTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None

try:
    from access_control import has_permission
except Exception:
    has_permission = None


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return date.today().strftime("%Y-%m-%d")


def money(value: Any) -> str:
    amount = float(value or 0)
    return (
        f"{int(amount):,}".replace(",", " ")
        if amount.is_integer()
        else f"{amount:,.2f}".replace(",", " ")
    )


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


def table_columns(cur: sqlite3.Cursor, name: str) -> set[str]:
    if not table_exists(cur, name):
        return set()
    cur.execute(f'PRAGMA table_info("{name}")')
    return {row[1] for row in cur.fetchall()}


def required_tables() -> set[str]:
    return {
        "service_workflow_profiles",
        "service_workflow_steps",
        "service_item_workflows",
        "service_price_versions",
        "service_orders",
        "service_order_steps",
        "service_order_charge_links",
        "service_order_payment_links",
        "service_order_events",
        "remote_assets",
        "remote_asset_movements",
        "remote_order_details",
        "service_access_credentials",
    }


def schema_ready(conn: sqlite3.Connection | None = None) -> tuple[bool, str]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        missing = sorted(name for name in required_tables() if not table_exists(cur, name))
        if missing:
            return False, "Не завершена миграция заказов услуг: " + ", ".join(missing)
        return True, ""
    finally:
        if owns:
            conn.close()


def _permission_or_raise(
    actor_id: int | str | None,
    resource: str,
    action: str,
    scope_type: str,
    scope_value: str,
) -> None:
    """
    Core intentionally does not require permissions for actor_id=None:
    this allows controlled system migrations/preflight. Bot handlers must
    always pass actor_id and enforce permissions before calling write methods.
    """
    if actor_id is None or has_permission is None:
        return
    if not has_permission(
        actor_id,
        resource,
        action,
        scope_type=scope_type,
        scope_value=scope_value,
    ):
        raise PermissionError(
            f"Нет права {resource}.{action} для {scope_type}:{scope_value}."
        )


def _fetchone_dict(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def _service_item(cur: sqlite3.Cursor, service_item_code: str) -> dict:
    if not table_exists(cur, "service_items"):
        raise RuntimeError("Не найдена service_items.")
    cols = table_columns(cur, "service_items")
    if "service_item_code" not in cols:
        raise RuntimeError("В service_items отсутствует service_item_code.")

    fields = [
        "service_item_code",
        "service_code" if "service_code" in cols else "NULL AS service_code",
        "service_item_name" if "service_item_name" in cols else "service_item_code AS service_item_name",
        "amount_default" if "amount_default" in cols else "NULL AS amount_default",
        "currency" if "currency" in cols else "'UAH' AS currency",
        "status" if "status" in cols else "'active' AS status",
        "is_active" if "is_active" in cols else "1 AS is_active",
    ]
    item = _fetchone_dict(
        cur,
        f"""
        SELECT {', '.join(fields)}
        FROM service_items
        WHERE service_item_code = ?
        """,
        (service_item_code,),
    )
    if not item:
        raise ValueError(f"Статья услуги не найдена: {service_item_code}")
    return item


def get_service_workflow(cur: sqlite3.Cursor, service_item_code: str) -> dict:
    row = _fetchone_dict(
        cur,
        """
        SELECT
            w.service_item_code,
            w.workflow_profile_code,
            w.resident_request_enabled,
            w.operator_create_enabled,
            w.requires_charge,
            w.payment_timing,
            w.inventory_mode,
            w.resident_asset_mode,
            w.is_active,
            p.profile_name,
            p.service_category,
            p.description AS profile_description
        FROM service_item_workflows w
        JOIN service_workflow_profiles p
          ON p.profile_code = w.workflow_profile_code
         AND p.is_active = 1
        WHERE w.service_item_code = ?
          AND w.is_active = 1
        """,
        (service_item_code,),
    )
    if not row:
        raise ValueError(
            "Для статьи не настроен активный профиль выполнения: "
            f"{service_item_code}"
        )
    return row


def effective_price(
    cur: sqlite3.Cursor,
    service_item_code: str,
    *,
    on_date: str | None = None,
) -> tuple[float | None, str]:
    on_date = on_date or today()
    row = _fetchone_dict(
        cur,
        """
        SELECT amount, currency
        FROM service_price_versions
        WHERE service_item_code = ?
          AND is_active = 1
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to = '' OR effective_to >= ?)
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
        """,
        (service_item_code, on_date, on_date),
    )
    if row:
        return float(row["amount"]), text(row["currency"]) or "UAH"

    item = _service_item(cur, service_item_code)
    amount = item.get("amount_default")
    return (float(amount) if amount is not None else None, text(item.get("currency")) or "UAH")


def _next_order_number(cur: sqlite3.Cursor) -> str:
    prefix = f"SO-{datetime.now():%Y%m%d}-"
    cur.execute(
        """
        SELECT order_number
        FROM service_orders
        WHERE order_number LIKE ?
        ORDER BY order_number DESC
        LIMIT 1
        """,
        (prefix + "%",),
    )
    row = cur.fetchone()
    if not row:
        serial = 1
    else:
        try:
            serial = int(str(row[0]).rsplit("-", 1)[1]) + 1
        except Exception:
            serial = 1
    return f"{prefix}{serial:06d}"


def _event(
    cur: sqlite3.Cursor,
    *,
    order_id: int,
    event_type: str,
    actor_id: int | str | None,
    actor_role: str = "",
    source_context: str = "",
    details: str = "",
) -> int:
    cur.execute(
        """
        INSERT INTO service_order_events (
            service_order_id, event_type, actor_id, actor_role,
            source_context, details, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(order_id),
            event_type,
            str(actor_id) if actor_id is not None else None,
            actor_role or None,
            source_context or None,
            details or None,
            now_db(),
        ),
    )
    return int(cur.lastrowid)


def _audit_order(
    conn: sqlite3.Connection,
    *,
    actor_id: int | str | None,
    action_type: str,
    order_id: int,
    details: str,
) -> None:
    if not audit_log:
        return
    audit_log(
        conn=conn,
        operator_id=str(actor_id) if actor_id is not None else "system",
        user_id=str(actor_id) if actor_id is not None else "system",
        actor_type="service_operator",
        action_type=action_type,
        table_name="service_orders",
        row_id=str(order_id),
        field_name="service_order",
        old_value="",
        new_value=details,
        source_context="service_orders_core",
        comment=details,
        commit=False,
    )


def _order_steps(cur: sqlite3.Cursor, order_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT *
        FROM service_order_steps
        WHERE service_order_id = ?
        ORDER BY sequence_no, id
        """,
        (int(order_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def _derive_status(steps: list[dict]) -> tuple[str, str, str]:
    required = [row for row in steps if int(row.get("is_required") or 0) == 1]
    waiting = [
        row for row in required
        if text(row.get("step_status")) not in {"CONFIRMED", "WAIVED"}
    ]

    payment = next(
        (row for row in required if text(row.get("step_code")) == "PAYMENT_CONFIRMED"),
        None,
    )
    payment_status = (
        "NOT_REQUIRED"
        if payment is None
        else (
            "CONFIRMED"
            if text(payment.get("step_status")) in {"CONFIRMED", "WAIVED"}
            else "AWAITING_PAYMENT"
        )
    )

    if not waiting:
        return "COMPLETED", payment_status, "COMPLETED"

    waiting_codes = {text(row.get("step_code")) for row in waiting}
    if "RESIDENT_REMOTE_RECEIVED" in waiting_codes:
        return "AWAITING_RESIDENT_ASSET", payment_status, "WAITING_FOR_REMOTE"
    if "STOCK_ASSET_RESERVED" in waiting_codes:
        return "AWAITING_STOCK", payment_status, "WAITING_FOR_STOCK"
    if "PAYMENT_CONFIRMED" in waiting_codes:
        return "AWAITING_PAYMENT", payment_status, "WAITING_FOR_PAYMENT"
    if "DIGITAL_ACCESS_ACTIVATED" in waiting_codes:
        return "IN_PROGRESS", payment_status, "WAITING_FOR_ACCESS_ACTIVATION"
    if "REMOTE_PROGRAMMED" in waiting_codes:
        return "IN_PROGRESS", payment_status, "WAITING_FOR_PROGRAMMING"
    if (
        "RESIDENT_REMOTE_RETURNED" in waiting_codes
        or "STOCK_ASSET_ISSUED" in waiting_codes
    ):
        return "READY_FOR_ISSUE", payment_status, "READY_FOR_HANDOVER"
    if "SERVICE_DELIVERED" in waiting_codes:
        return "IN_PROGRESS", payment_status, "WAITING_FOR_FULFILLMENT"
    return "IN_PROGRESS", payment_status, "IN_PROGRESS"


def recompute_order_status(
    cur: sqlite3.Cursor,
    order_id: int,
) -> dict:
    steps = _order_steps(cur, order_id)
    order_status, payment_status, fulfillment_status = _derive_status(steps)
    completed_at = now_db() if order_status == "COMPLETED" else None
    cur.execute(
        """
        UPDATE service_orders
        SET order_status = ?,
            payment_status = ?,
            fulfillment_status = ?,
            updated_at = ?,
            completed_at = COALESCE(completed_at, ?)
        WHERE id = ?
        """,
        (
            order_status,
            payment_status,
            fulfillment_status,
            now_db(),
            completed_at,
            int(order_id),
        ),
    )
    return get_service_order(cur, order_id)


def get_service_order(
    cur: sqlite3.Cursor,
    order_id: int,
) -> dict:
    order = _fetchone_dict(
        cur,
        "SELECT * FROM service_orders WHERE id = ?",
        (int(order_id),),
    )
    if not order:
        raise ValueError(f"Заказ не найден: {order_id}")
    order["steps"] = _order_steps(cur, int(order_id))
    return order


def create_service_order(
    *,
    resident_account_id: int | None,
    telegram_user_id: int | str | None,
    apartment_id: int | None,
    apartment_number: str,
    service_item_code: str,
    quantity: float = 1,
    resident_comment: str = "",
    actor_id: int | str | None = None,
    actor_role: str = "",
    source_context: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    if quantity <= 0:
        raise ValueError("Количество должно быть больше нуля.")

    owns = conn is None
    conn = conn or get_conn()
    try:
        ready, reason = schema_ready(conn)
        if not ready:
            raise RuntimeError(reason)

        cur = conn.cursor()
        item = _service_item(cur, service_item_code)
        workflow = get_service_workflow(cur, service_item_code)

        request_allowed = int(workflow.get("resident_request_enabled") or 0) == 1
        if actor_id is None and not request_allowed:
            raise PermissionError("Эта услуга пока недоступна для самостоятельной заявки жителя.")

        category = text(workflow.get("service_category")) or "GENERAL"
        _permission_or_raise(
            actor_id,
            "service_orders",
            "CREATE",
            "SERVICE_CATEGORY",
            category,
        )

        unit_price, currency = effective_price(cur, service_item_code)
        amount_due = (
            round(float(unit_price) * float(quantity), 2)
            if unit_price is not None
            else None
        )
        order_number = _next_order_number(cur)

        cur.execute(
            """
            INSERT INTO service_orders (
                order_number, resident_account_id, telegram_user_id,
                apartment_id, apartment_number,
                service_code, service_item_code,
                service_name_snapshot, workflow_profile_code,
                quantity, unit_price_snapshot, amount_due_snapshot, currency,
                order_status, payment_status, fulfillment_status,
                resident_comment, requested_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'REQUESTED', 'NOT_REQUIRED', 'NOT_STARTED',
                    ?, ?, ?)
            """,
            (
                order_number,
                resident_account_id,
                str(telegram_user_id) if telegram_user_id is not None else None,
                apartment_id,
                text(apartment_number),
                text(item.get("service_code")) or None,
                service_item_code,
                text(item.get("service_item_name")) or service_item_code,
                workflow["workflow_profile_code"],
                float(quantity),
                unit_price,
                amount_due,
                currency,
                text(resident_comment) or None,
                now_db(),
                now_db(),
            ),
        )
        order_id = int(cur.lastrowid)

        cur.execute(
            """
            SELECT step_code, step_name, step_kind, sequence_no, is_required
            FROM service_workflow_steps
            WHERE profile_code = ?
              AND is_active = 1
            ORDER BY sequence_no, id
            """,
            (workflow["workflow_profile_code"],),
        )
        steps = cur.fetchall()
        if not steps:
            raise RuntimeError(
                "В профиле нет шагов выполнения: "
                f"{workflow['workflow_profile_code']}"
            )

        for row in steps:
            cur.execute(
                """
                INSERT INTO service_order_steps (
                    service_order_id, step_code, step_name, step_kind,
                    sequence_no, is_required, step_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'WAITING', ?, ?)
                """,
                (
                    order_id,
                    row["step_code"],
                    row["step_name"],
                    row["step_kind"],
                    row["sequence_no"],
                    row["is_required"],
                    now_db(),
                    now_db(),
                ),
            )

        profile = text(workflow["workflow_profile_code"])
        if profile.startswith("REMOTE_"):
            cur.execute(
                """
                INSERT INTO remote_order_details (
                    service_order_id, remote_owner_mode,
                    reprogramming_required, inventory_asset_required,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    (
                        "RESIDENT_OWN"
                        if profile == "REMOTE_REPROGRAM_OWN"
                        else "OSBB_STOCK"
                    ),
                    1 if profile == "REMOTE_REPROGRAM_OWN" else 0,
                    1 if profile in {
                        "REMOTE_NEW_FROM_STOCK",
                        "REMOTE_REFURBISHED_FROM_STOCK",
                    } else 0,
                    now_db(),
                    now_db(),
                ),
            )

        _event(
            cur,
            order_id=order_id,
            event_type="ORDER_CREATED",
            actor_id=actor_id,
            actor_role=actor_role,
            source_context=source_context,
            details=(
                f"item={service_item_code}; profile={profile}; "
                f"quantity={quantity}; amount={amount_due}"
            ),
        )
        result = recompute_order_status(cur, order_id)
        _audit_order(
            conn,
            actor_id=actor_id,
            action_type="service_order_created",
            order_id=order_id,
            details=f"{order_number}; {service_item_code}; {result['order_status']}",
        )

        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def confirm_order_step(
    *,
    order_id: int,
    step_code: str,
    actor_id: int | str | None,
    actor_role: str = "",
    note: str = "",
    source_context: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ready, reason = schema_ready(conn)
        if not ready:
            raise RuntimeError(reason)

        cur = conn.cursor()
        order = get_service_order(cur, order_id)
        profile = _fetchone_dict(
            cur,
            """
            SELECT service_category
            FROM service_workflow_profiles
            WHERE profile_code = ?
            """,
            (order["workflow_profile_code"],),
        ) or {"service_category": "GENERAL"}

        _permission_or_raise(
            actor_id,
            "service_order_steps",
            "CONFIRM",
            "SERVICE_CATEGORY",
            text(profile.get("service_category")) or "GENERAL",
        )

        step = _fetchone_dict(
            cur,
            """
            SELECT *
            FROM service_order_steps
            WHERE service_order_id = ?
              AND step_code = ?
            """,
            (int(order_id), step_code),
        )
        if not step:
            raise ValueError(f"В заказе нет шага: {step_code}")
        if text(step.get("step_status")) in {"CONFIRMED", "WAIVED"}:
            return order

        cur.execute(
            """
            UPDATE service_order_steps
            SET step_status = 'CONFIRMED',
                confirmed_by = ?,
                confirmed_at = ?,
                note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(actor_id) if actor_id is not None else "system",
                now_db(),
                text(note) or None,
                now_db(),
                int(step["id"]),
            ),
        )
        _event(
            cur,
            order_id=order_id,
            event_type="STEP_CONFIRMED",
            actor_id=actor_id,
            actor_role=actor_role,
            source_context=source_context,
            details=f"{step_code}; {text(note)}",
        )
        result = recompute_order_status(cur, order_id)
        _audit_order(
            conn,
            actor_id=actor_id,
            action_type="service_order_step_confirmed",
            order_id=order_id,
            details=f"{step_code}; {result['order_status']}",
        )
        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def link_payment_to_order(
    *,
    order_id: int,
    payment_id: int,
    amount: float | None,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        order = get_service_order(cur, order_id)
        cur.execute(
            """
            INSERT INTO service_order_payment_links (
                service_order_id, payment_id, amount, linked_at, linked_by, note
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(service_order_id, payment_id) DO NOTHING
            """,
            (
                int(order_id), int(payment_id), amount, now_db(),
                str(actor_id) if actor_id is not None else "system",
                text(note) or None,
            ),
        )
        _event(
            cur,
            order_id=order_id,
            event_type="PAYMENT_LINKED",
            actor_id=actor_id,
            source_context="payment_link",
            details=f"payment_id={payment_id}; amount={amount}",
        )
        result = confirm_order_step(
            order_id=order_id,
            step_code="PAYMENT_CONFIRMED",
            actor_id=actor_id,
            note=note,
            source_context="payment_link",
            conn=conn,
        )
        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def link_charge_to_order(
    *,
    order_id: int,
    charge_id: int,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        get_service_order(cur, order_id)
        cur.execute(
            """
            INSERT INTO service_order_charge_links (
                service_order_id, charge_id, linked_at, linked_by, note
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_order_id, charge_id) DO NOTHING
            """,
            (
                int(order_id), int(charge_id), now_db(),
                str(actor_id) if actor_id is not None else "system",
                text(note) or None,
            ),
        )
        _event(
            cur,
            order_id=order_id,
            event_type="CHARGE_LINKED",
            actor_id=actor_id,
            source_context="charge_link",
            details=f"charge_id={charge_id}; {text(note)}",
        )
        if owns:
            conn.commit()
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def create_remote_asset(
    *,
    asset_number: str,
    ownership_type: str,
    inventory_status: str = "AVAILABLE",
    condition_status: str = "UNKNOWN",
    hardware_model: str = "",
    serial_number: str = "",
    programming_identifier: str = "",
    apartment_id: int | None = None,
    apartment_number: str = "",
    actor_id: int | str | None = None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        _permission_or_raise(
            actor_id,
            "remote_assets",
            "MOVE",
            "POST",
            "O",
        )
        cur.execute(
            """
            INSERT INTO remote_assets (
                asset_number, asset_type, ownership_type,
                inventory_status, condition_status,
                hardware_model, serial_number, programming_identifier,
                apartment_id, apartment_number,
                note, created_at, updated_at
            )
            VALUES (?, 'REMOTE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                text(asset_number), text(ownership_type),
                text(inventory_status), text(condition_status),
                text(hardware_model) or None,
                text(serial_number) or None,
                text(programming_identifier) or None,
                apartment_id,
                text(apartment_number) or None,
                text(note) or None,
                now_db(), now_db(),
            ),
        )
        asset_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO remote_asset_movements (
                remote_asset_id, movement_type, to_state,
                apartment_id, apartment_number, post_code,
                actor_id, note, created_at
            )
            VALUES (?, 'ASSET_REGISTERED', ?, ?, ?, 'O', ?, ?, ?)
            """,
            (
                asset_id, text(inventory_status), apartment_id,
                text(apartment_number) or None,
                str(actor_id) if actor_id is not None else "system",
                text(note) or None, now_db(),
            ),
        )
        if owns:
            conn.commit()
        return _fetchone_dict(cur, "SELECT * FROM remote_assets WHERE id = ?", (asset_id,)) or {}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def record_remote_movement(
    *,
    remote_asset_id: int,
    service_order_id: int,
    movement_type: str,
    to_state: str,
    actor_id: int | str | None,
    apartment_id: int | None = None,
    apartment_number: str = "",
    note: str = "",
    confirm_step_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        order = get_service_order(cur, service_order_id)
        _permission_or_raise(
            actor_id,
            "remote_assets",
            "MOVE",
            "POST",
            "O",
        )
        asset = _fetchone_dict(
            cur,
            "SELECT * FROM remote_assets WHERE id = ?",
            (int(remote_asset_id),),
        )
        if not asset:
            raise ValueError("Пульт/складская единица не найдена.")

        from_state = text(asset.get("inventory_status"))
        cur.execute(
            """
            UPDATE remote_assets
            SET inventory_status = ?,
                apartment_id = COALESCE(?, apartment_id),
                apartment_number = COALESCE(?, apartment_number),
                updated_at = ?
            WHERE id = ?
            """,
            (
                text(to_state),
                apartment_id,
                text(apartment_number) or None,
                now_db(),
                int(remote_asset_id),
            ),
        )
        cur.execute(
            """
            INSERT INTO remote_asset_movements (
                remote_asset_id, service_order_id, movement_type,
                from_state, to_state, apartment_id, apartment_number,
                post_code, actor_id, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'O', ?, ?, ?)
            """,
            (
                int(remote_asset_id), int(service_order_id), text(movement_type),
                from_state, text(to_state),
                apartment_id, text(apartment_number) or None,
                str(actor_id) if actor_id is not None else "system",
                text(note) or None, now_db(),
            ),
        )
        movement_id = int(cur.lastrowid)

        _event(
            cur,
            order_id=service_order_id,
            event_type="REMOTE_MOVEMENT",
            actor_id=actor_id,
            source_context="remote_asset",
            details=(
                f"asset={remote_asset_id}; {movement_type}; "
                f"{from_state}->{to_state}; movement={movement_id}"
            ),
        )

        if confirm_step_code:
            result = confirm_order_step(
                order_id=service_order_id,
                step_code=confirm_step_code,
                actor_id=actor_id,
                note=note,
                source_context="remote_asset",
                conn=conn,
            )
        else:
            result = get_service_order(cur, service_order_id)

        if owns:
            conn.commit()
        return {"movement_id": movement_id, "order": result}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def activate_access_credential(
    *,
    order_id: int,
    credential_value: str,
    actor_id: int | str | None,
    apartment_id: int | None,
    apartment_number: str,
    external_reference: str = "",
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        order = get_service_order(cur, order_id)
        _permission_or_raise(
            actor_id,
            "service_access_credentials",
            "ACTIVATE",
            "SERVICE_CATEGORY",
            "ACCESS",
        )
        cur.execute(
            """
            INSERT INTO service_access_credentials (
                service_order_id, credential_kind, credential_value,
                access_scope, credential_status, external_reference,
                apartment_id, apartment_number,
                activated_by, activated_at, note,
                created_at, updated_at
            )
            VALUES (?, 'PHONE', ?, 'BARRIER', 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(order_id), text(credential_value), text(external_reference) or None,
                apartment_id, text(apartment_number),
                str(actor_id) if actor_id is not None else "system",
                now_db(), text(note) or None, now_db(), now_db(),
            ),
        )
        credential_id = int(cur.lastrowid)
        _event(
            cur,
            order_id=order_id,
            event_type="DIGITAL_ACCESS_ACTIVATED",
            actor_id=actor_id,
            source_context="access_credentials",
            details=f"credential_id={credential_id}; phone={text(credential_value)}",
        )
        result = confirm_order_step(
            order_id=order_id,
            step_code="DIGITAL_ACCESS_ACTIVATED",
            actor_id=actor_id,
            note=note,
            source_context="access_credentials",
            conn=conn,
        )
        if owns:
            conn.commit()
        return {"credential_id": credential_id, "order": result}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def list_order_steps(
    order_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        return _order_steps(conn.cursor(), order_id)
    finally:
        if owns:
            conn.close()


def service_order_summary(
    order_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        order = get_service_order(cur, order_id)
        cur.execute(
            """
            SELECT payment_id, amount, linked_at, linked_by, note
            FROM service_order_payment_links
            WHERE service_order_id = ?
            ORDER BY id
            """,
            (int(order_id),),
        )
        order["payments"] = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT charge_id, linked_at, linked_by, note
            FROM service_order_charge_links
            WHERE service_order_id = ?
            ORDER BY id
            """,
            (int(order_id),),
        )
        order["charges"] = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT *
            FROM remote_order_details
            WHERE service_order_id = ?
            """,
            (int(order_id),),
        )
        detail = cur.fetchone()
        order["remote_details"] = dict(detail) if detail else None
        return order
    finally:
        if owns:
            conn.close()
