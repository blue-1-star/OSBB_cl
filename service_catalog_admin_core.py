"""
Управление актуальным каталогом услуг без удаления истории.

Слой service_catalog / service_items остаётся источником списка услуг.
Этот модуль добавляет:
- создание новой услуги и статьи;
- настройку профиля выполнения;
- версионирование цены;
- архивирование вместо удаления;
- возврат архивной услуги.

Ни одна услуга с историческими заказами, начислениями или платежами не удаляется.
Она только исчезает из актуального выбора после archive/retire.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT / "Bots"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from service_orders_core import (
    get_conn,
    now_db,
    schema_ready,
    table_columns,
    table_exists,
    text,
)

try:
    from access_control import has_permission
except Exception:
    has_permission = None


def _catalog_permission(actor_id: int | str | None) -> None:
    if actor_id is None or has_permission is None:
        return
    if not has_permission(
        actor_id,
        "service_catalog",
        "MANAGE",
        scope_type="ALL",
        scope_value="*",
    ):
        raise PermissionError("Нет права service_catalog.MANAGE.")


def _required_defaults(cur: sqlite3.Cursor, table: str) -> dict[str, Any]:
    cur.execute(f'PRAGMA table_info("{table}")')
    defaults: dict[str, Any] = {}
    for _, name, col_type, notnull, default_value, pk in cur.fetchall():
        if pk or not notnull or default_value is not None:
            continue
        upper = text(name).upper()
        typ = text(col_type).upper()
        if name == "service_group":
            defaults[name] = "GENERAL"
        elif name == "unit":
            defaults[name] = "service"
        elif name in {"service_name", "service_item_name", "name", "title"}:
            defaults[name] = "Без названия"
        elif name in {"service_code", "service_item_code", "code"}:
            defaults[name] = "UNSET"
        elif "ACTIVE" in upper:
            defaults[name] = 1
        elif "STATUS" in upper:
            defaults[name] = "active"
        elif any(token in upper for token in ("AMOUNT", "PRICE", "SUM", "BALANCE")):
            defaults[name] = 0
        elif "INT" in typ:
            defaults[name] = 0
        elif any(token in typ for token in ("REAL", "NUM", "DEC")):
            defaults[name] = 0
        elif upper.endswith("_AT") or "DATE" in upper or "TIME" in upper:
            defaults[name] = now_db()
        else:
            defaults[name] = ""
    return defaults


def _insert_dynamic(cur: sqlite3.Cursor, table: str, values: dict[str, Any]) -> int:
    cols = table_columns(cur, table)
    values = {**_required_defaults(cur, table), **values}
    actual = {key: value for key, value in values.items() if key in cols}
    if not actual:
        raise RuntimeError(f"В таблице {table} нет полей для вставки.")
    cur.execute(
        f"""
        INSERT INTO "{table}" ({', '.join(actual)})
        VALUES ({', '.join('?' for _ in actual)})
        """,
        tuple(actual.values()),
    )
    return int(cur.lastrowid)


def _update_dynamic(
    cur: sqlite3.Cursor,
    table: str,
    where_sql: str,
    where_params: tuple[Any, ...],
    values: dict[str, Any],
) -> None:
    cols = table_columns(cur, table)
    actual = {key: value for key, value in values.items() if key in cols}
    if not actual:
        return
    cur.execute(
        f"""
        UPDATE "{table}"
        SET {', '.join(f'{key} = ?' for key in actual)}
        WHERE {where_sql}
        """,
        tuple(actual.values()) + where_params,
    )


def _valid_code(value: str, label: str) -> str:
    raw = text(value).upper()
    if not raw:
        raise ValueError(f"Укажите {label}.")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(ch not in allowed for ch in raw):
        raise ValueError(
            f"{label} используйте латиницей: A-Z, цифры, _ или -."
        )
    return raw


def _price(amount: float | int | str | None) -> float | None:
    if amount is None or text(amount) == "":
        return None
    raw = float(str(amount).replace(",", "."))
    if raw < 0:
        raise ValueError("Цена не может быть отрицательной.")
    return round(raw, 2)


def _validate_date(value: str) -> str:
    try:
        return datetime.strptime(text(value), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Дата: ГГГГ-ММ-ДД, например 2026-07-01.") from exc


def list_service_offers(
    *,
    include_retired: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "service_items"):
            return []

        icols = table_columns(cur, "service_items")
        name = "i.service_item_name" if "service_item_name" in icols else "i.service_item_code"
        service_code = "i.service_code" if "service_code" in icols else "NULL"
        active = "COALESCE(i.is_active, 1)" if "is_active" in icols else "1"
        status = "COALESCE(i.status, 'active')" if "status" in icols else "'active'"
        amount = "i.amount_default" if "amount_default" in icols else "NULL"
        currency = "i.currency" if "currency" in icols else "'UAH'"

        where = "" if include_retired else f"WHERE {active} = 1 AND {status} = 'active'"
        cur.execute(
            f"""
            SELECT
                i.service_item_code,
                {service_code} AS service_code,
                {name} AS service_item_name,
                {amount} AS amount_default,
                {currency} AS currency,
                {active} AS is_active,
                {status} AS status,
                w.workflow_profile_code,
                w.resident_request_enabled,
                w.operator_create_enabled,
                w.is_active AS workflow_active,
                p.profile_name,
                p.service_category
            FROM service_items i
            LEFT JOIN service_item_workflows w
              ON w.service_item_code = i.service_item_code
            LEFT JOIN service_workflow_profiles p
              ON p.profile_code = w.workflow_profile_code
            {where}
            ORDER BY COALESCE(p.service_category, ''), {name}, i.service_item_code
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        if owns:
            conn.close()


