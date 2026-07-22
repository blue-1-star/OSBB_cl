#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
service_access_policy.py

OSBB working module: service access policy v1.1.
Read-only policy checks for service_catalog v2.

Main API:
- ServiceAccessDenied
- check_service_allowed(conn, apartment_number, service_code)
- resolve_service_code(conn, service_item_code)
- check_service_item_allowed(conn, apartment_number, service_item_code)
- ensure_service_order_allowed(conn, apartment_number, service_item_code)
- result_to_short_text(result)
"""

from __future__ import annotations

import sqlite3
from typing import Any


class ServiceAccessDenied(PermissionError):
    """Операция запрещена политикой доступа к услугам."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(result.get("message") or "Service access denied")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def money(value: Any) -> str:
    n = float(value or 0)
    if abs(n - round(n)) < 0.00001:
        return str(int(round(n)))
    return f"{n:.2f}"


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    if not table_exists(cur, table_name):
        return set()
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}


def _allocation_amount_column(cols: set[str]) -> str | None:
    if "amount" in cols:
        return "amount"
    if "allocated_amount" in cols:
        return "allocated_amount"
    return None


def service_scope_match(scope: str, service_code: Any) -> bool:
    scope = text(scope).upper()
    service = text(service_code).upper()
    if scope in ("", "NONE"):
        return False
    if scope == "PARKING":
        return service in {"PARKING_DAY", "PARKING_NIGHT"} or service.startswith("PARKING")
    if scope == "ACCESS":
        return "BARRIER" in service or "PHONE" in service or "REMOTE" in service
    if scope == "ALL_CASH_COLLECTABLE":
        return True
    if scope == "MANUAL":
        return False
    return False


def _charge_amount_expr(charge_cols: set[str]) -> str:
    if "net_amount" in charge_cols and "amount" in charge_cols:
        return "COALESCE(c.net_amount, c.amount, 0)"
    if "amount" in charge_cols:
        return "COALESCE(c.amount, 0)"
    if "net_amount" in charge_cols:
        return "COALESCE(c.net_amount, 0)"
    return "0"


def load_service_policy(conn: sqlite3.Connection, service_code: str) -> dict[str, Any] | None:
    cur = conn.cursor()
    if not table_exists(cur, "service_catalog"):
        return None
    cur.execute("SELECT * FROM service_catalog WHERE service_code = ?", (text(service_code),))
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def resolve_service_code(conn: sqlite3.Connection, service_item_code: str) -> dict[str, Any]:
    """Resolve service_items.service_item_code to service_code. Read-only."""
    cur = conn.cursor()
    item_code = text(service_item_code)
    if not item_code:
        return {"found": False, "service_item_code": "", "service_code": "", "service_item_name": "", "message": "Пустой service_item_code."}
    if not table_exists(cur, "service_items"):
        return {"found": False, "service_item_code": item_code, "service_code": "", "service_item_name": "", "message": "Таблица service_items не найдена."}
    cols = table_columns(cur, "service_items")
    if "service_item_code" not in cols:
        return {"found": False, "service_item_code": item_code, "service_code": "", "service_item_name": "", "message": "В service_items отсутствует service_item_code."}

    service_expr = "service_code" if "service_code" in cols else ("base_service_code AS service_code" if "base_service_code" in cols else "NULL AS service_code")
    name_expr = "service_item_name" if "service_item_name" in cols else "service_item_code AS service_item_name"
    cur.execute(
        f"""
        SELECT service_item_code, {service_expr}, {name_expr}
        FROM service_items
        WHERE service_item_code = ?
        LIMIT 1
        """,
        (item_code,),
    )
    row = cur.fetchone()
    if not row:
        return {"found": False, "service_item_code": item_code, "service_code": "", "service_item_name": "", "message": f"Статья услуги не найдена: {item_code}"}
    data = _row_to_dict(row)
    return {
        "found": True,
        "service_item_code": text(data.get("service_item_code")) or item_code,
        "service_code": text(data.get("service_code")) or item_code,
        "service_item_name": text(data.get("service_item_name")) or item_code,
        "message": "",
    }


