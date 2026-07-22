#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_phone_access_legacy.py

OSBB phone access legacy inspector - READ ONLY.
Reality-first tool for the existing phone access implementation.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


KEY_TABLES = [
    "phone_access_requests",
    "phone_access_policies",
    "phone_access_policy_versions",
    "phone_access_tariffs",
    "phone_access_payments",
    "phone_access_request_events",
    "phone_access_credentials",
    "service_access_credentials",
    "phone_barrier_access_points",
    "phone_barrier_access_interests",
    "phone_barrier_access_interest_points",
    "phone_barrier_access_order_points",
    "service_interests",
    "service_orders",
    "service_order_steps",
    "service_items",
    "service_catalog",
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


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    if not table_exists(cur, table):
        return []
    cur.execute(f'PRAGMA table_info("{table}")')
    return [row["name"] for row in cur.fetchall()]


def count_rows(cur: sqlite3.Cursor, table: str) -> int:
    if not table_exists(cur, table):
        return 0
    try:
        return int(cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)
    except Exception:
        return 0


def distinct_values(cur: sqlite3.Cursor, table: str, column: str, limit: int = 50) -> list[tuple[Any, int]]:
    if not table_exists(cur, table):
        return []
    cols = table_columns(cur, table)
    if column not in cols:
        return []
    cur.execute(
        f"""
        SELECT "{column}" AS value, COUNT(*) AS n
        FROM "{table}"
        GROUP BY "{column}"
        ORDER BY n DESC, value
        LIMIT ?
        """,
        (limit,),
    )
    return [(row["value"], row["n"]) for row in cur.fetchall()]


def print_section(title: str) -> None:
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print(" - none")
        return
    for row in rows:
        print(" -", dict(row))


def quoted_cols(cols: list[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def inspect_tables(cur: sqlite3.Cursor) -> None:
    print_section("PHONE/ACCESS RELATED TABLES")
    for table in KEY_TABLES:
        if table_exists(cur, table):
            print(f"OK   {table:<40} rows={count_rows(cur, table)}")
        else:
            print(f"MISS {table}")

    print_section("PHONE/ACCESS TABLE COLUMNS")
    for table in KEY_TABLES:
        if not table_exists(cur, table):
            continue
        cols = table_columns(cur, table)
        print("")
        print(f"{table} ({len(cols)} columns)")
        for col in cols:
            print(f" - {col}")


def inspect_statuses(cur: sqlite3.Cursor) -> None:
    print_section("ACTUAL STATUSES / MODES")
    candidates = {
        "phone_access_requests": [
            "request_status",
            "parking_debt_check_mode",
            "registered_at",
            "first_charge_period",
        ],
        "service_access_credentials": [
            "status",
            "credential_status",
            "credential_type",
        ],
        "phone_barrier_access_order_points": [
            "status",
        ],
        "service_orders": [
            "order_status",
            "workflow_status",
            "service_item_code",
        ],
        "service_interests": [
            "status",
            "service_item_code",
        ],
    }

    for table, columns in candidates.items():
        if not table_exists(cur, table):
            continue
        print("")
        print(table)
        for col in columns:
            values = distinct_values(cur, table, col)
            if not values:
                continue
            print(f"  {col}:")
            for value, n in values:
                print(f"    - {value!r}: {n}")


def inspect_recent_phone_requests(cur: sqlite3.Cursor, limit: int) -> None:
    print_section("RECENT phone_access_requests")
    if not table_exists(cur, "phone_access_requests"):
        print("phone_access_requests missing")
        return

    cols = table_columns(cur, "phone_access_requests")
    wanted = [
        "id",
        "request_number",
        "interest_id",
        "service_order_id",
        "resident_account_id",
        "telegram_user_id",
        "apartment_id",
        "apartment_number",
        "phone_normalized",
        "request_status",
        "parking_debt_check_mode",
        "policy_version_id",
        "quoted_at",
        "registered_at",
        "first_charge_period",
        "paid_at",
        "created_at",
        "updated_at",
    ]
    select_cols = [c for c in wanted if c in cols]
    if not select_cols:
        print("No known columns to show")
        return
    order_col = "id" if "id" in cols else select_cols[0]
    cur.execute(
        f"""
        SELECT {quoted_cols(select_cols)}
        FROM phone_access_requests
        ORDER BY "{order_col}" DESC
        LIMIT ?
        """,
        (limit,),
    )
    print_rows(cur.fetchall())


def inspect_links(cur: sqlite3.Cursor, limit: int) -> None:
    print_section("LINKS: phone_access_requests -> service_orders")
    if not (table_exists(cur, "phone_access_requests") and table_exists(cur, "service_orders")):
        print("Required tables missing")
        return

    pr_cols = table_columns(cur, "phone_access_requests")
    so_cols = table_columns(cur, "service_orders")
    if "service_order_id" not in pr_cols or "id" not in so_cols:
        print("Link columns missing")
        return

    service_item_expr = 'so."service_item_code"' if "service_item_code" in so_cols else "NULL"
    status_expr = 'so."order_status"' if "order_status" in so_cols else "NULL"
    apt_expr = 'so."apartment_number"' if "apartment_number" in so_cols else "NULL"

    cur.execute(
        f"""
        SELECT
            pr.id AS phone_request_id,
            pr.request_number,
            pr.request_status,
            pr.apartment_number AS request_apartment,
            pr.phone_normalized,
            pr.service_order_id,
            {service_item_expr} AS order_service_item_code,
            {status_expr} AS order_status,
            {apt_expr} AS order_apartment
        FROM phone_access_requests pr
        LEFT JOIN service_orders so ON so.id = pr.service_order_id
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    print_rows(cur.fetchall())

    print_section("LINKS: phone_access_requests -> credentials")
    if not table_exists(cur, "service_access_credentials"):
        print("service_access_credentials missing")
        return

    cred_cols = table_columns(cur, "service_access_credentials")
    if "service_order_id" in cred_cols and "service_order_id" in pr_cols:
        cur.execute(
            """
            SELECT pr.id AS phone_request_id, pr.request_number, pr.request_status,
                   sac.*
            FROM phone_access_requests pr
            LEFT JOIN service_access_credentials sac ON sac.service_order_id = pr.service_order_id
            ORDER BY pr.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        print_rows(cur.fetchall())
    else:
        print("No obvious credential link column found in service_access_credentials")


def inspect_access_points(cur: sqlite3.Cursor, limit: int) -> None:
    print_section("ACCESS POINTS / BARRIER MODEL")
    tables = [
        "phone_barrier_access_points",
        "phone_barrier_access_interest_points",
        "phone_barrier_access_order_points",
    ]
    for table in tables:
        print("")
        print(table)
        if not table_exists(cur, table):
            print(" - missing")
            continue
        n = count_rows(cur, table)
        print(f" - rows: {n}")
        cols = table_columns(cur, table)
        if not cols:
            print(" - no columns")
            continue
        select_cols = cols[:20]
        order_col = "id" if "id" in cols else select_cols[0]
        cur.execute(
            f"""
            SELECT {quoted_cols(select_cols)}
            FROM "{table}"
            ORDER BY "{order_col}" DESC
            LIMIT ?
            """,
            (limit,),
        )
        print_rows(cur.fetchall())


def inspect_schema_gaps(cur: sqlite3.Cursor) -> None:
    print_section("SCHEMA GAPS / NOTES")
    notes: list[str] = []

    if table_exists(cur, "phone_access_requests"):
        req_cols = set(table_columns(cur, "phone_access_requests"))
        for col in ["request_status", "phone_normalized", "service_order_id", "apartment_number"]:
            notes.append(("OK " if col in req_cols else "WARN ") + f"phone_access_requests.{col}")
    else:
        notes.append("FAIL phone_access_requests missing")

    if table_exists(cur, "phone_barrier_access_points"):
        n = count_rows(cur, "phone_barrier_access_points")
        notes.append(f"OK phone_barrier_access_points has {n} row(s)" if n else "WARN phone_barrier_access_points exists but is empty")
    else:
        notes.append("WARN phone_barrier_access_points missing")

    if table_exists(cur, "service_interests") and count_rows(cur, "service_interests") == 0:
        notes.append("NOTE service_interests exists but phone legacy currently does not use it in this DB")

    if table_exists(cur, "phone_access_requests") and count_rows(cur, "phone_access_requests") > 0:
        notes.append("NOTE phone_access_requests is the current factual legacy phone request table")

    for note in notes:
        print(" -", note)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--limit", type=int, default=5, help="Rows to show from each table.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 100)
    print("OSBB phone access legacy inspector - READ ONLY")
    print("=" * 100)
    print("Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("DB:", db_path)
    print("Limit:", args.limit)

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()
        inspect_tables(cur)
        inspect_statuses(cur)
        inspect_recent_phone_requests(cur, args.limit)
        inspect_links(cur, args.limit)
        inspect_access_points(cur, args.limit)
        inspect_schema_gaps(cur)
        print("")
        print("READ ONLY COMPLETED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
