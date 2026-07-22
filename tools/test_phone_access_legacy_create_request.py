#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
test_phone_access_legacy_create_request.py

OSBB phone access create-request acceptance test v2.

Goal:
  Fast practical check for bot launch:
  "Can the current phone-access code create a resident phone-access request?"

Important:
  This is NOT a legacy-preservation test.
  It checks the currently coded fast path that is closest to launch.

Safety:
  - Source DB is never modified.
  - Test copies source DB to acceptance_tmp.
  - All writes happen only in the temp DB.
  - Temp DB is deleted unless --keep-temp is passed.

Current factual write path:
  service_order_interests
  phone_access_requests
  phone_access_request_points

PowerShell:

  python .\OSBB\tools\test_phone_access_legacy_create_request.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apt 89 --apartment-id 87

Optional:
  --keep-temp
  --phone +380501112233
  --service-item-code TEST_PHONE_ACCESS_CONNECT
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
for _p in (str(ROOT), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def count_rows(cur: sqlite3.Cursor, table: str) -> int:
    if not table_exists(cur, table):
        return 0
    return int(cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    return {r["name"] for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()}


def latest_rows(cur: sqlite3.Cursor, table: str, limit: int = 3) -> list[dict[str, Any]]:
    if not table_exists(cur, table):
        return []
    cols = list(table_columns(cur, table))
    if not cols:
        return []
    order_col = "id" if "id" in cols else sorted(cols)[0]
    rows = cur.execute(
        f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def make_temp_db(src: Path) -> Path:
    out_dir = src.parent / "acceptance_tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}_phone_access_create_request_tmp_{datetime.now():%Y-%m-%d_%H-%M-%S}{src.suffix}"
    shutil.copy2(src, dst)
    return dst


def set_runtime_db_env(tmp_db: Path) -> None:
    """
    Best-effort override for modules that read DB path from environment/config.
    Passing conn should be enough, but these env vars make the test more robust
    for the current starter/config ecosystem.
    """
    value = str(tmp_db)
    for key in [
        "OSBB_DB_PATH",
        "OSBB_SQLITE_DB",
        "OSBB_DATABASE_PATH",
        "DB_PATH",
        "DATABASE_PATH",
        "LIVE_SERVICES_DB",
        "OSBB_LIVE_SERVICES_DB",
        "OSBB_SANDBOX_DB",
    ]:
        os.environ[key] = value


def row_by_id(cur: sqlite3.Cursor, table: str, row_id: Any) -> dict[str, Any] | None:
    if row_id is None or not table_exists(cur, table):
        return None
    cols = table_columns(cur, table)
    if "id" not in cols:
        return None
    row = cur.execute(f'SELECT * FROM "{table}" WHERE id = ?', (int(row_id),)).fetchone()
    return dict(row) if row else None


def rows_by_fk(cur: sqlite3.Cursor, table: str, fk_col: str, fk_value: Any) -> list[dict[str, Any]]:
    if fk_value is None or not table_exists(cur, table):
        return []
    cols = table_columns(cur, table)
    if fk_col not in cols:
        return []
    rows = cur.execute(
        f'SELECT * FROM "{table}" WHERE "{fk_col}" = ? ORDER BY id',
        (int(fk_value),),
    ).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Source SQLite DB path. It will not be modified.")
    ap.add_argument("--apt", required=True, help="Apartment number.")
    ap.add_argument("--apartment-id", type=int, required=True, help="Apartment ID.")
    ap.add_argument("--resident-account-id", type=int, default=1, help="Resident account ID for test request.")
    ap.add_argument("--telegram-user-id", default="999999", help="Telegram user ID for test request.")
    ap.add_argument("--phone", default="+380501112233", help="Phone number for test request.")
    ap.add_argument("--service-item-code", default="TEST_PHONE_ACCESS_CONNECT", help="Phone access service item code.")
    ap.add_argument("--keep-temp", action="store_true", help="Keep temporary DB copy.")
    args = ap.parse_args()

    src_db = Path(args.db)
    if not src_db.exists():
        raise SystemExit(f"DB not found: {src_db}")

    print("=" * 88)
    print("PHONE ACCESS CREATE REQUEST TEST v2")
    print("=" * 88)
    print("Source DB:", src_db)
    print("Apartment:", args.apt, "apartment_id:", args.apartment_id)
    print("Service item:", args.service_item_code)
    print("Phone:", args.phone)
    print("")

    tmp_db = make_temp_db(src_db)
    set_runtime_db_env(tmp_db)

    print("Temp DB:", tmp_db)
    print("Source DB will not be modified.")
    print("")

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row

    try:
        from phone_barrier_access_service import create_phone_barrier_access_interest
    except Exception as exc:
        print("FAIL import phone_barrier_access_service:", type(exc).__name__, exc)
        conn.close()
        if not args.keep_temp:
            tmp_db.unlink(missing_ok=True)
        return 2

    ok = True

    try:
        cur = conn.cursor()

        tracked_tables = [
            "service_order_interests",
            "phone_access_requests",
            "phone_access_request_points",
            "service_orders",
            "service_access_credentials",
        ]

        before = {t: count_rows(cur, t) for t in tracked_tables}

        print("Before:")
        for k, v in before.items():
            print(f" - {k}: {v}")
        print("")

        try:
            result = create_phone_barrier_access_interest(
                resident_account_id=int(args.resident_account_id),
                telegram_user_id=args.telegram_user_id,
                apartment_id=int(args.apartment_id),
                apartment_number=text(args.apt),
                service_item_code=text(args.service_item_code),
                phone=text(args.phone),
                access_point_codes=["BARRIER_FAR_01", "BARRIER_NEAR_02"],
                parking_debt_check_mode="MANUAL_REVIEW",
                parking_debt_check_note="PHONE_ACCESS_CREATE_REQUEST_ACCEPTANCE_V2",
                conn=conn,
            )
        except Exception as exc:
            print("FAIL create_phone_barrier_access_interest:")
            print(type(exc).__name__ + ":", exc)
            print("")
            print("Latest service_order_interests:")
            for row in latest_rows(cur, "service_order_interests", 5):
                print(" -", row)
            print("Latest phone_access_requests:")
            for row in latest_rows(cur, "phone_access_requests", 5):
                print(" -", row)
            return 2

        # Commit explicitly because we supplied conn, so the service function will not own/commit it.
        conn.commit()

        # Re-open persisted temp DB view.
        conn.close()
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        after = {t: count_rows(cur, t) for t in tracked_tables}

        print("Returned top-level result:")
        compact = {
            "id": result.get("id"),
            "interest_number": result.get("interest_number"),
            "apartment_number": result.get("apartment_number"),
            "service_item_code": result.get("service_item_code"),
            "amount_due_snapshot": result.get("amount_due_snapshot"),
            "interest_status": result.get("interest_status"),
        }
        print(compact)
        print("")

        req_result = result.get("phone_access_request") or {}
        print("Returned phone_access_request:")
        print({
            "id": req_result.get("id"),
            "request_number": req_result.get("request_number"),
            "apartment_number": req_result.get("apartment_number"),
            "phone_normalized": req_result.get("phone_normalized"),
            "request_status": req_result.get("request_status"),
            "points": len(req_result.get("points") or []),
            "connection_total": req_result.get("connection_total"),
            "monthly_total": req_result.get("monthly_total"),
        })
        print("")

        print("After:")
        for k, v in after.items():
            print(f" - {k}: {v} (delta {after[k] - before[k]})")
        print("")

        expected_deltas = {
            "service_order_interests": 1,
            "phone_access_requests": 1,
            "phone_access_request_points": 2,
            "service_orders": 0,
            "service_access_credentials": 0,
        }

        for table, expected_delta in expected_deltas.items():
            got = after[table] - before[table]
            if got == expected_delta:
                print(f"OK {table} delta {got}")
            else:
                print(f"FAIL {table} delta expected {expected_delta}, got {got}")
                ok = False

        print("")

        interest_id = result.get("id")
        request_id = req_result.get("id")

        interest_row = row_by_id(cur, "service_order_interests", interest_id)
        request_row = row_by_id(cur, "phone_access_requests", request_id)
        point_rows = rows_by_fk(cur, "phone_access_request_points", "request_id", request_id)

        print("Persisted interest row:")
        print(interest_row or " - missing")
        print("")
        print("Persisted phone request row:")
        print(request_row or " - missing")
        print("")
        print("Persisted phone request points:")
        if point_rows:
            for row in point_rows:
                print(" -", row)
        else:
            print(" - missing")
        print("")

        if not interest_row:
            print("FAIL service_order_interests row not found by returned id")
            ok = False
        else:
            checks = [
                ("apartment_number", text(args.apt)),
                ("service_item_code", text(args.service_item_code)),
            ]
            for col, expected in checks:
                got = text(interest_row.get(col))
                if got == expected:
                    print(f"OK interest.{col} = {got}")
                else:
                    print(f"FAIL interest.{col}: expected {expected!r}, got {got!r}")
                    ok = False

        if not request_row:
            print("FAIL phone_access_requests row not found by returned id")
            ok = False
        else:
            checks = [
                ("apartment_number", text(args.apt)),
                ("phone_normalized", text(args.phone)),
                ("parking_debt_check_mode", "MANUAL_REVIEW"),
            ]
            for col, expected in checks:
                got = text(request_row.get(col))
                if got == expected:
                    print(f"OK request.{col} = {got}")
                else:
                    print(f"FAIL request.{col}: expected {expected!r}, got {got!r}")
                    ok = False

            if text(request_row.get("request_number")):
                print("OK request.request_number present:", request_row.get("request_number"))
            else:
                print("FAIL request.request_number missing")
                ok = False

            if text(request_row.get("request_status")):
                print("OK request.request_status present:", request_row.get("request_status"))
            else:
                print("FAIL request.request_status missing")
                ok = False

        point_codes = sorted(text(row.get("access_point_code")) for row in point_rows)
        if point_codes == ["BARRIER_FAR_01", "BARRIER_NEAR_02"]:
            print("OK request points:", ", ".join(point_codes))
        else:
            print("FAIL request points:", point_codes)
            ok = False

        print("")
        if ok:
            print("RESULT: PASS")
            return 0

        print("RESULT: FAIL")
        return 2

    finally:
        try:
            conn.close()
        except Exception:
            pass

        if args.keep_temp:
            print("")
            print("Temp DB kept:", tmp_db)
        else:
            tmp_db.unlink(missing_ok=True)
            print("")
            print("Temp DB deleted:", tmp_db)

        print("SOURCE DB UNTOUCHED")
        print("TEST COMPLETED")


if __name__ == "__main__":
    raise SystemExit(main())
