#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
run_remote_debt_blocked_probe.py

Runner v1 probe:
TEST_REMOTE_NEW_DEBT_BLOCKED

Purpose:
- verify that a resident with debt cannot create a new remote/pult service request;
- produce a report and DB diff summary;
- do not require a full universal YAML Runner yet.

Safety:
- never modifies source DB directly;
- always copies DB to Runner/TEMP_DB before running;
- successful run keeps only report/snapshots/diff/log;
- full temp DB is kept only with --keep-db or on failure.

Usage:
  python .\OSBB\Runner\run_remote_debt_blocked_probe.py
  python .\OSBB\Runner\run_remote_debt_blocked_probe.py --apply
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import shutil
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_DB = DEFAULT_ROOT / "Data" / "db" / "osbb_test.db"
SCENARIO = "TEST_REMOTE_NEW_DEBT_BLOCKED"

OBSERVED_TABLES = [
    "service_orders",
    "service_order_steps",
    "service_order_interests",
    "payments",
    "cashbox_operations",
    "operator_audit_log",
    "remote_order_details",
    "remote_supplier_batches",
    "remote_supplier_batch_links",
    "remote_order_issued_assets",
    "resident_accounts",
    "apartments",
    "charges",
]


@dataclass
class ProbeResult:
    scenario: str
    status: str
    started_at: str
    finished_at: str
    source_db: str
    temp_db: str
    apartment: int
    quantity: int
    debtor_prepared: bool
    order_action_status: str
    order_action_detail: str
    service_orders_before: int | None
    service_orders_after: int | None
    service_orders_delta: int | None
    expected_blocked: bool
    actual_blocked: bool
    notes: list[str]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not table_exists(conn, table):
        return []
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def count_rows(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def snapshot_db(conn: sqlite3.Connection) -> dict[str, Any]:
    snap: dict[str, Any] = {"created_at": now_text(), "tables": {}}
    for table in sorted(set(OBSERVED_TABLES)):
        if not table_exists(conn, table):
            snap["tables"][table] = {"exists": False}
            continue

        info: dict[str, Any] = {
            "exists": True,
            "columns": columns(conn, table),
            "count": count_rows(conn, table),
            "last_rows": [],
        }

        cols = info["columns"]
        if "id" in cols:
            order = "id DESC"
        elif "created_at" in cols:
            order = "created_at DESC"
        else:
            order = "rowid DESC"

        try:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order} LIMIT 5").fetchall()
            info["last_rows"] = [dict(r) for r in rows]
        except Exception as exc:
            info["last_rows_error"] = str(exc)

        snap["tables"][table] = info
    return snap


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {"created_at": now_text(), "tables": {}}
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        b = before_tables.get(table, {})
        a = after_tables.get(table, {})
        b_count = b.get("count")
        a_count = a.get("count")
        delta = None
        if isinstance(b_count, int) and isinstance(a_count, int):
            delta = a_count - b_count
        diff["tables"][table] = {
            "before_exists": b.get("exists", False),
            "after_exists": a.get("exists", False),
            "before_count": b_count,
            "after_count": a_count,
            "delta": delta,
        }
    return diff