def find_blocking_debt_rows(conn: sqlite3.Connection, apartment_number: Any, scope: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    if not table_exists(cur, "charges"):
        return []
    charge_cols = table_columns(cur, "charges")
    if "apartment_number" not in charge_cols:
        return []

    service_expr = "COALESCE(c.service_code, '')" if "service_code" in charge_cols else "''"
    period_expr = "COALESCE(c.period_code, '')" if "period_code" in charge_cols else "''"

    status_filter = ""
    if "status" in charge_cols:
        status_filter = "AND COALESCE(c.status, '') <> 'cancelled'"
    elif "charge_status" in charge_cols:
        status_filter = "AND COALESCE(c.charge_status, '') <> 'cancelled'"

    allocation_join = ""
    allocation_select = "0 AS allocated_amount"
    if table_exists(cur, "payment_allocations"):
        alloc_cols = table_columns(cur, "payment_allocations")
        amount_col = _allocation_amount_column(alloc_cols)
        if amount_col and "charge_id" in alloc_cols:
            allocation_join = "LEFT JOIN payment_allocations pa ON pa.charge_id = c.id"
            allocation_select = f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'

    cur.execute(f"""
        SELECT c.id AS charge_id, {period_expr} AS period_code,
               {service_expr} AS service_code,
               {_charge_amount_expr(charge_cols)} AS amount,
               {allocation_select}
        FROM charges c
        {allocation_join}
        WHERE c.apartment_number = ?
        {status_filter}
        GROUP BY c.id
        ORDER BY {period_expr}, c.id
    """, (text(apartment_number),))

    rows: list[dict[str, Any]] = []
    for row in cur.fetchall():
        item = _row_to_dict(row)
        if not service_scope_match(scope, item.get("service_code")):
            continue
        amount = float(item.get("amount") or 0)
        allocated = float(item.get("allocated_amount") or 0)
        outstanding = max(0.0, amount - allocated)
        if outstanding > 0.01:
            item["outstanding_amount"] = round(outstanding, 2)
            rows.append(item)
    return rows


def default_policy_message(apartment_number: Any, service_code: str, decision: str, debt_total: float) -> str:
    apt = text(apartment_number) or "-"
    if decision == "BLOCK":
        return f"По квартире {apt} есть задолженность {money(debt_total)} грн. Услуга {service_code} временно недоступна до оплаты или сверки."
    if decision == "WARN":
        return f"По квартире {apt} есть задолженность {money(debt_total)} грн. Требуется ручное решение оператора."
    return ""


def _base_result(apartment_number: Any, service_code: str, service_name: str = "") -> dict[str, Any]:
    return {
        "allowed": True,
        "decision": "ALLOW",
        "apartment_number": text(apartment_number),
        "service_code": text(service_code),
        "service_item_code": "",
        "service_item_name": "",
        "service_name": service_name,
        "policy": {},
        "debt": {"total": 0.0, "services": [], "rows": []},
        "message": "",
    }


def check_service_allowed(conn: sqlite3.Connection, apartment_number: Any, service_code: str) -> dict[str, Any]:
    """Main read-only policy check by service_code."""
    svc = load_service_policy(conn, service_code)
    if not svc:
        result = _base_result(apartment_number, service_code)
        result.update({"allowed": False, "decision": "ERROR", "message": f"service_code не найден в service_catalog: {service_code}"})
        return result

    enabled = int(svc.get("access_policy_enabled") or 0)
    scope = text(svc.get("access_policy_scope") or "NONE").upper()
    mode = text(svc.get("access_policy_mode") or "NONE").upper()
    review = int(svc.get("manual_review_required") or 0)
    service_name = text(svc.get("service_name"))
    policy_message = text(svc.get("access_policy_message"))

    result = _base_result(apartment_number, service_code, service_name)
    result["policy"] = {"enabled": enabled, "scope": scope, "mode": mode or "NONE", "manual_review_required": review}

    if not enabled or mode in ("", "NONE") or scope in ("", "NONE"):
        result["message"] = "Политика доступа для услуги отключена."
        return result

    rows = find_blocking_debt_rows(conn, apartment_number, scope)
    total = round(sum(float(r.get("outstanding_amount") or 0) for r in rows), 2)
    debt_services = sorted({text(r.get("service_code")) for r in rows if text(r.get("service_code"))})
    result["debt"] = {"total": total, "services": debt_services, "rows": rows}

    if total <= 0.01:
        result["debt"] = {"total": 0.0, "services": [], "rows": []}
        result["message"] = "Блокирующей задолженности не найдено."
        return result

    if mode == "BLOCK":
        result.update({
            "allowed": False,
            "decision": "BLOCK",
            "message": policy_message or default_policy_message(apartment_number, service_code, "BLOCK", total),
        })
        return result

    if mode == "WARN":
        result.update({
            "allowed": True,
            "decision": "WARN",
            "message": policy_message or default_policy_message(apartment_number, service_code, "WARN", total),
        })
        return result

    result["message"] = f"Неизвестный режим политики {mode}; в v1 трактуем как ALLOW."
    return result


def check_service_item_allowed(conn: sqlite3.Connection, apartment_number: Any, service_item_code: str) -> dict[str, Any]:
    """Resolve service_item_code -> service_code and check policy."""
    resolved = resolve_service_code(conn, service_item_code)
    if not resolved.get("found"):
        result = _base_result(apartment_number, "")
        result.update({
            "allowed": False,
            "decision": "ERROR",
            "service_item_code": text(service_item_code),
            "message": resolved.get("message") or f"service_item_code не найден: {service_item_code}",
        })
        return result

    result = check_service_allowed(conn, apartment_number, text(resolved.get("service_code")))
    result["service_item_code"] = text(resolved.get("service_item_code"))
    result["service_item_name"] = text(resolved.get("service_item_name"))
    if not result.get("service_name"):
        result["service_name"] = text(resolved.get("service_item_name"))
    return result


def ensure_service_order_allowed(conn: sqlite3.Connection, apartment_number: Any, service_item_code: str) -> dict[str, Any]:
    """
    Ensure a service order may be created for apartment/service_item.

    BLOCK/ERROR -> raises ServiceAccessDenied(result)
    WARN/ALLOW -> returns result
    """
    result = check_service_item_allowed(conn, apartment_number, service_item_code)
    decision = text(result.get("decision")).upper()
    if decision in {"BLOCK", "ERROR"} or result.get("allowed") is False:
        raise ServiceAccessDenied(result)
    return result


def result_to_short_text(result: dict[str, Any]) -> str:
    service = (
        result.get("service_name")
        or result.get("service_item_name")
        or result.get("service_code")
        or result.get("service_item_code")
        or "-"
    )
    decision = result.get("decision")
    debt = result.get("debt") or {}
    total = float(debt.get("total") or 0)
    message = result.get("message") or ""

    if decision == "ALLOW":
        return f"✅ Услуга доступна: {service}"
    if decision == "WARN":
        return f"⚠️ {message}\n\nУслуга: {service}\nЗадолженность: {money(total)} грн."
    if decision == "BLOCK":
        return f"⛔ {message}\n\nУслуга: {service}\nЗадолженность: {money(total)} грн."
    return f"⚠️ Проверка услуги завершилась ошибкой: {message}"
