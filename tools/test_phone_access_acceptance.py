#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_phone_access_acceptance.py

PHONE ACCESS ACCEPTANCE TEST v1 - READ ONLY.

Small tests toward the big goal:
prove that the phone access service has the minimum production foundation.

This first version:
- no writes;
- no interest/order creation;
- no activation;
- checks schema, catalog, service items, workflows, access points;
- optionally checks one apartment with --apt.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PHONE_SERVICE_CODES = [
    "TEST_PHONE_ACCESS_CONNECT",
    "BARRIER_PHONE_CONNECT",
    "BARRIER_PHONE",
]

EXPECTED_ACCESS_POINTS = [
    "BARRIER_FAR_01",
    "BARRIER_NEAR_02",
]


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row["name"] for row in cur.fetchall()}


def add(lines: list[str], icon: str, label: str, details: str = "") -> None:
    lines.append(f"{icon} {label}" + (f" - {details}" if details else ""))


def scalar(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def check_required_tables(cur: sqlite3.Cursor, lines: list[str]) -> int:
    failures = 0
    required = [
        "service_catalog",
        "service_items",
        "service_item_workflows",
        "service_workflow_profiles",
        "service_workflow_steps",
        "service_orders",
        "service_order_steps",
        "service_interests",
        "phone_barrier_access_interests",
        "phone_barrier_access_points",
        "phone_barrier_access_order_points",
        "service_access_credentials",
    ]
    for table in required:
        if table_exists(cur, table):
            add(lines, "OK", f"table {table}")
        else:
            add(lines, "FAIL", f"table {table}", "missing")
            failures += 1
    return failures


def check_service_catalog(cur: sqlite3.Cursor, lines: list[str]) -> int:
    failures = 0
    if not table_exists(cur, "service_catalog"):
        return 1

    cols = table_columns(cur, "service_catalog")
    policy_cols = {
        "access_policy_enabled",
        "access_policy_scope",
        "access_policy_mode",
        "access_policy_message",
        "manual_review_required",
    }
    missing_policy = sorted(policy_cols - cols)
    if missing_policy:
        add(lines, "FAIL", "service_catalog policy columns", ", ".join(missing_policy))
        failures += 1
    else:
        add(lines, "OK", "service_catalog policy columns")

    for code in PHONE_SERVICE_CODES:
        cur.execute(
            """
            SELECT service_code, service_name, access_policy_enabled,
                   access_policy_scope, access_policy_mode
            FROM service_catalog
            WHERE service_code = ?
            """,
            (code,),
        )
        row = cur.fetchone()
        if not row:
            add(lines, "WARN", f"service_catalog {code}", "not found")
            continue
        add(
            lines,
            "OK",
            f"service_catalog {code}",
            f"mode={row['access_policy_mode']}, scope={row['access_policy_scope']}, enabled={row['access_policy_enabled']}",
        )
    return failures


def check_service_items_and_workflows(cur: sqlite3.Cursor, lines: list[str]) -> int:
    failures = 0
    if not table_exists(cur, "service_items"):
        return 1

    cols = table_columns(cur, "service_items")
    service_expr = (
        "service_code"
        if "service_code" in cols
        else ("base_service_code AS service_code" if "base_service_code" in cols else "NULL AS service_code")
    )
    name_expr = "service_item_name" if "service_item_name" in cols else "service_item_code AS service_item_name"

    cur.execute(
        f"""
        SELECT service_item_code, {service_expr}, {name_expr}
        FROM service_items
        WHERE service_item_code LIKE '%PHONE%'
           OR service_item_code LIKE '%BARRIER%'
           OR {name_expr.split(' AS ')[0]} LIKE '%телефон%'
           OR {name_expr.split(' AS ')[0]} LIKE '%Телефон%'
        ORDER BY service_item_code
        """
    )
    rows = cur.fetchall()
    if not rows:
        add(lines, "FAIL", "phone service_items", "none found")
        return failures + 1

    add(lines, "OK", "phone service_items found", str(len(rows)))
    for row in rows:
        item = text(row["service_item_code"])
        svc = text(row["service_code"])
        add(lines, "OK", f"service_item {item}", f"service_code={svc}")

        if table_exists(cur, "service_item_workflows"):
            cur.execute(
                """
                SELECT workflow_profile_code, resident_request_enabled, operator_create_enabled, is_active
                FROM service_item_workflows
                WHERE service_item_code = ?
                ORDER BY is_active DESC
                LIMIT 1
                """,
                (item,),
            )
            wf = cur.fetchone()
            if wf:
                add(
                    lines,
                    "OK",
                    f"workflow for {item}",
                    f"profile={wf['workflow_profile_code']}, resident={wf['resident_request_enabled']}, operator={wf['operator_create_enabled']}, active={wf['is_active']}",
                )
            else:
                add(lines, "WARN", f"workflow for {item}", "not configured")
    return failures


def check_access_points(cur: sqlite3.Cursor, lines: list[str]) -> int:
    failures = 0
    if not table_exists(cur, "phone_barrier_access_points"):
        return 1

    cols = table_columns(cur, "phone_barrier_access_points")
    if "access_point_code" not in cols:
        add(lines, "FAIL", "phone access point code column", "access_point_code missing")
        return failures + 1

    active_expr = "is_active" if "is_active" in cols else "1"
    cur.execute(
        f"""
        SELECT access_point_code AS code, {active_expr} AS active
        FROM phone_barrier_access_points
        ORDER BY access_point_code
        """
    )
    rows = cur.fetchall()
    if not rows:
        add(lines, "FAIL", "phone access points", "none found")
        return failures + 1

    found = {text(r["code"]) for r in rows}
    add(lines, "OK", "phone access points found", ", ".join(sorted(found)))

    for code in EXPECTED_ACCESS_POINTS:
        if code in found:
            add(lines, "OK", f"access point {code}")
        else:
            add(lines, "WARN", f"access point {code}", "not found")
    return failures


def check_apartment_profile(cur: sqlite3.Cursor, lines: list[str], apartment_number: str) -> int:
    failures = 0
    if not apartment_number:
        add(lines, "WARN", "apartment profile check", "skipped; use --apt")
        return failures

    if not table_exists(cur, "apartments"):
        add(lines, "FAIL", "apartments table", "missing")
        return failures + 1

    cols = table_columns(cur, "apartments")
    if "apartment_number" not in cols:
        add(lines, "FAIL", "apartments.apartment_number", "missing")
        return failures + 1

    cur.execute("SELECT * FROM apartments WHERE apartment_number = ? LIMIT 1", (apartment_number,))
    apt = cur.fetchone()
    if not apt:
        add(lines, "FAIL", f"apartment {apartment_number}", "not found")
        return failures + 1

    apt_keys = set(apt.keys())
    apt_id = apt["id"] if "id" in apt_keys else "-"
    add(lines, "OK", f"apartment {apartment_number}", f"id={apt_id}")

    if table_exists(cur, "resident_accounts"):
        ra_cols = table_columns(cur, "resident_accounts")
        if "apartment_id" in ra_cols and "id" in apt_keys:
            n = scalar(cur, "SELECT COUNT(*) FROM resident_accounts WHERE apartment_id = ?", (apt["id"],))
            add(lines, "OK" if int(n or 0) > 0 else "WARN", "resident account linked", str(n))
        elif "apartment_number" in ra_cols:
            n = scalar(cur, "SELECT COUNT(*) FROM resident_accounts WHERE apartment_number = ?", (apartment_number,))
            add(lines, "OK" if int(n or 0) > 0 else "WARN", "resident account linked", str(n))
        else:
            add(lines, "WARN", "resident account link check", "no known apartment column")
    else:
        add(lines, "WARN", "resident_accounts", "missing")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--apt", default="", help="Apartment number for profile/readiness checks.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    lines: list[str] = []
    failures = 0

    print("=" * 88)
    print("PHONE ACCESS ACCEPTANCE TEST v1 - READ ONLY")
    print("=" * 88)
    print("DB:", db_path)
    print("Apartment:", args.apt or "-")
    print("")

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()

        lines.append("SCHEMA")
        failures += check_required_tables(cur, lines)

        lines.append("")
        lines.append("SERVICE CATALOG / POLICY")
        failures += check_service_catalog(cur, lines)

        lines.append("")
        lines.append("SERVICE ITEMS / WORKFLOWS")
        failures += check_service_items_and_workflows(cur, lines)

        lines.append("")
        lines.append("ACCESS POINTS")
        failures += check_access_points(cur, lines)

        lines.append("")
        lines.append("APARTMENT PROFILE")
        failures += check_apartment_profile(cur, lines, text(args.apt))

        for line in lines:
            print(line)

        print("")
        if failures:
            print(f"RESULT: FAIL ({failures} blocking issue(s))")
            return 2

        print("RESULT: PASS")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
