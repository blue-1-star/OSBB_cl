#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_inspector.py v5

Changes:
- `cashier` remains short summary.
- `parking-payments-report` now creates a public-friendly Excel report.
- Public sheets contain only compact columns:
    apartment, plate, amount, inferred_parking_time
- Internal sheet keeps diagnostic columns for review.
- Fixes the old bug where `missing_parking_time` was YES for everything by using
  broader vehicle table detection and by reporting the vehicle source used.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "Recovered"

NIGHT_RE = re.compile(r"(ноч|ніч|night|нічн|ночн)", re.I)
DAY_RE = re.compile(r"(днев|денн|день|денний|day)", re.I)
PARKING_RE = re.compile(r"(парков|парку|parking|стоян|машином|авто|vehicle)", re.I)

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
    return [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


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


def value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return row[key] if key in row.keys() else default


def first_col(cols: list[str], names: list[str]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


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


def payment_rows(con: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    if not table_exists(con, "payments"):
        return []
    cols = column_names(con, "payments")
    order_col = "id" if "id" in cols else cols[0]
    sql = f"SELECT * FROM payments ORDER BY {q(order_col)} DESC"
    if limit is not None:
        sql += " LIMIT ?"
        return con.execute(sql, (limit,)).fetchall()
    return con.execute(sql).fetchall()


def scalar(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = con.execute(sql, params).fetchone()
    return None if row is None else row[0]


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
    if not table_exists(con, "payments"):
        print("  payments table missing")
        return
    cols = column_names(con, "payments")
    for needed in ["source_ref", "source_type", "amount", "created_at"]:
        print(f"  payments.{needed:20s} {'YES' if needed in cols else 'NO'}")

    print()
    print("Totals:")
    print(f"  Payments total:       {scalar(con, 'SELECT COUNT(*) FROM payments')}")
    print(f"  Amount total:         {money(scalar(con, 'SELECT COALESCE(SUM(amount), 0) FROM payments'), 'UAH')}")
    print(f"  Confirmed payments:   {scalar(con, \"SELECT COUNT(*) FROM payments WHERE COALESCE(cashier_entry_status, '') = 'CONFIRMED'\")}")
    print(f"  Confirmed amount:     {money(scalar(con, \"SELECT COALESCE(SUM(amount), 0) FROM payments WHERE COALESCE(cashier_entry_status, '') = 'CONFIRMED'\"), 'UAH')}")

    rows = payment_rows(con, 1)
    if rows:
        r = rows[0]
        print()
        print("Latest payment:")
        print(f"  #{value(r, 'id')} · apt {value(r, 'apartment_number', '—')} · {money(value(r, 'amount'), value(r, 'currency', 'UAH'))} · {value(r, 'cashier_entry_status') or value(r, 'status') or '—'}")
        print(f"  {service_title(r)}")
        print(f"  source_ref: {value(r, 'source_ref') or value(r, 'source') or '—'}")


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
    print(f"│ Статус:    {value(row, 'cashier_entry_status') or value(row, 'status') or '—'}")
    print("├" + "─" * 72)
    print(f"│ Услуга:    {service_title(row)}")
    print(f"│ Основание: {value(row, 'source_ref') or value(row, 'source') or '—'}")
    print("└" + "─" * 72)


def show_payments(con: sqlite3.Connection, raw: bool = False, telegram: bool = False, limit: int = 10) -> None:
    rows = payment_rows(con, limit)
    if raw:
        print_rows(rows, limit)
        return
    for i, row in enumerate(rows, 1):
        if telegram:
            print(f"💳 Оплата #{value(row, 'id', '—')}")
            print(f"{value(row, 'apartment_number', '—')} · {money(value(row, 'amount'), value(row, 'currency', 'UAH'))} · касса {value(row, 'cashbox_code', '—')}")
            print(service_title(row))
            print(str(value(row, "cashier_entry_status") or value(row, "status") or "—"))
            print()
        else:
            payment_card(row, i)


def show_cashier(con: sqlite3.Connection, raw: bool = False, telegram: bool = False, limit: int = 10) -> None:
    if raw or telegram:
        show_payments(con, raw=raw, telegram=telegram, limit=limit)
    else:
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


def classify_parking_payment(row: sqlite3.Row, night_amount: float | None, day_amount: float | None) -> tuple[str, str]:
    text = " ".join(str(value(row, k) or "") for k in ["comment", "service_item_code", "base_service_code", "service_type", "source", "source_ref"])
    try:
        amt = float(value(row, "amount"))
    except Exception:
        amt = None
    if NIGHT_RE.search(text):
        return "night", "text marker"
    if DAY_RE.search(text):
        return "day", "text marker"
    if night_amount is not None and amt == night_amount:
        return "night", f"amount={night_amount}"
    if day_amount is not None and amt == day_amount:
        return "day", f"amount={day_amount}"
    return "undefined", "no marker"


def is_parking_payment(row: sqlite3.Row, include_all: bool) -> bool:
    if include_all:
        return True
    text = " ".join(str(value(row, k) or "") for k in ["comment", "service_item_code", "base_service_code", "service_type", "source", "source_ref"])
    return bool(PARKING_RE.search(text))


def apartment_number_from_id_map(con: sqlite3.Connection) -> dict[str, str]:
    result: dict[str, str] = {}
    for table in table_names(con):
        cols = column_names(con, table)
        id_col = first_col(cols, ["id", "apartment_id", "unit_id"])
        apt_col = first_col(cols, ["apartment_number", "unit_number", "premise_code", "unit_code", "number", "apartment"])
        if id_col and apt_col and any(k in table.lower() for k in ["apartment", "unit", "premise"]):
            try:
                for r in con.execute(f"SELECT {q(id_col)} AS idv, {q(apt_col)} AS apt FROM {q(table)}"):
                    if r["idv"] is not None and r["apt"]:
                        result[str(r["idv"])] = str(r["apt"]).strip()
            except Exception:
                pass
    return result


def vehicle_rows_by_apartment(con: sqlite3.Connection) -> tuple[dict[str, list[dict[str, str]]], str]:
    apt_id_map = apartment_number_from_id_map(con)
    best: dict[str, list[dict[str, str]]] = {}
    sources: list[str] = []

    plate_names = ["plate", "vehicle_plate", "plate_number", "license_plate", "car_number", "number", "vehicle_number", "state_number"]
    apt_names = ["apartment_number", "apartment", "unit_number", "premise_code", "unit_code"]
    apt_id_names = ["apartment_id", "unit_id", "premise_id"]
    pt_names = ["parking_time", "parking_mode", "parking_type"]

    for table in table_names(con):
        cols = column_names(con, table)
        low_table = table.lower()
        has_vehicle_hint = any(k in low_table for k in ["vehicle", "car", "auto", "parking"])
        pt_col = first_col(cols, pt_names)
        plate_col = first_col(cols, plate_names)
        apt_col = first_col(cols, apt_names)
        apt_id_col = first_col(cols, apt_id_names)

        if not (has_vehicle_hint or pt_col or plate_col):
            continue
        if not (apt_col or apt_id_col):
            continue

        select_cols = []
        for alias, col in [("apt", apt_col), ("apt_id", apt_id_col), ("plate", plate_col), ("parking_time", pt_col)]:
            if col:
                select_cols.append(f"{q(col)} AS {alias}")
            else:
                select_cols.append(f"NULL AS {alias}")

        try:
            rows = con.execute(f"SELECT {', '.join(select_cols)} FROM {q(table)}").fetchall()
        except Exception:
            continue

        used = False
        for r in rows:
            apt = str(r["apt"] or "").strip()
            if not apt and r["apt_id"] is not None:
                apt = apt_id_map.get(str(r["apt_id"]), "")
            if not apt:
                continue
            item = {
                "plate": str(r["plate"] or "").strip(),
                "parking_time": str(r["parking_time"] or "").strip(),
                "source_table": table,
            }
            best.setdefault(apt, []).append(item)
            used = True
        if used:
            sources.append(table)

    return best, ", ".join(sorted(set(sources))) if sources else "NOT FOUND"


def parking_report_rows(con: sqlite3.Connection, include_all: bool, night_amount: float | None, day_amount: float | None) -> tuple[dict[str, list[dict[str, Any]]], str]:
    vehicles, source = vehicle_rows_by_apartment(con)
    grouped: dict[str, list[dict[str, Any]]] = {"night": [], "day": [], "undefined": [], "review_needed": [], "internal": []}

    for row in payment_rows(con, None):
        if not is_parking_payment(row, include_all):
            continue
        mode, reason = classify_parking_payment(row, night_amount, day_amount)
        apt = str(value(row, "apartment_number") or "").strip()
        vehicle_items = vehicles.get(apt, [])
        plates = [x["plate"] for x in vehicle_items if x["plate"]]
        parking_values = [x["parking_time"] for x in vehicle_items if x["parking_time"]]

        missing = "YES" if vehicle_items and not parking_values else "NO"
        if not vehicle_items:
            missing = "UNKNOWN_NO_VEHICLE_MATCH"

        conflict = "NO"
        if parking_values and mode != "undefined":
            joined = " ".join(parking_values).lower()
            if mode not in joined:
                conflict = "YES"

        public = {
            "apartment": apt,
            "plate": ", ".join(plates),
            "amount": value(row, "amount"),
            "inferred_parking_time": mode,
        }
        internal = {
            **public,
            "payment_id": value(row, "id"),
            "payment_date": value(row, "created_at") or value(row, "payment_date"),
            "currency": value(row, "currency", "UAH"),
            "status": value(row, "cashier_entry_status") or value(row, "status"),
            "source_ref": value(row, "source_ref") or value(row, "source"),
            "inference_reason": reason,
            "existing_parking_time": ", ".join(parking_values),
            "missing_parking_time": missing,
            "conflict": conflict,
            "comment": value(row, "comment"),
        }

        grouped[mode].append(public)
        if missing != "NO" or conflict == "YES":
            grouped["review_needed"].append(internal)
        grouped["internal"].append(internal)

    return grouped, source


def xlsx_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(sheets.keys())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(f'<sheet name="{escape(name[:31])}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1)) + "</Relationships>")
        for idx, name in enumerate(names, 1):
            rows = sheets[name]
            headers = list(rows[0].keys()) if rows else ["message"]
            data = rows if rows else [{"message": "No rows"}]
            xml_rows = []
            for r_idx, row in enumerate([dict.fromkeys(headers, None)] + data, 1):
                cells = []
                for c_idx, h in enumerate(headers, 1):
                    val = h if r_idx == 1 else row.get(h, "")
                    ref = f"{xlsx_col(c_idx)}{r_idx}"
                    if isinstance(val, (int, float)) and val is not None and not isinstance(val, bool):
                        cells.append(f'<c r="{ref}"><v>{val}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val or ""))}</t></is></c>')
                xml_rows.append(f'<row r="{r_idx}">' + "".join(cells) + "</row>")
            z.writestr(f"xl/worksheets/sheet{idx}.xml", '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>")


def show_parking_payments_report(con: sqlite3.Connection, include_all: bool, night_amount: float | None, day_amount: float | None, xlsx: str | None) -> None:
    grouped, vehicle_source = parking_report_rows(con, include_all, night_amount, day_amount)
    summary = [
        {"category": "night", "count": len(grouped["night"])},
        {"category": "day", "count": len(grouped["day"])},
        {"category": "undefined", "count": len(grouped["undefined"])},
        {"category": "review_needed", "count": len(grouped["review_needed"])},
        {"category": "vehicle_source", "count": vehicle_source},
    ]
    sheets = {
        "Summary": summary,
        "Night_Public": grouped["night"],
        "Day_Public": grouped["day"],
        "Undefined_Public": grouped["undefined"],
        "Review_Internal": grouped["review_needed"],
        "All_Internal": grouped["internal"],
    }

    header("Parking payments report")
    print("Vehicle source:", vehicle_source)
    for row in summary[:4]:
        print(f"{row['category']:15s} {row['count']}")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = Path(xlsx) if xlsx else DEFAULT_REPORT_DIR / f"PARKING_PAYMENTS_REPORT_{stamp}.xlsx"
    if not path.is_absolute():
        path = (PROJECT_ROOT.parent / path).resolve()
    write_xlsx(path, sheets)
    print()
    print("Excel:", path)
    print()
    print("inferred_parking_time = inferred category from payment text or tariff amount: night/day/undefined")
    print("If vehicle source is NOT FOUND, run: python .\\OSBB\\tools\\db_inspector.py vehicles")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect OSBB SQLite database.")
    ap.add_argument("command", choices=["summary", "tables", "schema", "admins", "roles", "cashier", "payments", "service-orders", "remotes", "vehicles", "search", "parking-payments-report"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--xlsx", default=None)
    ap.add_argument("--include-all", action="store_true")
    ap.add_argument("--night-amount", type=float, default=None)
    ap.add_argument("--day-amount", type=float, default=None)
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
        elif args.command == "parking-payments-report":
            show_parking_payments_report(con, args.include_all, args.night_amount, args.day_amount, args.xlsx)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
