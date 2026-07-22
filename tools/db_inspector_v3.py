#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

SERVICE_NAMES = {
    "01_BarrierPhoneConnect": "Подключение телефона к шлагбауму",
    "BARRIER_PHONE_CONNECT": "Подключение телефона к шлагбауму",
    "TEST_REMOTE_NEW": "Новый пульт",
    "REMOTE_NEW_PREORDER": "Новый пульт",
    "TEST_REMOTE_REPROGRAM_OWN": "Перепрошивка собственного пульта",
    "REMOTE_REPROGRAM_OWN": "Перепрошивка собственного пульта",
}


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def header(title: str) -> None:
    print("=" * 90)
    print(title)
    print("=" * 90)


def section(title: str) -> None:
    print()
    print("-" * 90)
    print(title)
    print("-" * 90)


def table_names(con: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return con.execute(f"PRAGMA table_info({q(table)})").fetchall()


def column_names(con: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in columns(con, table)]


def row_count(con: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(con, table):
        return None
    return int(con.execute(f"SELECT COUNT(*) AS n FROM {q(table)}").fetchone()["n"])


def print_rows(rows: Iterable[sqlite3.Row], limit: int = 50) -> None:
    count = 0
    for row in rows:
        print(dict(row))
        count += 1
        if count >= limit:
            break
    if count == 0:
        print("(no rows)")


def value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def money(amount: Any, currency: Any) -> str:
    try:
        return f"{float(amount):.2f} {currency or 'UAH'}"
    except Exception:
        return f"{amount or '—'} {currency or ''}".strip()


def service_title(row: sqlite3.Row) -> str:
    for key in ("service_item_code", "base_service_code", "service_type"):
        code = value(row, key)
        if code in SERVICE_NAMES:
            return SERVICE_NAMES[code]
    return str(value(row, "service_item_code") or value(row, "base_service_code") or value(row, "service_type") or "—")


def split_comment(comment: str) -> list[str]:
    if not comment:
        return []
    text = str(comment).replace("; ", ". ")
    return [p.strip().strip(".") for p in text.split(". ") if p.strip().strip(".")]


def payment_card(row: sqlite3.Row, index: int | None = None) -> None:
    pid = value(row, "id", "—")
    title = f"Оплата #{pid}"
    if index is not None:
        title = f"{index}. {title}"

    print()
    print("┌" + "─" * 72)
    print(f"│ {title}")
    print("├" + "─" * 72)
    print(f"│ Дата:      {value(row, 'created_at') or value(row, 'payment_date') or '—'}")
    print(f"│ Квартира:  {value(row, 'apartment_number', '—')}")
    print(f"│ Сумма:     {money(value(row, 'amount'), value(row, 'currency', 'UAH'))}")
    print(f"│ Касса:     {value(row, 'cashbox_code', '—')}")
    print(f"│ Кассир:    {value(row, 'operator_id') or value(row, 'created_by') or '—'}")
    print(f"│ Метод:     {value(row, 'payment_method') or value(row, 'payment_channel') or '—'}")
    print(f"│ Статус:    {value(row, 'cashier_entry_status') or value(row, 'status') or '—'}")
    if value(row, "cashier_receipt_id") is not None:
        print(f"│ Чек:       {value(row, 'cashier_receipt_id')}")
    if value(row, "payment_notice_id") is not None:
        print(f"│ Уведомл.:  {value(row, 'payment_notice_id')}")
    print("├" + "─" * 72)
    print(f"│ Услуга:    {service_title(row)}")
    print(f"│ Основание: {value(row, 'source_ref') or value(row, 'source') or '—'}")
    parts = split_comment(str(value(row, "comment") or ""))
    if parts:
        print("├" + "─" * 72)
        print("│ Комментарий:")
        for part in parts[:8]:
            print(f"│   - {part}")
        if len(parts) > 8:
            print(f"│   ... ещё {len(parts) - 8}")
    print("└" + "─" * 72)


def payment_telegram(row: sqlite3.Row) -> None:
    print(f"💳 Оплата #{value(row, 'id', '—')}")
    print(f"{value(row, 'apartment_number', '—')} · {money(value(row, 'amount'), value(row, 'currency', 'UAH'))} · касса {value(row, 'cashbox_code', '—')}")
    print(service_title(row))
    print(str(value(row, "cashier_entry_status") or value(row, "status") or "—"))


def list_tables(con: sqlite3.Connection, pattern: str | None = None) -> None:
    names = table_names(con)
    if pattern:
        names = [n for n in names if pattern.lower() in n.lower()]
    for name in names:
        print(name)


def show_schema(con: sqlite3.Connection, table: str) -> None:
    if not table_exists(con, table):
        print(f"Table not found: {table}")
        return
    header(f"Schema: {table}")
    for col in columns(con, table):
        print(dict(col))


def show_admins(con: sqlite3.Connection) -> None:
    header("OSBB admins")
    if table_exists(con, "bot_admins"):
        print_rows(con.execute("SELECT * FROM bot_admins ORDER BY id").fetchall())
    else:
        print("Table bot_admins not found.")


def matching_table_names(con: sqlite3.Connection, keywords: list[str]) -> list[str]:
    return [n for n in table_names(con) if any(k in n.lower() for k in keywords)]


def matching_tables(con: sqlite3.Connection, title: str, keywords: list[str]) -> None:
    header(title)
    found = matching_table_names(con, keywords)
    if not found:
        print("(no matching tables)")
        return
    for name in found:
        print(f"{name:45s} rows={row_count(con, name)}")


def payment_rows(con: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    cols = column_names(con, "payments")
    order_col = "id" if "id" in cols else cols[0]
    return con.execute(f"SELECT * FROM payments ORDER BY {q(order_col)} DESC LIMIT ?", (limit,)).fetchall()


def scalar(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = con.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def show_cashier_summary(con: sqlite3.Connection) -> None:
    header("Cashier / payments summary")
    tables = matching_table_names(con, ["cash", "payment", "receipt"])
    print("Tables:")
    for name in tables:
        print(f"  {name:45s} rows={row_count(con, name)}")
    if not tables:
        print("  (no matching tables)")

    print()
    print("Compatibility:")
    if table_exists(con, "payments"):
        cols = column_names(con, "payments")
        for needed in ["source_ref", "source_type", "amount", "created_at"]:
            print(f"  payments.{needed:20s} {'YES' if needed in cols else 'NO'}")
    else:
        print("  payments table missing")
        return

    total_count = scalar(con, "SELECT COUNT(*) FROM payments")
    total_amount = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM payments")
    confirmed_count = scalar(con, "SELECT COUNT(*) FROM payments WHERE COALESCE(cashier_entry_status, '') = 'CONFIRMED'")
    confirmed_amount = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE COALESCE(cashier_entry_status, '') = 'CONFIRMED'")

    print()
    print("Totals:")
    print(f"  Payments total:       {total_count}")
    print(f"  Amount total:         {money(total_amount, 'UAH')}")
    print(f"  Confirmed payments:   {confirmed_count}")
    print(f"  Confirmed amount:     {money(confirmed_amount, 'UAH')}")

    rows = payment_rows(con, 1)
    if rows:
        r = rows[0]
        print()
        print("Latest payment:")
        print(f"  #{value(r, 'id')} · apt {value(r, 'apartment_number', '—')} · {money(value(r, 'amount'), value(r, 'currency', 'UAH'))} · {value(r, 'cashier_entry_status') or value(r, 'status') or '—'}")
        print(f"  {service_title(r)}")
        print(f"  source_ref: {value(r, 'source_ref') or value(r, 'source') or '—'}")

    print()
    print("For details:")
    print("  python .\OSBB\tools\db_inspector.py payments --limit 3")
    print("  python .\OSBB\tools\db_inspector.py payments --telegram --limit 5")
    print("  python .\OSBB\tools\db_inspector.py cashier --raw --limit 1")


def show_payments(con: sqlite3.Connection, raw: bool = False, telegram: bool = False, limit: int = 10) -> None:
    if not table_exists(con, "payments"):
        print("Table payments not found.")
        return
    rows = payment_rows(con, limit)
    if raw:
        print_rows(rows, limit)
        return
    for i, row in enumerate(rows, 1):
        if telegram:
            payment_telegram(row)
            print()
        else:
            payment_card(row, i)


def show_cashier(con: sqlite3.Connection, raw: bool = False, telegram: bool = False, limit: int = 10) -> None:
    if raw or telegram:
        if table_exists(con, "payments"):
            show_payments(con, raw=raw, telegram=telegram, limit=limit)
        else:
            print("Table payments not found.")
        return
    show_cashier_summary(con)


def show_summary(con: sqlite3.Connection, db_path: Path) -> None:
    header("OSBB DATABASE INSPECTOR")
    print("Database:", db_path)
    print("Size KB :", round(db_path.stat().st_size / 1024, 1))
    section("Core table counts")
    for table in ["bot_admins", "payments", "service_orders", "service_order_steps", "service_order_payment_links", "remote_assets", "remote_requests"]:
        n = row_count(con, table)
        if n is not None:
            print(f"{table:35s} {n}")


def show_generic(con: sqlite3.Connection, title: str, keywords: list[str]) -> None:
    matching_tables(con, title, keywords)


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect OSBB SQLite database.")
    ap.add_argument("command", choices=["summary", "tables", "schema", "admins", "roles", "cashier", "payments", "service-orders", "remotes", "vehicles", "search"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    con = connect(db_path)
    try:
        if args.command == "summary":
            show_summary(con, db_path)
        elif args.command == "tables":
            list_tables(con)
        elif args.command == "schema":
            if not args.arg:
                raise SystemExit("schema requires table name")
            show_schema(con, args.arg)
        elif args.command == "admins":
            show_admins(con)
        elif args.command == "roles":
            show_generic(con, "Role / access tables", ["admin", "role", "permission", "guard", "operator", "access"])
        elif args.command == "cashier":
            show_cashier(con, raw=args.raw, telegram=args.telegram, limit=args.limit)
        elif args.command == "payments":
            show_payments(con, raw=args.raw, telegram=args.telegram, limit=args.limit)
        elif args.command == "service-orders":
            show_generic(con, "Service order tables", ["service_order", "remote_asset", "remote_request"])
        elif args.command == "remotes":
            show_generic(con, "Remote / access tables", ["remote", "barrier", "access"])
        elif args.command == "vehicles":
            show_generic(con, "Vehicle / parking tables", ["vehicle", "car", "parking", "auto"])
        elif args.command == "search":
            if not args.arg:
                raise SystemExit("search requires term")
            list_tables(con, args.arg)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