def try_prepare_debtor(conn: sqlite3.Connection, apartment: int, notes: list[str]) -> bool:
    if not table_exists(conn, "charges"):
        notes.append("charges table not present; debtor marker could not be inserted via charges.")
        return False

    cols = columns(conn, "charges")
    lower = {c.lower(): c for c in cols}

    apt_col = None
    for candidate in ["apartment", "apartment_no", "apartment_number", "unit", "unit_id", "apartment_id"]:
        if candidate in lower:
            apt_col = lower[candidate]
            break

    amount_col = None
    for candidate in ["amount", "charge_amount", "debt", "balance", "sum", "total"]:
        if candidate in lower:
            amount_col = lower[candidate]
            break

    if not apt_col or not amount_col:
        notes.append(f"charges table exists but suitable apartment/amount columns not found: {cols}")
        return False

    insert_cols = [apt_col, amount_col]
    values: list[Any] = [apartment, 1000]

    if "created_at" in lower:
        insert_cols.append(lower["created_at"])
        values.append(now_text())
    if "description" in lower:
        insert_cols.append(lower["description"])
        values.append(f"{SCENARIO}: artificial debtor marker")
    if "status" in lower:
        insert_cols.append(lower["status"])
        values.append("unpaid")
    if "is_paid" in lower:
        insert_cols.append(lower["is_paid"])
        values.append(0)

    sql = f"INSERT INTO charges ({', '.join(insert_cols)}) VALUES ({', '.join(['?'] * len(values))})"
    try:
        conn.execute(sql, values)
        conn.commit()
        notes.append(f"Inserted artificial debtor marker into charges using columns {insert_cols}.")
        return True
    except Exception as exc:
        conn.rollback()
        notes.append(f"Could not insert debtor marker into charges: {type(exc).__name__}: {exc}")
        return False


def discover_order_action(root: Path, notes: list[str]):
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "Bots"))
    sys.path.insert(0, str(root / "Bots" / "handlers"))

    candidates = [
        ("service_preorders_core", ["create_remote_order_interest", "create_service_interest", "create_preorder_interest"]),
        ("service_orders_core", ["create_service_order", "create_remote_service_order", "create_order"]),
        ("Bots.handlers.service_orders_workspace", ["create_remote_order", "order_new_remote"]),
    ]

    for module_name, function_names in candidates:
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:
            notes.append(f"Cannot import {module_name}: {type(exc).__name__}: {exc}")
            continue

        for fn_name in function_names:
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                try:
                    sig = inspect.signature(fn)
                except Exception:
                    sig = "(signature unavailable)"
                notes.append(f"Discovered callable order action: {module_name}.{fn_name}{sig}")
                return module_name, fn_name, fn

    return None, None, None


