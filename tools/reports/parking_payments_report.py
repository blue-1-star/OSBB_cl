#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
OSBB parking payments report.

Purpose:
    Create a guard/admin-friendly Excel report from confirmed parking payments.

This is not a Telegram handler.
This is the first operational reporting module for the future web/admin layer.

Typical run from G:\Programming\Py:

    python .\OSBB\tools\reports\parking_payments_report.py --include-all

With known tariff amounts:

    python .\OSBB\tools\reports\parking_payments_report.py --include-all --night-amount 400 --day-amount 200
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Recovered"

NIGHT_RE = re.compile(r"(ноч|ніч|night|нічн|ночн)", re.IGNORECASE)
DAY_RE = re.compile(r"(днев|денн|день|денний|day)", re.IGNORECASE)
PARKING_RE = re.compile(r"(парков|парку|parking|стоян|машином|авто|vehicle)", re.IGNORECASE)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def table_names(con: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def column_names(con: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in con.execute(f"PRAGMA table_info({q(table)})")
    ]


def first_col(cols: list[str], names: list[str]) -> str | None:
    lower = {col.lower(): col for col in cols}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return row[key] if key in row.keys() else default


def payment_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    if not table_exists(con, "payments"):
        raise RuntimeError("Table payments not found.")

    cols = column_names(con, "payments")
    order_col = "id" if "id" in cols else cols[0]
    return con.execute(f"SELECT * FROM payments ORDER BY {q(order_col)} DESC").fetchall()


def apartment_number_from_id_map(con: sqlite3.Connection) -> dict[str, str]:
    result: dict[str, str] = {}

    for table in table_names(con):
        cols = column_names(con, table)
        table_low = table.lower()

        if not any(token in table_low for token in ["apartment", "unit", "premise"]):
            continue

        id_col = first_col(cols, ["id", "apartment_id", "unit_id", "premise_id"])
        apt_col = first_col(cols, [
            "apartment_number",
            "unit_number",
            "premise_code",
            "unit_code",
            "number",
            "apartment",
        ])

        if not id_col or not apt_col:
            continue

        try:
            rows = con.execute(
                f"SELECT {q(id_col)} AS id_value, {q(apt_col)} AS apartment FROM {q(table)}"
            ).fetchall()
        except sqlite3.Error:
            continue

        for row in rows:
            if row["id_value"] is not None and row["apartment"]:
                result[str(row["id_value"])] = str(row["apartment"]).strip()

    return result


def vehicle_rows_by_apartment(con: sqlite3.Connection) -> tuple[dict[str, list[dict[str, str]]], str]:
    """
    Returns:
        apartment -> [{plate, parking_time, source_table}]
    """
    apartment_id_map = apartment_number_from_id_map(con)
    result: dict[str, list[dict[str, str]]] = {}
    used_sources: set[str] = set()

    plate_candidates = [
        "plate",
        "vehicle_plate",
        "plate_number",
        "license_plate",
        "car_number",
        "vehicle_number",
        "state_number",
        "number",
    ]
    apartment_candidates = [
        "apartment_number",
        "apartment",
        "unit_number",
        "premise_code",
        "unit_code",
    ]
    apartment_id_candidates = ["apartment_id", "unit_id", "premise_id"]
    parking_candidates = ["parking_time", "parking_mode", "parking_type"]

    for table in table_names(con):
        cols = column_names(con, table)
        table_low = table.lower()

        plate_col = first_col(cols, plate_candidates)
        apt_col = first_col(cols, apartment_candidates)
        apt_id_col = first_col(cols, apartment_id_candidates)
        parking_col = first_col(cols, parking_candidates)

        has_vehicle_hint = any(token in table_low for token in ["vehicle", "car", "auto", "parking"])
        if not (has_vehicle_hint or plate_col or parking_col):
            continue
        if not (apt_col or apt_id_col):
            continue

        select_parts = [
            f"{q(apt_col)} AS apartment" if apt_col else "NULL AS apartment",
            f"{q(apt_id_col)} AS apartment_id" if apt_id_col else "NULL AS apartment_id",
            f"{q(plate_col)} AS plate" if plate_col else "NULL AS plate",
            f"{q(parking_col)} AS parking_time" if parking_col else "NULL AS parking_time",
        ]

        try:
            rows = con.execute(f"SELECT {', '.join(select_parts)} FROM {q(table)}").fetchall()
        except sqlite3.Error:
            continue

        table_used = False
        for row in rows:
            apartment = str(row["apartment"] or "").strip()
            if not apartment and row["apartment_id"] is not None:
                apartment = apartment_id_map.get(str(row["apartment_id"]), "")

            if not apartment:
                continue

            result.setdefault(apartment, []).append(
                {
                    "plate": str(row["plate"] or "").strip(),
                    "parking_time": str(row["parking_time"] or "").strip(),
                    "source_table": table,
                }
            )
            table_used = True

        if table_used:
            used_sources.add(table)

    source = ", ".join(sorted(used_sources)) if used_sources else "NOT FOUND"
    return result, source


def payment_text(row: sqlite3.Row) -> str:
    return " ".join(
        str(value(row, key) or "")
        for key in [
            "comment",
            "service_item_code",
            "base_service_code",
            "service_type",
            "source",
            "source_ref",
        ]
    )


def is_parking_payment(row: sqlite3.Row, include_all: bool) -> bool:
    if include_all:
        return True
    return bool(PARKING_RE.search(payment_text(row)))


def classify_parking_payment(
    row: sqlite3.Row,
    night_amount: float | None,
    day_amount: float | None,
) -> tuple[str, str]:
    text = payment_text(row)

    try:
        amount = float(value(row, "amount"))
    except Exception:
        amount = None

    if NIGHT_RE.search(text):
        return "night", "text marker"
    if DAY_RE.search(text):
        return "day", "text marker"
    if night_amount is not None and amount == night_amount:
        return "night", f"amount={night_amount}"
    if day_amount is not None and amount == day_amount:
        return "day", f"amount={day_amount}"

    return "undefined", "no marker"


def build_report_rows(
    con: sqlite3.Connection,
    include_all: bool,
    night_amount: float | None,
    day_amount: float | None,
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    vehicles_by_apartment, vehicle_source = vehicle_rows_by_apartment(con)

    grouped: dict[str, list[dict[str, Any]]] = {
        "night": [],
        "day": [],
        "undefined": [],
        "review_needed": [],
        "internal_all": [],
    }

    for payment in payment_rows(con):
        if not is_parking_payment(payment, include_all):
            continue

        apartment = str(value(payment, "apartment_number") or "").strip()
        mode, reason = classify_parking_payment(payment, night_amount, day_amount)

        vehicle_items = vehicles_by_apartment.get(apartment, [])
        plates = [item["plate"] for item in vehicle_items if item["plate"]]
        parking_values = [item["parking_time"] for item in vehicle_items if item["parking_time"]]

        if not vehicle_items:
            missing_parking_time = "UNKNOWN_NO_VEHICLE_MATCH"
        elif not parking_values:
            missing_parking_time = "YES"
        else:
            missing_parking_time = "NO"

        conflict = "NO"
        if parking_values and mode != "undefined":
            joined = " ".join(parking_values).lower()
            if mode not in joined:
                conflict = "YES"

        public_row = {
            "apartment": apartment,
            "plate": ", ".join(plates),
            "amount": value(payment, "amount"),
            "inferred_parking_time": mode,
        }

        internal_row = {
            **public_row,
            "payment_id": value(payment, "id"),
            "payment_date": value(payment, "created_at") or value(payment, "payment_date"),
            "currency": value(payment, "currency", "UAH"),
            "status": value(payment, "cashier_entry_status") or value(payment, "status"),
            "source_ref": value(payment, "source_ref") or value(payment, "source"),
            "inference_reason": reason,
            "existing_parking_time": ", ".join(parking_values),
            "missing_parking_time": missing_parking_time,
            "conflict": conflict,
            "comment": value(payment, "comment"),
        }

        grouped[mode].append(public_row)
        grouped["internal_all"].append(internal_row)

        if missing_parking_time != "NO" or conflict == "YES":
            grouped["review_needed"].append(internal_row)

    return grouped, vehicle_source


def xlsx_col(n: int) -> str:
    result = ""
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def safe_sheet_name(name: str) -> str:
    bad = set('[]:*?/\\')
    cleaned = "".join("_" if ch in bad else ch for ch in name)
    return cleaned[:31] or "Sheet"


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    sheet_names = list(sheets.keys())

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheet_names) + 1)
            )
            + "</Types>",
        )

        zip_file.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )

        workbook_sheets = "".join(
            f'<sheet name="{escape(safe_sheet_name(name))}" sheetId="{idx}" r:id="rId{idx}"/>'
            for idx, name in enumerate(sheet_names, 1)
        )
        zip_file.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets>"
            "</workbook>",
        )

        relationships = "".join(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
            for idx in range(1, len(sheet_names) + 1)
        )
        zip_file.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )

        for idx, name in enumerate(sheet_names, 1):
            rows = sheets[name]
            headers = list(rows[0].keys()) if rows else ["message"]
            data = rows if rows else [{"message": "No rows"}]

            xml_rows = []
            all_rows = [dict.fromkeys(headers, None)] + data
            for row_index, row in enumerate(all_rows, 1):
                cells = []
                for col_index, header in enumerate(headers, 1):
                    val = header if row_index == 1 else row.get(header, "")
                    ref = f"{xlsx_col(col_index)}{row_index}"

                    if isinstance(val, (int, float)) and val is not None and not isinstance(val, bool):
                        cells.append(f'<c r="{ref}"><v>{val}</v></c>')
                    else:
                        cells.append(
                            f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val or ""))}</t></is></c>'
                        )

                xml_rows.append(f'<row r="{row_index}">' + "".join(cells) + "</row>")

            zip_file.writestr(
                f"xl/worksheets/sheet{idx}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                '<sheetData>'
                + "".join(xml_rows)
                + "</sheetData></worksheet>",
            )