def add_or_update_service_offer(
    *,
    service_code: str,
    service_name: str,
    service_group: str,
    service_type: str,
    category: str,
    service_item_code: str,
    service_item_name: str,
    workflow_profile_code: str,
    amount: float | int | str | None,
    effective_from: str,
    resident_request_enabled: bool,
    actor_id: int | str | None,
    description: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """
    Creates or updates a catalog service and one active service item.
    It never removes old price history. New price version starts at effective_from.
    """
    owns = conn is None
    conn = conn or get_conn()
    try:
        ready, reason = schema_ready(conn)
        if not ready:
            raise RuntimeError(reason)
        _catalog_permission(actor_id)
        cur = conn.cursor()

        service_code = _valid_code(service_code, "код услуги")
        service_item_code = _valid_code(service_item_code, "код статьи услуги")
        effective_from = _validate_date(effective_from)
        amount_value = _price(amount)

        cur.execute(
            "SELECT 1 FROM service_workflow_profiles WHERE profile_code = ? AND is_active = 1",
            (workflow_profile_code,),
        )
        if not cur.fetchone():
            raise ValueError(f"Неизвестный активный профиль: {workflow_profile_code}")

        if not table_exists(cur, "service_catalog") or not table_exists(cur, "service_items"):
            raise RuntimeError("Нужны таблицы service_catalog и service_items.")

        cur.execute(
            "SELECT 1 FROM service_catalog WHERE service_code = ?",
            (service_code,),
        )
        catalog_values = {
            "service_code": service_code,
            "service_name": text(service_name),
            "service_group": text(service_group) or "GENERAL",
            "unit": "service",
            "service_type": text(service_type) or "ONE_TIME",
            "category": text(category) or "GENERAL",
            "is_monthly": 1 if text(service_type).upper() == "MONTHLY" else 0,
            "is_fundraising": 1 if text(service_type).upper() == "FUNDRAISING" else 0,
            "is_commercial": 1 if text(service_type).upper() == "COMMERCIAL" else 0,
            "is_access_control": 1 if text(category).upper() == "ACCESS" else 0,
            "is_cash_collectable": 1,
            "is_active": 1,
            "comment": text(description) or None,
            "updated_at": now_db(),
        }
        if cur.fetchone():
            _update_dynamic(
                cur, "service_catalog", "service_code = ?", (service_code,), catalog_values
            )
        else:
            catalog_values["created_at"] = now_db()
            _insert_dynamic(cur, "service_catalog", catalog_values)

        cur.execute(
            "SELECT id FROM service_items WHERE service_item_code = ?",
            (service_item_code,),
        )
        item_values = {
            "service_item_code": service_item_code,
            "service_code": service_code,
            "service_item_name": text(service_item_name),
            "service_type": text(service_type) or "ONE_TIME",
            "period_code": None,
            "sequence_no": 1000,
            "amount_default": amount_value,
            "currency": "UAH",
            "date_from": effective_from,
            "date_to": None,
            "status": "active",
            "is_active": 1,
            "description": text(description) or None,
            "comment": "Управляется через каталог услуг.",
            "updated_at": now_db(),
        }
        if cur.fetchone():
            _update_dynamic(
                cur, "service_items", "service_item_code = ?",
                (service_item_code,), item_values
            )
        else:
            item_values["created_at"] = now_db()
            _insert_dynamic(cur, "service_items", item_values)

        cur.execute(
            """
            INSERT INTO service_item_workflows (
                service_item_code, workflow_profile_code,
                resident_request_enabled, operator_create_enabled,
                requires_charge, payment_timing,
                inventory_mode, resident_asset_mode, is_active,
                retired_at, retired_reason, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, 1, 'BEFORE_FULFILLMENT',
                    'NONE', 'NONE', 1, NULL, NULL, ?, ?)
            ON CONFLICT(service_item_code) DO UPDATE SET
                workflow_profile_code = excluded.workflow_profile_code,
                resident_request_enabled = excluded.resident_request_enabled,
                operator_create_enabled = 1,
                requires_charge = 1,
                is_active = 1,
                retired_at = NULL,
                retired_reason = NULL,
                updated_at = excluded.updated_at
            """,
            (
                service_item_code,
                workflow_profile_code,
                1 if resident_request_enabled else 0,
                now_db(),
                now_db(),
            ),
        )

        if amount_value is not None:
            set_service_price(
                service_item_code=service_item_code,
                amount=amount_value,
                effective_from=effective_from,
                actor_id=actor_id,
                note="Создание/обновление услуги",
                conn=conn,
            )

        if owns:
            conn.commit()
        return {
            "service_code": service_code,
            "service_item_code": service_item_code,
            "workflow_profile_code": workflow_profile_code,
            "amount": amount_value,
            "effective_from": effective_from,
        }
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def set_service_price(
    *,
    service_item_code: str,
    amount: float | int | str,
    effective_from: str,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> None:
    """
    Adds a new effective-dated price. It closes the preceding active version;
    it does not overwrite the old price record.
    """
    owns = conn is None
    conn = conn or get_conn()
    try:
        _catalog_permission(actor_id)
        cur = conn.cursor()
        amount_value = _price(amount)
        if amount_value is None:
            raise ValueError("Для цены укажите число.")
        effective_from = _validate_date(effective_from)

        cur.execute(
            """
            SELECT id, effective_from
            FROM service_price_versions
            WHERE service_item_code = ?
              AND is_active = 1
              AND effective_from < ?
              AND (effective_to IS NULL OR effective_to = '' OR effective_to >= ?)
            ORDER BY effective_from DESC, id DESC
            LIMIT 1
            """,
            (service_item_code, effective_from, effective_from),
        )
        previous = cur.fetchone()
        if previous:
            day_before = (
                datetime.strptime(effective_from, "%Y-%m-%d").date()
                - timedelta(days=1)
            ).strftime("%Y-%m-%d")
            cur.execute(
                """
                UPDATE service_price_versions
                SET effective_to = ?, updated_at = ?
                WHERE id = ?
                """,
                (day_before, now_db(), int(previous["id"])),
            )

        cur.execute(
            """
            INSERT INTO service_price_versions (
                service_item_code, amount, currency,
                effective_from, effective_to, is_active,
                created_by, note, created_at, updated_at
            )
            VALUES (?, ?, 'UAH', ?, NULL, 1, ?, ?, ?, ?)
            ON CONFLICT(service_item_code, effective_from) DO UPDATE SET
                amount = excluded.amount,
                currency = 'UAH',
                effective_to = NULL,
                is_active = 1,
                created_by = excluded.created_by,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                service_item_code, amount_value, effective_from,
                str(actor_id) if actor_id is not None else "system",
                text(note) or None,
                now_db(), now_db(),
            ),
        )
        _update_dynamic(
            cur,
            "service_items",
            "service_item_code = ?",
            (service_item_code,),
            {"amount_default": amount_value, "currency": "UAH", "updated_at": now_db()},
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


def retire_service_offer(
    *,
    service_item_code: str,
    actor_id: int | str | None,
    reason: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """
    Archive only. Historical orders, charges and payments stay untouched.
    """
    if not text(reason):
        raise ValueError("Для архивирования укажите причину.")

    owns = conn is None
    conn = conn or get_conn()
    try:
        _catalog_permission(actor_id)
        cur = conn.cursor()
        _service = _fetch_service_item_or_raise(cur, service_item_code)

        _update_dynamic(
            cur,
            "service_items",
            "service_item_code = ?",
            (service_item_code,),
            {
                "is_active": 0,
                "status": "archived",
                "date_to": today_iso(),
                "updated_at": now_db(),
            },
        )
        cur.execute(
            """
            UPDATE service_item_workflows
            SET is_active = 0,
                retired_at = ?,
                retired_reason = ?,
                updated_at = ?
            WHERE service_item_code = ?
            """,
            (now_db(), text(reason), now_db(), service_item_code),
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


def restore_service_offer(
    *,
    service_item_code: str,
    actor_id: int | str | None,
    conn: sqlite3.Connection | None = None,
) -> None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        _catalog_permission(actor_id)
        cur = conn.cursor()
        _fetch_service_item_or_raise(cur, service_item_code)
        _update_dynamic(
            cur,
            "service_items",
            "service_item_code = ?",
            (service_item_code,),
            {"is_active": 1, "status": "active", "date_to": None, "updated_at": now_db()},
        )
        cur.execute(
            """
            UPDATE service_item_workflows
            SET is_active = 1,
                retired_at = NULL,
                retired_reason = NULL,
                updated_at = ?
            WHERE service_item_code = ?
            """,
            (now_db(), service_item_code),
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


def _fetch_service_item_or_raise(cur: sqlite3.Cursor, service_item_code: str) -> dict:
    if not table_exists(cur, "service_items"):
        raise RuntimeError("Не найдена service_items.")
    cur.execute(
        "SELECT * FROM service_items WHERE service_item_code = ?",
        (service_item_code,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Статья услуги не найдена: {service_item_code}")
    return dict(row)


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")
