#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
remote_debt_gate_test_fixture.py

Sandbox-only TEST fixture for the pult/remote debt gate.

Purpose:
  1) Find an apartment with unpaid PARKING/BARRIER debt.
  2) Create one clearly marked TEST remote_requests row for that apartment.
  3) Let the real bot/guard UI try to show/issue that request.
     Expected result after INSTALL_remote_debt_gate_v1.py:
       - "✅ Пульт выдан" is hidden for the debtor apartment;
       - _save_remote_issued refuses if called anyway.

Default mode is READ ONLY / DRY RUN.
No production use. Refuses to run writes unless config.USE_TEST_DB is True.

PowerShell from G:\Programming\Py:
  python .\OSBB\tools\remote_debt_gate_test_fixture.py

Create fixture:
  python .\OSBB\tools\remote_debt_gate_test_fixture.py --apply

Create fixture for a specific apartment:
  python .\OSBB\tools\remote_debt_gate_test_fixture.py --apt 40 --apply

Check an existing request:
  python .\OSBB\tools\remote_debt_gate_test_fixture.py --request-id 123

Cleanup/cancel TEST fixtures:
  python .\OSBB\tools\remote_debt_gate_test_fixture.py --cleanup --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


MARKER = "TEST_DEBT_GATE_FIXTURE"
DEFAULT_REQUEST_KIND = "ADDITIONAL"


@dataclass
class RuntimeContext:
    root: Path
    py_root: Path
    db_path: Path
    backup_dir: Path
    use_test_db: bool
    mode_label: str


@dataclass
class DebtCandidate:
    apartment_id: int | None
    apartment_number: str
    entrance: str
    debt_total: float
    rows: int
    periods: str
    services: str


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def money(value: Any) -> str:
    n = float(value or 0)
    if abs(n - round(n)) < 0.00001:
        return str(int(round(n)))
    return f"{n:.2f}"


def project_root_from_script() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name.lower() == "tools":
        return here.parent.parent
    cwd = Path.cwd().resolve()
    if (cwd / "OSBB").exists():
        return cwd / "OSBB"
    return cwd


def load_context() -> RuntimeContext:
    root = project_root_from_script()
    py_root = root.parent
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))

    try:
        import config  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Cannot import config.py from {py_root}: {type(exc).__name__}: {exc}")

    use_test = bool(getattr(config, "USE_TEST_DB", False))
    paths = getattr(config, "paths")
    db_path = Path(paths.OSBB_TEST_DB_FILE if use_test else paths.OSBB_DB_FILE)
    backup_dir = Path(getattr(paths, "OSBB_BACKUPS_DIR", root / "Data" / "db" / "backups"))

    return RuntimeContext(
        root=root,
        py_root=py_root,
        db_path=db_path,
        backup_dir=backup_dir,
        use_test_db=use_test,
        mode_label="TEST" if use_test else "PROD",
    )