def build_workbook_sheets(grouped: dict[str, list[dict[str, Any]]], vehicle_source: str) -> dict[str, list[dict[str, Any]]]:
    summary = [
        {"metric": "night", "value": len(grouped["night"])},
        {"metric": "day", "value": len(grouped["day"])},
        {"metric": "undefined", "value": len(grouped["undefined"])},
        {"metric": "review_needed", "value": len(grouped["review_needed"])},
        {"metric": "vehicle_source", "value": vehicle_source},
        {
            "metric": "inferred_parking_time",
            "value": "calculated from payment text or tariff amount: night/day/undefined",
        },
    ]

    return {
        "Summary": summary,
        "Night_Public": grouped["night"],
        "Day_Public": grouped["day"],
        "Undefined_Public": grouped["undefined"],
        "Review_Internal": grouped["review_needed"],
        "All_Internal": grouped["internal_all"],
    }


def run_report(args: argparse.Namespace) -> Path:
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT.parent / db_path).resolve() if str(db_path).startswith("OSBB") else (PROJECT_ROOT / db_path).resolve()

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    con = connect(db_path)
    try:
        grouped, vehicle_source = build_report_rows(
            con,
            include_all=args.include_all,
            night_amount=args.night_amount,
            day_amount=args.day_amount,
        )
    finally:
        con.close()

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR / f"PARKING_PAYMENTS_REPORT_{stamp}.xlsx"
    if not output.is_absolute():
        output = (PROJECT_ROOT.parent / output).resolve() if str(output).startswith("OSBB") else (PROJECT_ROOT / output).resolve()

    write_xlsx(output, build_workbook_sheets(grouped, vehicle_source))

    print("=" * 90)
    print("OSBB Parking Payments Report")
    print("=" * 90)
    print("Vehicle source:", vehicle_source)
    print("Night:", len(grouped["night"]))
    print("Day:", len(grouped["day"]))
    print("Undefined:", len(grouped["undefined"]))
    print("Review needed:", len(grouped["review_needed"]))
    print()
    print("Excel:", output)
    print()
    print("Public sheets contain only:")
    print("  apartment, plate, amount, inferred_parking_time")
    print()
    print("Internal sheets contain diagnostic columns for admin/operator review.")

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Create OSBB parking payments Excel report.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--output", default=None, help="Excel output path")
    parser.add_argument("--include-all", action="store_true", help="include all payments, even without parking text markers")
    parser.add_argument("--night-amount", type=float, default=None, help="tariff amount that means night parking")
    parser.add_argument("--day-amount", type=float, default=None, help="tariff amount that means day parking")

    args = parser.parse_args()
    run_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
