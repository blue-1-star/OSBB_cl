#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSBB Cashier Surgical Cleanup v0.2

Developer scalpel for cashier/payment/service-order chains.

Default mode is DRY RUN.

v0.2 expands v0.1:
- service orders and service order links;
- remote / phone access request-like tables when linked by payment/service_order/notice/receipt ids;
- resident/payment notices;
- audit/log tables;
- schema-discovery pass for tables with suspicious link columns.

This is still conservative: it creates a deletion PLAN first.
Apply only after reading the plan.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = PROJECT_ROOT / "Data" / "backups" / "db"
REPORT_DIR = PROJECT_ROOT / "Data" / "reports" / "cashier_cleanup"

KNOWN_ORDER = [
    # fine-grained children
    ("payment_allocations", ["payment_id", "receipt_id", "cashier_receipt_id", "charge_id"]),
    ("service_order_charge_links", ["service_order_id", "charge_id", "payment_id", "receipt_id"]),
    ("charge_adjustments", ["charge_id", "payment_id", "receipt_id"]),
    ("access_debt_warnings", ["source_charge_id", "payment_id", "receipt_id", "service_order_id"]),
    # notices / requests / orders
    ("resident_payment_notices", ["payment_id", "receipt_id", "confirmed_receipt_id", "service_order_id"]),
    ("payment_notices", ["payment_id", "receipt_id", "confirmed_receipt_id", "service_order_id"]),
    ("service_orders", ["payment_id", "receipt_id", "cashier_receipt_id", "payment_notice_id"]),
    ("remote_orders", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("remote_requests", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("remote_control_orders", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("phone_access_requests", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("phone_access_subscriptions", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("phone_access_subscription_charges", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    # reconciliation
    ("cashier_reconciliation_cases", ["payment_id", "receipt_id", "cashier_receipt_id", "service_order_id", "notice_id"]),
    # charges / payments / receipts
    ("charges", ["payment_id", "receipt_id", "service_order_id", "payment_notice_id"]),
    ("payments", ["id", "receipt_id", "cashier_receipt_id", "service_order_id", "payment_notice_id"]),
    ("cashier_receipts", ["id", "payment_id", "service_order_id", "payment_notice_id"]),
]

AUDIT_TABLE_NAME_FRAGMENTS = [
    "audit", "log", "journal", "history", "event",
]

LINK_COLUMN_NAMES = [
    "payment_id", "receipt_id", "cashier_receipt_id", "confirmed_receipt_id",
    "service_order_id", "order_id", "request_id", "notice_id", "payment_notice_id",
    "charge_id", "source_charge_id",
]

ENTITY_COLUMNS = ["entity_id", "record_id", "object_id", "target_id"]
ENTITY_TYPE_COLUMNS = ["entity_type", "object_type", "target_type", "record_type", "table_name"]


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def all_tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def parse_ids(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(p.strip()) for p in text.replace(";", ",").split(",") if p.strip()]


def placeholders(values: Iterable[object]) -> str:
    vals = list(values)
    return ",".join(["?"] * len(vals))


def backup_db(db: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"before_cashier_surgical_cleanup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
    shutil.copy2(db, backup)
    return backup


def rows_by_ids(con: sqlite3.Connection, table: str, col: str, ids: list[int]) -> list[sqlite3.Row]:
    if not ids or not table_exists(con, table) or col not in columns(con, table):
        return []
    return con.execute(
        f"SELECT * FROM {table} WHERE {col} IN ({placeholders(ids)}) ORDER BY id",
        ids,
    ).fetchall()


def ids_from(rows: list[sqlite3.Row], col: str = "id") -> list[int]:
    out = []
    for row in rows:
        if col in row.keys() and row[col] is not None:
            try:
                out.append(int(row[col]))
            except Exception:
                pass
    return sorted(set(out))


def select_receipts(con: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    if not table_exists(con, "cashier_receipts"):
        return []
    cols = columns(con, "cashier_receipts")
    where = []
    params: list[object] = []

    ids = parse_ids(args.receipt_ids)
    if ids:
        where.append(f"id IN ({placeholders(ids)})")
        params.extend(ids)

    if args.source_like:
        for c in ["source_text", "comment", "note"]:
            if c in cols:
                where.append(f"{c} LIKE ?")
                params.append("%" + args.source_like + "%")
                break

    if args.operator_id and "operator_id" in cols:
        where.append("operator_id=?")
        params.append(str(args.operator_id))

    if args.date_from:
        dc = "receipt_date" if "receipt_date" in cols else "created_at"
        where.append(f"{dc}>=?")
        params.append(args.date_from)

    if args.date_to:
        dc = "receipt_date" if "receipt_date" in cols else "created_at"
        where.append(f"{dc}<=?")
        params.append(args.date_to)

    if not where:
        raise SystemExit("No selection criteria. Use --receipt-ids, --source-like, --operator-id/date range.")

    return con.execute("SELECT * FROM cashier_receipts WHERE " + " AND ".join(where) + " ORDER BY id", params).fetchall()


def expand_ids(con: sqlite3.Connection, receipt_rows: list[sqlite3.Row]) -> dict[str, set[int]]:
    ids: dict[str, set[int]] = {
        "receipt": set(ids_from(receipt_rows, "id")),
        "payment": set(ids_from(receipt_rows, "payment_id")),
        "notice": set(ids_from(receipt_rows, "payment_notice_id")),
        "service_order": set(ids_from(receipt_rows, "service_order_id")),
        "charge": set(),
        "request": set(),
        "order": set(),
    }

    changed = True
    while changed:
        changed = False

        # payments linked to receipts / service orders / notices
        for col, bucket in [
            ("receipt_id", "receipt"),
            ("cashier_receipt_id", "receipt"),
            ("service_order_id", "service_order"),
            ("payment_notice_id", "notice"),
        ]:
            rows = rows_by_ids(con, "payments", col, sorted(ids[bucket]))
            for pid in ids_from(rows):
                if pid not in ids["payment"]:
                    ids["payment"].add(pid); changed = True

        # receipts linked to payments
        rows = rows_by_ids(con, "cashier_receipts", "payment_id", sorted(ids["payment"]))
        for rid in ids_from(rows):
            if rid not in ids["receipt"]:
                ids["receipt"].add(rid); changed = True

        # notices linked to payment/receipt/service_order
        for table in ["resident_payment_notices", "payment_notices"]:
            for col, bucket in [
                ("payment_id", "payment"),
                ("receipt_id", "receipt"),
                ("confirmed_receipt_id", "receipt"),
                ("service_order_id", "service_order"),
            ]:
                rows = rows_by_ids(con, table, col, sorted(ids[bucket]))
                for nid in ids_from(rows):
                    if nid not in ids["notice"]:
                        ids["notice"].add(nid); changed = True

        # service orders linked to payment/receipt/notice
        for col, bucket in [
            ("payment_id", "payment"),
            ("receipt_id", "receipt"),
            ("cashier_receipt_id", "receipt"),
            ("payment_notice_id", "notice"),
        ]:
            rows = rows_by_ids(con, "service_orders", col, sorted(ids[bucket]))
            for sid in ids_from(rows):
                if sid not in ids["service_order"]:
                    ids["service_order"].add(sid); changed = True

        # service_order_charge_links: both directions
        rows = rows_by_ids(con, "service_order_charge_links", "service_order_id", sorted(ids["service_order"]))
        for cid in ids_from(rows, "charge_id"):
            if cid not in ids["charge"]:
                ids["charge"].add(cid); changed = True
        rows = rows_by_ids(con, "service_order_charge_links", "charge_id", sorted(ids["charge"]))
        for sid in ids_from(rows, "service_order_id"):
            if sid not in ids["service_order"]:
                ids["service_order"].add(sid); changed = True

        # charges linked to vehicle/service/order/payment
        for col, bucket in [("payment_id", "payment"), ("service_order_id", "service_order"), ("payment_notice_id", "notice")]:
            rows = rows_by_ids(con, "charges", col, sorted(ids[bucket]))
            for cid in ids_from(rows):
                if cid not in ids["charge"]:
                    ids["charge"].add(cid); changed = True

    return ids


def bucket_for_col(col: str) -> str | None:
    if col in {"payment_id"}:
        return "payment"
    if col in {"receipt_id", "cashier_receipt_id", "confirmed_receipt_id"}:
        return "receipt"
    if col in {"service_order_id"}:
        return "service_order"
    if col in {"notice_id", "payment_notice_id"}:
        return "notice"
    if col in {"charge_id", "source_charge_id"}:
        return "charge"
    if col in {"request_id"}:
        return "request"
    if col in {"order_id"}:
        return "order"
    return None


def collect_plan(con: sqlite3.Connection, ids: dict[str, set[int]]) -> tuple[dict[str, list[int]], list[str]]:
    plan: dict[str, set[int]] = {}
    notes: list[str] = []

    # Known dependency pass
    for table, cols_to_check in KNOWN_ORDER:
        if not table_exists(con, table):
            continue
        tcols = columns(con, table)
        for col in cols_to_check:
            if col not in tcols:
                continue
            bucket = "payment" if table == "payments" and col == "id" else "receipt" if table == "cashier_receipts" and col == "id" else bucket_for_col(col)
            if not bucket:
                continue
            id_list = sorted(ids.get(bucket, set()))
            rows = rows_by_ids(con, table, col, id_list)
            if rows:
                plan.setdefault(table, set()).update(ids_from(rows))

    # Discovery pass: any table with link columns
    for table in all_tables(con):
        if table in plan:
            # still may have other link columns, but known pass usually covers it
            pass
        tcols = columns(con, table)
        for col in sorted(tcols.intersection(set(LINK_COLUMN_NAMES))):
            bucket = bucket_for_col(col)
            if not bucket:
                continue
            id_list = sorted(ids.get(bucket, set()))
            rows = rows_by_ids(con, table, col, id_list)
            if rows:
                plan.setdefault(table, set()).update(ids_from(rows))
                if table not in [t for t, _ in KNOWN_ORDER]:
                    notes.append(f"DISCOVERED linked table: {table}.{col} -> {bucket}")

    # Audit/log pass
    all_related_ids = sorted(set().union(*ids.values())) if ids else []
    for table in all_tables(con):
        lname = table.lower()
        if not any(fragment in lname for fragment in AUDIT_TABLE_NAME_FRAGMENTS):
            continue
        tcols = columns(con, table)
        for col in ENTITY_COLUMNS:
            if col in tcols and all_related_ids:
                rows = rows_by_ids(con, table, col, all_related_ids)
                if rows:
                    plan.setdefault(table, set()).update(ids_from(rows))
                    notes.append(f"AUDIT/LOG candidate: {table}.{col} matched related ids; review carefully.")

    return {table: sorted(v) for table, v in plan.items() if v}, sorted(set(notes))


def report_row(row: sqlite3.Row) -> str:
    preferred = [
        "id", "receipt_number", "payment_id", "service_order_id", "payment_notice_id",
        "receipt_date", "created_at", "apartment_number", "amount", "currency",
        "cashbox_code", "operator_id", "status", "entry_status", "source_text", "comment", "note",
    ]
    return " | ".join(f"{c}={row[c]}" for c in preferred if c in row.keys() and row[c] is not None)


def write_report(db: Path, args: argparse.Namespace, receipts: list[sqlite3.Row], ids: dict[str, set[int]], plan: dict[str, list[int]], notes: list[str]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"cashier_cleanup_plan_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"

    lines = []
    lines.append("OSBB CASHIER SURGICAL CLEANUP PLAN v0.2")
    lines.append("=" * 100)
    lines.append(f"DB: {db}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Apply: {args.apply}")
    lines.append("")
    lines.append("Selected receipts")
    lines.append("-" * 100)
    for r in receipts:
        lines.append(report_row(r))
    lines.append("")
    lines.append("Expanded related ids")
    lines.append("-" * 100)
    for bucket, values in ids.items():
        lines.append(f"{bucket}: {', '.join(map(str, sorted(values))) if values else '—'}")
    lines.append("")
    lines.append("Deletion plan")
    lines.append("-" * 100)
    total = 0
    for table, row_ids in plan.items():
        total += len(row_ids)
        lines.append(f"{table}: {len(row_ids)}")
        lines.append("  ids: " + ", ".join(map(str, row_ids[:300])) + (" ..." if len(row_ids) > 300 else ""))
    lines.append("")
    lines.append(f"TOTAL planned rows: {total}")
    lines.append("")
    lines.append("Discovery notes")
    lines.append("-" * 100)
    if notes:
        lines.extend(notes)
    else:
        lines.append("No extra linked tables discovered.")
    lines.append("")
    lines.append("WARNING")
    lines.append("-" * 100)
    lines.append("Review plan before --apply.")
    lines.append("Audit/log matches by entity_id/record_id may be ambiguous.")
    lines.append("This tool is for developer use only.")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def delete_order(plan: dict[str, list[int]]) -> list[str]:
    known = [t for t, _ in KNOWN_ORDER if t in plan]
    extra = [t for t in plan if t not in known]
    # Extra tables first, then known child-to-parent order.
    return extra + known


def apply_plan(con: sqlite3.Connection, plan: dict[str, list[int]]) -> None:
    for table in delete_order(plan):
        ids = plan.get(table) or []
        if not ids:
            continue
        con.execute(f"DELETE FROM {table} WHERE id IN ({placeholders(ids)})", ids)


def main() -> int:
    ap = argparse.ArgumentParser(description="Developer surgical cleanup for OSBB cashier/payment/service chains.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--receipt-ids", help="Comma-separated cashier_receipts ids, e.g. 10,11,12")
    ap.add_argument("--source-like", help="Select receipts by source_text/comment/note LIKE fragment")
    ap.add_argument("--operator-id", help="Select receipts by operator_id if column exists")
    ap.add_argument("--date-from", help="Receipt date / created_at lower bound")
    ap.add_argument("--date-to", help="Receipt date / created_at upper bound")
    ap.add_argument("--apply", action="store_true", help="Actually delete after backup.")
    args = ap.parse_args()

    db = Path(args.db)
    con = connect(db)
    try:
        receipts = select_receipts(con, args)
        expanded = expand_ids(con, receipts)
        plan, notes = collect_plan(con, expanded)
        report = write_report(db, args, receipts, expanded, plan, notes)

        print("Cleanup plan saved:")
        print(report)
        print("Selected receipts:", len(receipts))
        print("Planned tables:", len(plan))
        print("Apply:", args.apply)

        if not args.apply:
            print("DRY RUN ONLY - no changes saved.")
            return 0

        backup = backup_db(db)
        apply_plan(con, plan)
        con.commit()
        print("Backup:", backup)
        print("APPLIED")
        return 0
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
