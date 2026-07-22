#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
check_service_policy_v1.py

READ ONLY проверочный модуль для service_catalog v2 policy.

Назначение:
  Проверить, как новая политика доступа к сервисам сработает для квартиры.

Читает:
  - service_catalog.access_policy_*
  - charges
  - payment_allocations

Ничего не пишет в БД.

Примеры:

  python .\OSBB\tools\check_service_policy_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apt 89 --service TEST_REMOTE_NEW

  python .\OSBB\tools\check_service_policy_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apt 89 --all

  python .\OSBB\tools\check_service_policy_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apt 89 --service TEST_REMOTE_REPROGRAM_OWN
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SERVICES = [
    "TEST_REMOTE_NEW",
    "TEST_REMOTE_REFURBISHED",
    "TEST_REMOTE_REPROGRAM_OWN",
    "TEST_PHONE_ACCESS_CONNECT",
    "BARRIER_PHONE_CONNECT",
    "BARRIER_PHONE",
]


@dataclass
class PolicyResult:
    apartment_number: str
    service_code: str
    service_name: str
    allowed: bool
    decision: str
    policy_enabled: int
    policy_scope: str
    policy_mode: str
    manual_review_required: int
    debt_total: float
    debt_services: str
    message: str


def text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def money(v: Any) -> str:
    n = float(v or 0)
    if abs(n - round(n)) < 0.00001:
        return str(int(round(n)))
    return f"{n:.2f}"


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}


def allocation_amount_column(cols: set[str]) -> str | None:
    if "amount" in cols:
        return "amount"
    if "allocated_amount" in cols:
        return "allocated_amount"
    return None


def service_scope_match(scope: str, service_code: str) -> bool:
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


def charge_amount_expr(charge_cols: set[str]) -> str:
    if "net_amount" in charge_cols and "amount" in charge_cols:
        return "COALESCE(c.net_amount, c.amount, 0)"
    if "amount" in charge_cols:
        return "COALESCE(c.amount, 0)"
    if "net_amount" in charge_cols:
        return "COALESCE(c.net_amount, 0)"
    return "0"


def debt_rows(cur: sqlite3.Cursor, apartment_number: str, scope: str) -> list[dict[str, Any]]:
    if not table_exists(cur, "charges"):
        return []

    charge_cols = columns(cur, "charges")
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
        alloc_cols = columns(cur, "payment_allocations")
        amount_col = allocation_amount_column(alloc_cols)
        if amount_col and "charge_id" in alloc_cols:
            allocation_join = "LEFT JOIN payment_allocations pa ON pa.charge_id = c.id"
            allocation_select = f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'

    cur.execute(f"""
        SELECT
            c.id AS charge_id,
            {period_expr} AS period_code,
            {service_expr} AS service_code,
            {charge_amount_expr(charge_cols)} AS amount,
            {allocation_select}
        FROM charges c
        {allocation_join}
        WHERE c.apartment_number = ?
        {status_filter}
        GROUP BY c.id
        ORDER BY {period_expr}, c.id
    """, (text(apartment_number),))

    rows = []
    for row in cur.fetchall():
        item = dict(row)
        if not service_scope_match(scope, item.get("service_code")):
            continue
        amount = float(item.get("amount") or 0)
        allocated = float(item.get("allocated_amount") or 0)
        rest = max(0.0, amount - allocated)
        if rest > 0.01:
            item["outstanding_amount"] = rest
            rows.append(item)
    return rows


def load_service(cur: sqlite3.Cursor, service_code: str) -> dict[str, Any] | None:
    if not table_exists(cur, "service_catalog"):
        return None
    cur.execute("SELECT * FROM service_catalog WHERE service_code = ?", (service_code,))
    row = cur.fetchone()
    return dict(row) if row else None