def call_order_action(fn, db: Path, apartment: int, quantity: int, notes: list[str]) -> tuple[str, str]:
    created_connections: list[sqlite3.Connection] = []
    try:
        sig = inspect.signature(fn)
        kwargs: dict[str, Any] = {}

        for name in sig.parameters:
            low = name.lower()
            if low in {"db", "db_path", "database", "database_path"}:
                kwargs[name] = str(db)
            elif low in {"conn", "connection"}:
                conn = connect(db)
                created_connections.append(conn)
                kwargs[name] = conn
            elif low in {"apartment", "apartment_no", "apartment_number", "unit", "unit_id"}:
                kwargs[name] = apartment
            elif low in {"quantity", "qty", "count"}:
                kwargs[name] = quantity
            elif low in {"service_item_code", "item_code"}:
                kwargs[name] = "REMOTE_NEW"
            elif low in {"created_by", "telegram_id", "user_id"}:
                kwargs[name] = 999000174
            elif low in {"comment", "note"}:
                kwargs[name] = SCENARIO

        result = fn(**kwargs)
        notes.append(f"Order action returned: {result!r}")
        return "CALLED", repr(result)
    except TypeError as exc:
        return "CALL_SIGNATURE_UNSUPPORTED", str(exc)
    except Exception as exc:
        notes.append(traceback.format_exc())
        return "CALL_FAILED", f"{type(exc).__name__}: {exc}"
    finally:
        for conn in created_connections:
            try:
                conn.close()
            except Exception:
                pass


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(path: Path, result: ProbeResult, diff: dict[str, Any], before_path: Path, after_path: Path, diff_path: Path, log_path: Path) -> None:
    status_icon = "PASS" if result.status == "PASS" else "FAIL" if result.status == "FAIL" else "WARN"
    lines = []
    lines.append(f"# {result.scenario}")
    lines.append("")
    lines.append(f"Status: **{status_icon} / {result.status}**")
    lines.append(f"Started: `{result.started_at}`")
    lines.append(f"Finished: `{result.finished_at}`")
    lines.append("")
    lines.append("## Purpose")
    lines.append("")
    lines.append("Verify that a debtor cannot create a new remote/pult service request.")
    lines.append("")
    lines.append("## Database")
    lines.append("")
    lines.append(f"- Source DB: `{result.source_db}`")
    lines.append(f"- Temp DB: `{result.temp_db}`")
    lines.append(f"- Apartment: `{result.apartment}`")
    lines.append(f"- Quantity: `{result.quantity}`")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(f"- Debtor prepared: `{result.debtor_prepared}`")
    lines.append(f"- Order action status: `{result.order_action_status}`")
    lines.append(f"- Order action detail: `{result.order_action_detail}`")
    lines.append(f"- service_orders before: `{result.service_orders_before}`")
    lines.append(f"- service_orders after: `{result.service_orders_after}`")
    lines.append(f"- service_orders delta: `{result.service_orders_delta}`")
    lines.append(f"- Expected blocked: `{result.expected_blocked}`")
    lines.append(f"- Actual blocked: `{result.actual_blocked}`")
    lines.append("")
    lines.append("## DB diff summary")
    lines.append("")
    lines.append("| Table | Before | After | Delta |")
    lines.append("|---|---:|---:|---:|")
    for table, info in diff.get("tables", {}).items():
        lines.append(f"| `{table}` | `{info.get('before_count')}` | `{info.get('after_count')}` | `{info.get('delta')}` |")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(f"- Before snapshot: `{before_path}`")
    lines.append(f"- After snapshot: `{after_path}`")
    lines.append(f"- DB diff: `{diff_path}`")
    lines.append(f"- Log: `{log_path}`")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for note in result.notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if result.order_action_status == "ORDER_REMOTE_ACTION_NOT_AVAILABLE":
        lines.append("Current code does not expose a stable callable order action for Runner yet.")
        lines.append("Next implementation step is to add a test seam/core function.")
    elif result.actual_blocked:
        lines.append("No new service order was created for debtor.")
    else:
        lines.append("A service order appears to have been created for debtor or DB changed unexpectedly.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_probe(args: argparse.Namespace) -> int:
    root = Path(args.root)
    source_db = Path(args.db)

    if not root.exists():
        raise SystemExit(f"Root not found: {root}")
    if not source_db.exists():
        raise SystemExit(f"DB not found: {source_db}")

    stamp = now_stamp()
    started = now_text()

    temp_dir = root / "Runner" / "TEMP_DB"
    report_dir = root / "Runner" / "REPORTS"
    snap_dir = root / "Runner" / "DB_SNAPSHOTS"
    diff_dir = root / "Runner" / "DB_DIFFS"
    log_dir = root / "Runner" / "LOGS"

    temp_db = temp_dir / f"{SCENARIO}_{stamp}.db"
    before_path = snap_dir / f"{SCENARIO}_{stamp}_before.json"
    after_path = snap_dir / f"{SCENARIO}_{stamp}_after.json"
    diff_path = diff_dir / f"{SCENARIO}_{stamp}_diff.json"
    log_path = log_dir / f"{SCENARIO}_{stamp}.log"
    report_path = report_dir / f"{SCENARIO}_{stamp}.md"

    logs: list[str] = []
    notes: list[str] = []

    print("=" * 100)
    print("OSBB Runner probe:", SCENARIO)
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Root:", root)
    print("Source DB:", source_db)
    print("Apartment:", args.apartment)
    print("Quantity:", args.quantity)

    if not args.apply:
        print("")
        print("DRY RUN: would copy DB and execute debtor-blocked probe.")
        print("Report would be:", report_path)
        return 0

    temp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, temp_db)
    logs.append(f"{now_text()} Copied DB: {source_db} -> {temp_db}")

    status = "WARN"
    order_status = "ORDER_REMOTE_ACTION_NOT_AVAILABLE"
    order_detail = "No stable callable order action discovered."
    debtor_prepared = False
    before_count = after_count = delta = None
    actual_blocked = False

    try:
        conn = connect(temp_db)
        before_count = count_rows(conn, "service_orders")
        before_snapshot = snapshot_db(conn)
        write_json(before_path, before_snapshot)
        logs.append(f"{now_text()} Wrote before snapshot: {before_path}")

        debtor_prepared = try_prepare_debtor(conn, args.apartment, notes)
        conn.close()

        module_name, fn_name, fn = discover_order_action(root, notes)
        if fn is not None:
            order_status, order_detail = call_order_action(fn, temp_db, args.apartment, args.quantity, notes)
        else:
            notes.append("No stable callable order action found. Probe did not create request via code.")

        conn = connect(temp_db)
        after_count = count_rows(conn, "service_orders")
        after_snapshot = snapshot_db(conn)
        write_json(after_path, after_snapshot)
        conn.close()

        diff = diff_snapshots(before_snapshot, after_snapshot)
        write_json(diff_path, diff)

        if isinstance(before_count, int) and isinstance(after_count, int):
            delta = after_count - before_count
            actual_blocked = delta == 0
        else:
            actual_blocked = True if before_count is None and after_count is None else False

        if order_status == "ORDER_REMOTE_ACTION_NOT_AVAILABLE":
            status = "WARN"
        elif actual_blocked:
            status = "PASS"
        else:
            status = "FAIL"

        result = ProbeResult(
            scenario=SCENARIO,
            status=status,
            started_at=started,
            finished_at=now_text(),
            source_db=str(source_db),
            temp_db=str(temp_db),
            apartment=args.apartment,
            quantity=args.quantity,
            debtor_prepared=debtor_prepared,
            order_action_status=order_status,
            order_action_detail=order_detail,
            service_orders_before=before_count,
            service_orders_after=after_count,
            service_orders_delta=delta,
            expected_blocked=True,
            actual_blocked=actual_blocked,
            notes=notes,
        )

        write_report(report_path, result, diff, before_path, after_path, diff_path, log_path)
        logs.append(f"{now_text()} Result: {status}")
        logs.append(f"{now_text()} Report: {report_path}")
        write_log(log_path, logs)

        print("")
        print("RESULT:", status)
        print("Order action:", order_status)
        print("Actual blocked:", actual_blocked)
        print("Report:", report_path)
        print("Diff:", diff_path)

        if status == "FAIL" or args.keep_db:
            print("Temp DB kept:", temp_db)
        else:
            try:
                temp_db.unlink()
                print("Temp DB removed:", temp_db)
            except Exception:
                print("Temp DB could not be removed:", temp_db)

        return 0 if status in {"PASS", "WARN"} else 1

    except Exception as exc:
        logs.append(f"{now_text()} PROBE ERROR: {type(exc).__name__}: {exc}")
        logs.append(traceback.format_exc())
        write_log(log_path, logs)

        result = ProbeResult(
            scenario=SCENARIO,
            status="FAIL",
            started_at=started,
            finished_at=now_text(),
            source_db=str(source_db),
            temp_db=str(temp_db),
            apartment=args.apartment,
            quantity=args.quantity,
            debtor_prepared=debtor_prepared,
            order_action_status="PROBE_ERROR",
            order_action_detail=f"{type(exc).__name__}: {exc}",
            service_orders_before=before_count,
            service_orders_after=after_count,
            service_orders_delta=delta,
            expected_blocked=True,
            actual_blocked=False,
            notes=notes + ["Probe crashed. See log."],
        )
        write_report(report_path, result, {"tables": {}}, before_path, after_path, diff_path, log_path)
        print("")
        print("RESULT: FAIL")
        print("Error:", type(exc).__name__, exc)
        print("Report:", report_path)
        print("Log:", log_path)
        print("Temp DB kept:", temp_db)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apartment", type=int, default=174)
    ap.add_argument("--quantity", type=int, default=2)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep-db", action="store_true")
    args = ap.parse_args()
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