def connect(ctx: RuntimeContext, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = ctx.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(ctx.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table_name: str) -> list[str]:
    if not table_exists(cur, table_name):
        return []
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def dynamic_insert(cur: sqlite3.Cursor, table_name: str, values: dict[str, Any]) -> int:
    cols = table_columns(cur, table_name)
    actual = {k: v for k, v in values.items() if k in cols}
    if not actual:
        raise RuntimeError(f"No matching insert columns for {table_name}")
    placeholders = ", ".join("?" for _ in actual)
    col_sql = ", ".join(actual.keys())
    cur.execute(f"INSERT INTO {table_name} ({col_sql}) VALUES ({placeholders})", tuple(actual.values()))
    return int(cur.lastrowid)


def dynamic_update(cur: sqlite3.Cursor, table_name: str, row_id: int, values: dict[str, Any]) -> bool:
    cols = table_columns(cur, table_name)
    actual = {k: v for k, v in values.items() if k in cols}
    if not actual:
        return False
    assignments = ", ".join(f"{k} = ?" for k in actual)
    cur.execute(f"UPDATE {table_name} SET {assignments} WHERE id = ?", tuple(actual.values()) + (int(row_id),))
    return cur.rowcount > 0


def allocation_amount_column(cols: list[str]) -> str | None:
    if "amount" in cols:
        return "amount"
    if "allocated_amount" in cols:
        return "allocated_amount"
    return None


def blocking_service(service_code: Any) -> bool:
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


def charge_amount_expr(cols: list[str]) -> str:
    if "net_amount" in cols and "amount" in cols:
        return "COALESCE(c.net_amount, c.amount, 0)"
    if "amount" in cols:
        return "COALESCE(c.amount, 0)"
    if "net_amount" in cols:
        return "COALESCE(c.net_amount, 0)"
    return "0"


def service_expr(cols: list[str]) -> str:
    candidates = []
    if "base_service_code" in cols:
        candidates.append("NULLIF(c.base_service_code, '')")
    if "service_code" in cols:
        candidates.append("NULLIF(c.service_code, '')")
    if not candidates:
        return "''"
    return "COALESCE(" + ", ".join(candidates + ["''"]) + ")"


def period_expr(cols: list[str]) -> str:
    return "COALESCE(c.period_code, '')" if "period_code" in cols else "''"


def status_filter(cols: list[str]) -> str:
    if "charge_status" in cols:
        return "AND COALESCE(c.charge_status, '') <> 'cancelled'"
    if "status" in cols:
        return "AND COALESCE(c.status, '') <> 'cancelled'"
    return ""


def debt_rows_for_apartment(cur: sqlite3.Cursor, apartment_number: str, period: str = "") -> list[dict[str, Any]]:
    if not table_exists(cur, "charges"):
        raise RuntimeError("Table charges is missing.")

    charge_cols = table_columns(cur, "charges")
    if "apartment_number" not in charge_cols:
        raise RuntimeError("charges.apartment_number is missing.")

    allocation_join = ""
    allocation_select = "0 AS allocated_amount"
    if table_exists(cur, "payment_allocations"):
        alloc_cols = table_columns(cur, "payment_allocations")
        amount_col = allocation_amount_column(alloc_cols)
        if amount_col and "charge_id" in alloc_cols:
            allocation_join = f'LEFT JOIN payment_allocations pa ON pa.charge_id = c.id'
            allocation_select = f'COALESCE(SUM(pa."{amount_col}"), 0) AS allocated_amount'

    where = ["c.apartment_number = ?"]
    params: list[Any] = [text(apartment_number)]
    if period and "period_code" in charge_cols:
        where.append("c.period_code = ?")
        params.append(period)

    sql = f"""
        SELECT
            c.id AS charge_id,
            c.apartment_number AS apartment_number,
            {period_expr(charge_cols)} AS period_code,
            {service_expr(charge_cols)} AS service_code,
            {charge_amount_expr(charge_cols)} AS amount,
            {allocation_select}
        FROM charges c
        {allocation_join}
        WHERE {' AND '.join(where)}
        {status_filter(charge_cols)}
        GROUP BY c.id
        ORDER BY {period_expr(charge_cols)}, c.id
    """
    cur.execute(sql, tuple(params))

    rows = []
    for row in cur.fetchall():
        item = dict(row)
        if not blocking_service(item.get("service_code")):
            continue
        amount = float(item.get("amount") or 0)
        allocated = float(item.get("allocated_amount") or 0)
        outstanding = max(0.0, amount - allocated)
        if outstanding > 0.01:
            item["outstanding_amount"] = outstanding
            rows.append(item)
    return rows


def apartment_info(cur: sqlite3.Cursor, apartment_number: str) -> tuple[int | None, str]:
    if not table_exists(cur, "apartments"):
        return None, ""
    cols = table_columns(cur, "apartments")
    fields = ["id", "apartment_number"]
    entrance_field = ""
    for candidate in ("entrance_number", "entrance"):
        if candidate in cols:
            entrance_field = candidate
            fields.append(candidate)
            break
    cur.execute(
        f"SELECT {', '.join(fields)} FROM apartments WHERE apartment_number = ? LIMIT 1",
        (text(apartment_number),),
    )
    row = cur.fetchone()
    if not row:
        return None, ""
    return int(row["id"]) if row["id"] is not None else None, text(row[entrance_field]) if entrance_field else ""


def find_debt_candidates(cur: sqlite3.Cursor, apt: str = "", period: str = "", limit: int = 20) -> list[DebtCandidate]:
    if not table_exists(cur, "charges"):
        raise RuntimeError("Table charges is missing.")

    charge_cols = table_columns(cur, "charges")
    if "apartment_number" not in charge_cols:
        raise RuntimeError("charges.apartment_number is missing.")

    where = []
    params: list[Any] = []
    if apt:
        where.append("apartment_number = ?")
        params.append(text(apt))
    if period and "period_code" in charge_cols:
        where.append("period_code = ?")
        params.append(period)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    cur.execute(f"""
        SELECT DISTINCT apartment_number
        FROM charges
        {where_sql}
        ORDER BY apartment_number
    """, tuple(params))

    result: list[DebtCandidate] = []
    for row in cur.fetchall():
        apt_no = text(row["apartment_number"])
        rows = debt_rows_for_apartment(cur, apt_no, period=period)
        total = sum(float(x.get("outstanding_amount") or 0) for x in rows)
        if total <= 0.01:
            continue
        apt_id, entrance = apartment_info(cur, apt_no)
        periods = ", ".join(sorted({text(x.get("period_code")) for x in rows if text(x.get("period_code"))}))
        services = ", ".join(sorted({text(x.get("service_code")) for x in rows if text(x.get("service_code"))}))
        result.append(DebtCandidate(
            apartment_id=apt_id,
            apartment_number=apt_no,
            entrance=entrance,
            debt_total=round(total, 2),
            rows=len(rows),
            periods=periods,
            services=services,
        ))

    result.sort(key=lambda x: (-x.debt_total, x.apartment_number))
    return result[:limit]


def existing_test_account(cur: sqlite3.Cursor, apartment_number: str) -> dict[str, Any] | None:
    if not table_exists(cur, "resident_accounts"):
        return None
    cols = table_columns(cur, "resident_accounts")
    where = []
    params = []
    if "notes" in cols:
        where.append("COALESCE(notes, '') LIKE ?")
        params.append(f"%{MARKER}%")
    if "apartment_number" in cols:
        where.append("apartment_number = ?")
        params.append(text(apartment_number))
    if not where:
        return None
    cur.execute(
        f"SELECT * FROM resident_accounts WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 1",
        tuple(params),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def create_or_get_test_account(cur: sqlite3.Cursor, candidate: DebtCandidate) -> dict[str, Any]:
    existing = existing_test_account(cur, candidate.apartment_number)
    if existing:
        return existing

    if not table_exists(cur, "resident_accounts"):
        raise RuntimeError("resident_accounts table is missing; cannot satisfy remote_requests.resident_account_id.")

    # Negative synthetic Telegram ID. Keep it deterministic enough to be unique.
    base = -900000000000 - int(time.time())
    while True:
        cur.execute("SELECT id FROM resident_accounts WHERE telegram_user_id = ?", (base,))
        if cur.fetchone() is None:
            break
        base -= 1

    values = {
        "telegram_user_id": base,
        "telegram_username": "TEST_REMOTE_DEBT_GATE",
        "telegram_first_name": "TEST",
        "telegram_last_name": "DEBT_GATE",
        "apartment_id": candidate.apartment_id,
        "apartment_number": candidate.apartment_number,
        "role": "resident",
        "status": "test_fixture",
        "language_code": "ru",
        "created_at": now_db(),
        "updated_at": now_db(),
        "verified_at": None,
        "last_seen_at": None,
        "notes": (
            f"{MARKER}; synthetic account for sandbox pult debt gate test; "
            f"created_at={now_db()}; do not use as real resident."
        ),
    }
    account_id = dynamic_insert(cur, "resident_accounts", values)
    cur.execute("SELECT * FROM resident_accounts WHERE id = ?", (account_id,))
    return dict(cur.fetchone())


def existing_fixture_request(cur: sqlite3.Cursor, apartment_number: str) -> dict[str, Any] | None:
    if not table_exists(cur, "remote_requests"):
        return None
    cols = table_columns(cur, "remote_requests")
    if "resident_comment" not in cols:
        return None
    status_filter_sql = ""
    if "status" in cols:
        status_filter_sql = "AND COALESCE(status, '') IN ('NEW', 'IN_REVIEW')"
    cur.execute(f"""
        SELECT *
        FROM remote_requests
        WHERE apartment_number = ?
          AND COALESCE(resident_comment, '') LIKE ?
          {status_filter_sql}
        ORDER BY id DESC
        LIMIT 1
    """, (text(apartment_number), f"%{MARKER}%"))
    row = cur.fetchone()
    return dict(row) if row else None


def create_fixture_request(cur: sqlite3.Cursor, candidate: DebtCandidate, new_always: bool = False) -> dict[str, Any]:
    if not table_exists(cur, "remote_requests"):
        raise RuntimeError("remote_requests table is missing.")

    if not new_always:
        existing = existing_fixture_request(cur, candidate.apartment_number)
        if existing:
            return existing

    account = create_or_get_test_account(cur, candidate)
    account_id = int(account["id"])
    tg = text(account.get("telegram_user_id"))

    values = {
        "resident_account_id": account_id,
        "telegram_user_id": tg,
        "current_apartment_id": candidate.apartment_id,
        "current_apartment_number": candidate.apartment_number,
        "requested_apartment_id": candidate.apartment_id,
        "requested_apartment_number": candidate.apartment_number,
        "apartment_id": candidate.apartment_id,
        "apartment_number": candidate.apartment_number,
        "request_kind": DEFAULT_REQUEST_KIND,
        "quantity": 1,
        "resident_comment": (
            f"{MARKER}: sandbox-only request for debt gate test. "
            f"Apartment={candidate.apartment_number}; debt={candidate.debt_total} UAH. DO NOT ISSUE."
        ),
        "status": "NEW",
        "operator_id": "remote_debt_gate_test_fixture",
        "operator_note": None,
        "created_at": now_db(),
        "updated_at": now_db(),
        "reviewed_at": None,
        "issued_at": None,
        "closed_at": None,
    }
    request_id = dynamic_insert(cur, "remote_requests", values)
    cur.execute("SELECT * FROM remote_requests WHERE id = ?", (request_id,))
    return dict(cur.fetchone())


def backup_db(ctx: RuntimeContext) -> Path:
    ctx.backup_dir.mkdir(parents=True, exist_ok=True)
    dst = ctx.backup_dir / f"{ctx.db_path.stem}_before_remote_debt_gate_fixture_{datetime.now():%Y-%m-%d_%H-%M-%S}{ctx.db_path.suffix}"
    shutil.copy2(ctx.db_path, dst)
    return dst


def cancel_fixture_requests(cur: sqlite3.Cursor) -> tuple[int, int]:
    if not table_exists(cur, "remote_requests"):
        return 0, 0
    cols = table_columns(cur, "remote_requests")
    if "resident_comment" not in cols:
        return 0, 0

    cur.execute("""
        SELECT *
        FROM remote_requests
        WHERE COALESCE(resident_comment, '') LIKE ?
          AND COALESCE(status, '') IN ('NEW', 'IN_REVIEW')
        ORDER BY id
    """, (f"%{MARKER}%",))
    requests = [dict(row) for row in cur.fetchall()]

    changed_requests = 0
    for req in requests:
        values = {
            "status": "CANCELLED",
            "operator_id": "remote_debt_gate_test_fixture",
            "operator_note": f"{MARKER} cleanup at {now_db()}",
            "updated_at": now_db(),
            "reviewed_at": now_db(),
            "closed_at": now_db(),
        }
        if dynamic_update(cur, "remote_requests", int(req["id"]), values):
            changed_requests += 1

    changed_accounts = 0
    if table_exists(cur, "resident_accounts"):
        acc_cols = table_columns(cur, "resident_accounts")
        if "notes" in acc_cols:
            cur.execute("""
                SELECT *
                FROM resident_accounts
                WHERE COALESCE(notes, '') LIKE ?
            """, (f"%{MARKER}%",))
            accounts = [dict(row) for row in cur.fetchall()]
            for acc in accounts:
                values = {
                    "status": "test_cancelled",
                    "updated_at": now_db(),
                    "notes": (text(acc.get("notes")) + f"; cleanup={now_db()}")[:2000],
                }
                if dynamic_update(cur, "resident_accounts", int(acc["id"]), values):
                    changed_accounts += 1

    return changed_requests, changed_accounts


def source_marker_status(ctx: RuntimeContext) -> dict[str, bool]:
    files = {
        "client_portal.py": ctx.root / "Bots" / "handlers" / "client_portal.py",
        "guard_workspace.py": ctx.root / "Bots" / "handlers" / "guard_workspace.py",
    }
    result = {}
    for name, path in files.items():
        if not path.exists():
            result[name] = False
            continue
        data = path.read_text(encoding="utf-8", errors="replace")
        result[name] = "OSBB_REMOTE_DEBT_GATE_V1" in data
    return result


def print_candidate(candidate: DebtCandidate) -> None:
    print(f"Candidate apartment: {candidate.apartment_number} | id={candidate.apartment_id or '-'} | entrance={candidate.entrance or '-'}")
    print(f"Debt total: {money(candidate.debt_total)} UAH")
    print(f"Debt rows: {candidate.rows}")
    print(f"Periods: {candidate.periods or '-'}")
    print(f"Services: {candidate.services or '-'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create sandbox-only TEST remote request for pult debt gate.")
    parser.add_argument("--apply", action="store_true", help="Write TEST fixture / cleanup after DB backup.")
    parser.add_argument("--cleanup", action="store_true", help="Cancel existing TEST fixture requests.")
    parser.add_argument("--apt", default="", help="Apartment number to use, e.g. 40.")
    parser.add_argument("--period", default="", help="Optional period_code filter, e.g. 2026-05_2026-06.")
    parser.add_argument("--request-id", type=int, default=0, help="Check a specific remote_requests row.")
    parser.add_argument("--new", action="store_true", help="Create a new fixture even if an open one already exists.")
    parser.add_argument("--limit", type=int, default=10, help="How many debt candidates to show.")
    args = parser.parse_args()

    ctx = load_context()

    print("=" * 88)
    print("OSBB remote/pult debt gate TEST fixture")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "READ ONLY / DRY RUN")
    print("DB mode from config.py:", ctx.mode_label)
    print("USE_TEST_DB:", ctx.use_test_db)
    print("DB:", ctx.db_path)
    print("")

    markers = source_marker_status(ctx)
    print("Source patch markers:")
    for name, ok in markers.items():
        print(f" - {name}: {'present' if ok else 'missing'}")
    print("")

    if args.apply and not ctx.use_test_db:
        raise SystemExit("REFUSED: config.USE_TEST_DB is False. This fixture is sandbox/test only.")

    readonly = not args.apply
    conn = connect(ctx, readonly=readonly)
    try:
        cur = conn.cursor()

        if args.cleanup:
            print("Cleanup target: TEST fixture remote_requests with marker:", MARKER)
            if not args.apply:
                if table_exists(cur, "remote_requests"):
                    cur.execute("""
                        SELECT id, apartment_number, status, resident_comment
                        FROM remote_requests
                        WHERE COALESCE(resident_comment, '') LIKE ?
                        ORDER BY id
                    """, (f"%{MARKER}%",))
                    rows = cur.fetchall()
                    print("Fixture rows found:", len(rows))
                    for row in rows[:20]:
                        print(f" - request #{row['id']} | apt={row['apartment_number']} | status={row['status']} | {row['resident_comment']}")
                print("")
                print("DRY RUN ONLY. Re-run with --cleanup --apply to cancel fixtures.")
                return 0

            backup = backup_db(ctx)
            req_count, acc_count = cancel_fixture_requests(cur)
            conn.commit()
            print("DB backup:", backup)
            print("Cancelled fixture requests:", req_count)
            print("Marked fixture accounts:", acc_count)
            print("CLEANUP COMPLETED")
            return 0

        if args.request_id:
            if not table_exists(cur, "remote_requests"):
                raise RuntimeError("remote_requests table is missing.")
            cur.execute("SELECT * FROM remote_requests WHERE id = ?", (args.request_id,))
            req = cur.fetchone()
            if not req:
                raise RuntimeError(f"remote_requests id={args.request_id} not found.")
            req = dict(req)
            apt = text(req.get("apartment_number"))
            rows = debt_rows_for_apartment(cur, apt, period=args.period)
            total = sum(float(x["outstanding_amount"]) for x in rows)
            print(f"Request #{args.request_id}: apt={apt}, status={req.get('status')}, kind={req.get('request_kind')}, qty={req.get('quantity')}")
            print(f"Debt check: {money(total)} UAH, rows={len(rows)}")
            print("Expected bot result:", "BLOCK / no issue button" if total > 0.01 else "ALLOW")
            print("READ ONLY COMPLETED")
            return 0

        candidates = find_debt_candidates(cur, apt=args.apt, period=args.period, limit=max(1, args.limit))
        if not candidates:
            print("No debt candidate found.")
            if args.apt:
                print("Apartment filter:", args.apt)
            if args.period:
                print("Period filter:", args.period)
            return 2

        print("Debt candidates:")
        for i, c in enumerate(candidates, start=1):
            print(f"{i:>2}. apt={c.apartment_number:>6} | id={c.apartment_id or '-':>4} | debt={money(c.debt_total):>8} | rows={c.rows:>2} | periods={c.periods or '-'} | services={c.services or '-'}")
        print("")

        candidate = candidates[0]
        print_candidate(candidate)
        print("")

        existing = existing_fixture_request(cur, candidate.apartment_number)
        if existing:
            print(f"Existing open TEST fixture request: #{existing['id']} | status={existing.get('status')} | apt={existing.get('apartment_number')}")
            print("Expected bot result: BLOCK / no issue button")
            if not args.new:
                print("")
                print("No new fixture needed unless you pass --new.")
                if not args.apply:
                    print("READ ONLY COMPLETED")
                    return 0

        if not args.apply:
            print("Planned write with --apply:")
            print(f" - create/reuse synthetic resident_account marked {MARKER}")
            print(f" - create one remote_requests row for apt {candidate.apartment_number}")
            print(" - no changes to charges/payments/vehicles/apartments")
            print("")
            print("DRY RUN ONLY. Re-run with --apply to create fixture.")
            return 0

        backup = backup_db(ctx)
        request = create_fixture_request(cur, candidate, new_always=args.new)
        conn.commit()

        rows = debt_rows_for_apartment(cur, candidate.apartment_number, period=args.period)
        total = sum(float(x["outstanding_amount"]) for x in rows)

        print("DB backup:", backup)
        print("Created/reused TEST fixture request:")
        print(f" - request id: {request['id']}")
        print(f" - apartment: {request.get('apartment_number')}")
        print(f" - status: {request.get('status')}")
        print(f" - kind: {request.get('request_kind')}")
        print(f" - quantity: {request.get('quantity')}")
        print(f" - comment: {request.get('resident_comment')}")
        print("")
        print(f"Debt gate simulation for request #{request['id']}:")
        print(f" - blocking debt: {money(total)} UAH")
        print(f" - expected bot result: {'BLOCK / no issue button' if total > 0.01 else 'ALLOW'}")
        print("")
        print("Next manual bot test:")
        print("  🛡 Пост охраны O -> 📤 Пульт выдан -> select the TEST request")
        print("  Expected: debt warning and no '✅ Пульт выдан' button.")
        print("")
        print("To cleanup later:")
        print("  python .\\OSBB\\tools\\remote_debt_gate_test_fixture.py --cleanup --apply")
        print("APPLY COMPLETED")
        return 0

    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