def check_service_allowed(cur: sqlite3.Cursor, apartment_number: str, service_code: str) -> PolicyResult:
    svc = load_service(cur, service_code)
    if not svc:
        return PolicyResult(
            apartment_number=text(apartment_number),
            service_code=service_code,
            service_name="",
            allowed=False,
            decision="ERROR",
            policy_enabled=0,
            policy_scope="",
            policy_mode="",
            manual_review_required=0,
            debt_total=0.0,
            debt_services="",
            message=f"service_code не найден в service_catalog: {service_code}",
        )

    enabled = int(svc.get("access_policy_enabled") or 0)
    scope = text(svc.get("access_policy_scope") or "NONE")
    mode = text(svc.get("access_policy_mode") or "NONE").upper()
    review = int(svc.get("manual_review_required") or 0)
    service_name = text(svc.get("service_name"))
    policy_message = text(svc.get("access_policy_message"))

    if not enabled or mode in ("", "NONE") or scope in ("", "NONE"):
        return PolicyResult(
            apartment_number=text(apartment_number),
            service_code=service_code,
            service_name=service_name,
            allowed=True,
            decision="ALLOW",
            policy_enabled=enabled,
            policy_scope=scope,
            policy_mode=mode or "NONE",
            manual_review_required=review,
            debt_total=0.0,
            debt_services="",
            message="Политика доступа для услуги отключена.",
        )

    rows = debt_rows(cur, apartment_number, scope)
    total = round(sum(float(r.get("outstanding_amount") or 0) for r in rows), 2)
    services = ", ".join(sorted({text(r.get("service_code")) for r in rows if text(r.get("service_code"))}))

    if total <= 0.01:
        return PolicyResult(
            apartment_number=text(apartment_number),
            service_code=service_code,
            service_name=service_name,
            allowed=True,
            decision="ALLOW",
            policy_enabled=enabled,
            policy_scope=scope,
            policy_mode=mode,
            manual_review_required=review,
            debt_total=0.0,
            debt_services="",
            message="Блокирующей задолженности не найдено.",
        )

    if mode == "BLOCK":
        msg = policy_message or (
            f"По квартире {apartment_number} есть задолженность {money(total)} грн. "
            f"Услуга {service_code} временно недоступна."
        )
        return PolicyResult(
            apartment_number=text(apartment_number),
            service_code=service_code,
            service_name=service_name,
            allowed=False,
            decision="BLOCK",
            policy_enabled=enabled,
            policy_scope=scope,
            policy_mode=mode,
            manual_review_required=review,
            debt_total=total,
            debt_services=services,
            message=msg,
        )

    if mode == "WARN":
        msg = policy_message or (
            f"По квартире {apartment_number} есть задолженность {money(total)} грн. "
            "Требуется ручное решение оператора."
        )
        return PolicyResult(
            apartment_number=text(apartment_number),
            service_code=service_code,
            service_name=service_name,
            allowed=True,
            decision="WARN",
            policy_enabled=enabled,
            policy_scope=scope,
            policy_mode=mode,
            manual_review_required=review,
            debt_total=total,
            debt_services=services,
            message=msg,
        )

    return PolicyResult(
        apartment_number=text(apartment_number),
        service_code=service_code,
        service_name=service_name,
        allowed=True,
        decision="ALLOW",
        policy_enabled=enabled,
        policy_scope=scope,
        policy_mode=mode,
        manual_review_required=review,
        debt_total=total,
        debt_services=services,
        message=f"Неизвестный режим политики {mode}; в v1 трактуем как ALLOW.",
    )


def print_result(r: PolicyResult) -> None:
    print("-" * 88)
    print(f"Apartment : {r.apartment_number}")
    print(f"Service   : {r.service_code} | {r.service_name or '-'}")
    print(f"Decision  : {r.decision}")
    print(f"Allowed   : {r.allowed}")
    print(f"Policy    : enabled={r.policy_enabled}, scope={r.policy_scope}, mode={r.policy_mode}, review={r.manual_review_required}")
    print(f"Debt      : {money(r.debt_total)} UAH | services={r.debt_services or '-'}")
    print(f"Message   : {r.message}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--apt", required=True, help="Apartment number.")
    ap.add_argument("--service", default="", help="Single service_code to check.")
    ap.add_argument("--all", action="store_true", help="Check default policy services.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    services = []
    if args.all:
        services = DEFAULT_SERVICES
    elif args.service:
        services = [args.service]
    else:
        raise SystemExit("Use --service SERVICE_CODE or --all")

    print("=" * 88)
    print("OSBB service policy check v1 - READ ONLY")
    print("=" * 88)
    print("DB:", db_path)
    print("Apartment:", args.apt)
    print("Services:", ", ".join(services))
    print("")

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()
        for svc in services:
            print_result(check_service_allowed(cur, args.apt, svc))
        print("")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
